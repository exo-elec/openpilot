"""Vision Turn Speed Control (VTSC): the one implementation for every branch.

Comma 3-safe: no Params, cereal, GPS, numpy or actuator access of its own. Callers
provide the model arrays and get back a speed suggestion this module never applies
itself. Comfortable lateral acceleration depends on how sharp the curve is (mild /
medium / sharp buckets, 2.0 / 1.8 / 1.5 m/s^2). Two optional extras, used by EOP10
and absent on NGP10 (where the result is exactly the plain vision one):
  - `ComfortCalibration`: per-bucket comfort read from and learned into a store the
    caller supplies (EOP10: Params `TSCTargetLatAccel{Mild,Medium,Sharp}`).
  - `learned_speed`: a per-cycle callable kappa -> m/s (0 = none) that takes priority
    over the vision target (EOP10: its GPS curve-speed database, cslb). As of 2026-08-25, longitudinal_planner.py *does* clamp v_cruise to
this result while ENTERING/TURNING (exopilot/docs/EOP10_PARITY_CANDIDATES.md
Tier 1) -- so target_speed can change vehicle output once wired, the same way
BRSC's/TJA's results do. This module staying free of Params/cereal/actuator
access is what keeps it unit-testable in isolation; it is not a claim that
its output is inert once a caller applies it.
"""

from dataclasses import dataclass
from enum import Enum
from math import sqrt
import time


class CurvatureBucket(Enum):
  MILD = "mild"      # radius > 100 m
  MEDIUM = "medium"  # radius 33-100 m
  SHARP = "sharp"    # radius < 33 m


BUCKET_COMFORT = {CurvatureBucket.MILD: 2.0, CurvatureBucket.MEDIUM: 1.8, CurvatureBucket.SHARP: 1.5}
CURVATURE_RANGE = (0.001, 0.1)  # 1/m, valid curvature for the speed target


def bucket_for_curvature(kappa: float) -> CurvatureBucket:
  if kappa < 0.010:
    return CurvatureBucket.MILD
  if kappa < 0.030:
    return CurvatureBucket.MEDIUM
  return CurvatureBucket.SHARP


def _clip(x: float, lo: float, hi: float) -> float:
  return max(lo, min(hi, x))


def percentile(values, q: float) -> float:
  """numpy.percentile's default (linear interpolation), without numpy: the 97th of the predicted lateral accelerations is
  how sunnypilot's vision turn controller (where VTSC comes from) and EOP10 picked the curve to plan for."""
  v = sorted(values)
  rank = (len(v) - 1) * q / 100.0
  lo = int(rank)
  hi = min(lo + 1, len(v) - 1)
  return v[lo] + (v[hi] - v[lo]) * (rank - lo)


class ComfortCalibration:
  """Per-bucket comfortable lateral acceleration, learned from what the driver accepts in curves.

  `read(bucket) -> float | None` gives the stored value (re-read at most every 2 s until the bucket is calibrated);
  `write(bucket, value)` persists a calibrated value at most every 30 s. Each bucket learns through a slow first-order
  filter (5 min time constant at the model rate) and is trusted after 50 samples. Values stay within 1.2-2.5 m/s^2.
  """
  FILTER_TC = 300.0
  DT = 0.05            # model rate, s
  MIN_SAMPLES = 50
  MIN_LAT_ACC = 1.2
  MAX_LAT_ACC = 2.5
  WRITE_EVERY_S = 30.0
  READ_TTL_S = 2.0

  def __init__(self, read=None, write=None, seed_fallback=None, clock=time.monotonic):
    self._read, self._write, self._clock = read or (lambda b: None), write or (lambda b, v: None), clock
    self._alpha = self.DT / (self.FILTER_TC + self.DT)
    self.x: dict[CurvatureBucket, float] = {}
    self.samples = dict.fromkeys(CurvatureBucket, 0)
    self._last_write = dict.fromkeys(CurvatureBucket, 0.0)
    self._cache: dict[CurvatureBucket, tuple[float, float]] = {}
    for b in CurvatureBucket:
      stored = self._stored(b)
      if stored is None and seed_fallback is not None:
        stored = seed_fallback()
      self.x[b] = _clip(stored, self.MIN_LAT_ACC, self.MAX_LAT_ACC) if stored is not None else BUCKET_COMFORT[b]

  def _stored(self, bucket):
    try:
      v = self._read(bucket)
      return float(v) if v not in (None, b'', '') else None
    except (ValueError, TypeError):
      return None

  def comfort(self, kappa: float) -> float:
    b = bucket_for_curvature(kappa)
    if self.samples[b] >= self.MIN_SAMPLES:
      return _clip(self.x[b], self.MIN_LAT_ACC, self.MAX_LAT_ACC)
    now = self._clock()
    cached = self._cache.get(b)
    if cached is not None and now - cached[1] < self.READ_TTL_S:
      return cached[0]
    stored = self._stored(b)
    value = _clip(stored, self.MIN_LAT_ACC, self.MAX_LAT_ACC) if stored is not None else BUCKET_COMFORT[b]
    self._cache[b] = (value, now)
    return value

  def observe(self, lat_acc: float, kappa: float) -> None:
    if lat_acc < 0.5:
      return
    b = bucket_for_curvature(kappa)
    self.x[b] += self._alpha * (_clip(lat_acc, self.MIN_LAT_ACC, self.MAX_LAT_ACC) - self.x[b])
    self.samples[b] += 1
    if self.samples[b] >= self.MIN_SAMPLES:
      now = self._clock()
      if now - self._last_write[b] > self.WRITE_EVERY_S:
        self._write(b, round(self.x[b], 3))
        self._last_write[b] = now


class VTSCState(Enum):
  DISABLED = "disabled"
  ENABLED = "enabled"
  ENTERING = "entering"
  TURNING = "turning"
  LEAVING = "leaving"


@dataclass(frozen=True)
class VTSCResult:
  """One advisory update; ``target_speed`` is informational only."""
  target_speed: float | None
  state: VTSCState
  current_lat_acc: float
  predicted_lat_acc: float
  curvature: float
  using_learned: bool = False


class VTSC:
  """Vision turn-speed estimator using only v0.10.0 model outputs."""

  MIN_VELOCITY = 5.0
  ENTERING_LAT_ACC = 1.3
  TURNING_LAT_ACC = 1.6
  LEAVING_LAT_ACC = 1.3
  ENABLED_LAT_ACC = 1.1

  def __init__(self, enabled=True, comfort: ComfortCalibration | None = None):
    self.enabled = enabled
    self.comfort = comfort
    self.state = VTSCState.DISABLED

  @staticmethod
  def _field(model, name):
    if model is None:
      return []
    if isinstance(model, dict):
      value = model.get(name)
    else:
      value = model
      for part in name.split("."):
        value = getattr(value, part, None)
    return [] if value is None else value  # capnp list, list or numpy array (no truth test: arrays have none)

  @classmethod
  def _lateral_acceleration(cls, v_ego, model):
    rates = list(cls._field(model, "orientationRate.z"))
    if not rates:
      rates = list(cls._field(model, "orientation_rate_z"))
    velocities = list(cls._field(model, "velocity.x"))
    if not velocities:
      velocities = list(cls._field(model, "velocity_x"))
    current = abs(float(rates[0])) * max(0.0, float(v_ego)) if rates else 0.0
    samples = [abs(float(rate)) * float(speed)
               for rate, speed in zip(rates[:10], velocities[:10], strict=False) if float(speed) > 1.0]
    if not samples:
      return current, 0.0
    return current, percentile(samples, 97.0)

  def _update_state(self, v_ego, predicted, current):
    if not self.enabled or v_ego < self.MIN_VELOCITY:
      self.state = VTSCState.DISABLED
    elif self.state is VTSCState.DISABLED:
      self.state = VTSCState.ENABLED
    elif self.state is VTSCState.ENABLED and predicted >= self.ENTERING_LAT_ACC:
      self.state = VTSCState.ENTERING
    elif self.state is VTSCState.ENTERING:
      if current >= self.TURNING_LAT_ACC:
        self.state = VTSCState.TURNING
      elif predicted < self.ENTERING_LAT_ACC:
        self.state = VTSCState.ENABLED
    elif self.state is VTSCState.TURNING and current <= self.LEAVING_LAT_ACC:
      self.state = VTSCState.LEAVING
    elif self.state is VTSCState.LEAVING and current < self.ENABLED_LAT_ACC:
      self.state = VTSCState.ENABLED

  def _speed_target(self, v_ego, predicted):
    if predicted <= 0.1 or v_ego <= 1.0:
      return None, 0.0
    curvature = _clip(predicted / (v_ego * v_ego), *CURVATURE_RANGE)
    comfort = self.comfort.comfort(curvature) if self.comfort is not None else BUCKET_COMFORT[bucket_for_curvature(curvature)]
    return sqrt(comfort / curvature), curvature

  def update(self, v_ego, model, enabled=None, learned_speed=None):
    """Return an advisory result; never writes controls. `learned_speed(kappa) -> m/s` (0 = none) wins when it has one."""
    if enabled is not None:
      self.enabled = bool(enabled)
    v_ego = max(0.0, float(v_ego))
    current, predicted = self._lateral_acceleration(v_ego, model)
    self._update_state(v_ego, predicted, current)
    target, curvature = self._speed_target(v_ego, predicted)
    using_learned = False
    if self.state not in (VTSCState.ENTERING, VTSCState.TURNING):
      target = None
    else:
      kappa = predicted / (v_ego * v_ego) if predicted > 0.1 and v_ego > 1.0 else 0.0
      if learned_speed is not None:
        learned = learned_speed(kappa)
        if learned and learned > 0:
          target, using_learned = float(learned), True
      if self.comfort is not None:
        self.comfort.observe(current, kappa)
    return VTSCResult(target, self.state, current, predicted, curvature, using_learned)

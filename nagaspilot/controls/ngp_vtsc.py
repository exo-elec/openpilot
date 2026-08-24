"""Comma 3-safe Vision Turn Speed Control (VTSC) advisory calculation.

This is the application proving slice of EOP10 VTSC. It intentionally has no
Params, cereal, GPS, or actuator integration of its own: callers provide the
model arrays and get back a speed suggestion this module never applies
itself. As of 2026-08-25, longitudinal_planner.py *does* clamp v_cruise to
this result while ENTERING/TURNING (nagaspilot/docs/EOP10_PARITY_CANDIDATES.md
Tier 1) -- so target_speed can change vehicle output once wired, the same way
BRSC's/TJA's results do. This module staying free of Params/cereal/actuator
access is what keeps it unit-testable in isolation; it is not a claim that
its output is inert once a caller applies it.
"""

from dataclasses import dataclass
from enum import Enum
from math import sqrt


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


class NGPVTSC:
  """Vision turn-speed estimator using only v0.10.0 model outputs."""

  MIN_VELOCITY = 5.0
  ENTERING_LAT_ACC = 1.3
  TURNING_LAT_ACC = 1.6
  LEAVING_LAT_ACC = 1.3
  ENABLED_LAT_ACC = 1.1

  def __init__(self, enabled=True):
    self.enabled = enabled
    self.state = VTSCState.DISABLED

  @staticmethod
  def _field(model, name):
    if model is None:
      return []
    if isinstance(model, dict):
      return model.get(name, []) or []
    value = model
    for part in name.split("."):
      value = getattr(value, part, [])
    return value or []

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
    samples.sort()
    # 97th percentile without numpy (small model horizon, comma 3 friendly).
    index = min(len(samples) - 1, int(0.97 * (len(samples) - 1) + 0.5))
    return current, samples[index]

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

  @staticmethod
  def _speed_target(v_ego, predicted):
    if predicted <= 0.1 or v_ego <= 1.0:
      return None, 0.0
    curvature = max(0.001, min(0.1, predicted / (v_ego * v_ego)))
    if curvature < 0.010:
      comfort = 2.0
    elif curvature < 0.030:
      comfort = 1.8
    else:
      comfort = 1.5
    return sqrt(comfort / curvature), curvature

  def update(self, v_ego, model, enabled=None):
    """Return an advisory result; never writes controls or parameters."""
    if enabled is not None:
      self.enabled = bool(enabled)
    v_ego = max(0.0, float(v_ego))
    current, predicted = self._lateral_acceleration(v_ego, model)
    self._update_state(v_ego, predicted, current)
    target, curvature = self._speed_target(v_ego, predicted)
    if self.state not in (VTSCState.ENTERING, VTSCState.TURNING):
      target = None
    return VTSCResult(target, self.state, current, predicted, curvature)

"""CAT — Car Adaptive Tuning: smoothed, validated steer ratio / stiffness / angle offset.

Wraps what openpilot's `paramsd` already learns (`liveParameters`) with validity gates, a first-order
smoothing filter and a confidence counter, so steering geometry only changes once it is trustworthy.
Pure policy (no Params, messaging or clock): the caller passes `now` and the live values, like the other
`nagaspilot/controls` modules. Comma-3 inputs only (`liveParameters`, `carState`).

ExoPilot extends this with a persisted seed from the last drive, a manual steer-ratio override and
per-car geometry presets (see `selfdrive/controls/lib/cat.py` on the EOP branches).
"""
from dataclasses import dataclass

from openpilot.common.filter_simple import FirstOrderFilter
from openpilot.common.realtime import DT_MDL

MIN_SPEED = 5.0  # m/s
MAX_STEER_ANGLE = 45.0  # deg
MIN_SAMPLES = 30  # valid ticks before claiming adaptive
DECAY_TIME_S = 10.0  # seconds over which valid samples decay while gated out
FILTER_TC = 8.0


@dataclass
class CATStatus:
  adaptive: bool
  confidence: float
  steer_ratio: float
  stiffness_factor: float
  angle_offset_deg: float
  base_steer_ratio: float
  samples: int
  note: str


def live_params_gated(lp, cs) -> bool:
  """True when the learner's output is trustworthy right now."""
  return bool(lp.valid and lp.posenetValid and lp.sensorValid and lp.steerRatioValid and lp.stiffnessFactorValid
              and abs(cs.steeringAngleDeg) < MAX_STEER_ANGLE and cs.vEgo > MIN_SPEED)


class NGPCAT:
  def __init__(self, base_steer_ratio: float, min_sr: float | None = None, max_sr: float | None = None,
               seed: tuple[float, float, float] | None = None, filter_tc: float = FILTER_TC, now: float = 0.0):
    self.base_sr = float(base_steer_ratio)
    self.min_sr = 0.5 * self.base_sr if min_sr is None else min_sr
    self.max_sr = 2.0 * self.base_sr if max_sr is None else max_sr
    seed_sr, seed_stiff, seed_angle = seed if seed is not None else (self.base_sr, 1.0, 0.0)
    self.sr_filter = FirstOrderFilter(seed_sr, filter_tc, DT_MDL)
    self.stiffness_filter = FirstOrderFilter(seed_stiff, filter_tc, DT_MDL)
    self.angle_filter = FirstOrderFilter(seed_angle, filter_tc, DT_MDL)
    self.valid_samples = 0.0
    self.last_update_t = now
    self.status = CATStatus(False, 0.0, self.base_sr, 1.0, 0.0, self.base_sr, 0, "init")

  def reset(self, seed: tuple[float, float, float], now: float = 0.0):
    self.sr_filter.x, self.stiffness_filter.x, self.angle_filter.x = seed
    self.valid_samples = 0.0
    self.last_update_t = now

  def decay(self, now: float, gated: bool = False) -> float:
    """Advance the confidence counter; returns confidence in [0, 1]."""
    dt = max(0.0, now - self.last_update_t)
    self.last_update_t = now
    if not gated and self.valid_samples > 0:
      # float subtraction: int() would truncate to 0 at 20-50 Hz
      self.valid_samples = max(0.0, self.valid_samples - (dt / DECAY_TIME_S) * MIN_SAMPLES)
    return min(1.0, self.valid_samples / float(MIN_SAMPLES))

  def step(self, now: float, gated: bool, steer_ratio: float, stiffness: float, angle_offset_deg: float,
           manual_sr: float | None = None) -> CATStatus:
    """One control tick with fresh `liveParameters`. `manual_sr` (optional) pins the steer ratio."""
    if gated:
      tuned_sr = self.sr_filter.update(min(max(float(steer_ratio), self.min_sr), self.max_sr))
      tuned_stiff = self.stiffness_filter.update(float(stiffness))
      tuned_angle = self.angle_filter.update(float(angle_offset_deg))
      self.valid_samples += 1
      note = "adaptive"
    else:
      tuned_sr = self.sr_filter.update(self.base_sr)
      tuned_stiff = self.stiffness_filter.update(1.0)
      tuned_angle = self.angle_filter.update(0.0)
      note = "gated"

    if manual_sr is not None:
      tuned_sr = min(max(manual_sr, self.min_sr), self.max_sr)
      confidence, adaptive, note = 1.0, True, "manual_sr"
    else:
      confidence = self.decay(now, gated)
      adaptive = confidence >= 0.5 and self.valid_samples >= MIN_SAMPLES

    self.status = CATStatus(adaptive, confidence, tuned_sr, tuned_stiff, tuned_angle, self.base_sr,
                            int(self.valid_samples), note)
    return self.status

  def adaptive_params(self) -> dict:
    """Geometry to use now: the tuned values when confident, otherwise the base values."""
    if not self.status.adaptive:
      return {"steerRatio": self.base_sr, "stiffnessFactor": 1.0, "angleOffsetDeg": 0.0}
    return {"steerRatio": float(self.status.steer_ratio), "stiffnessFactor": float(self.status.stiffness_factor),
            "angleOffsetDeg": float(self.status.angle_offset_deg)}

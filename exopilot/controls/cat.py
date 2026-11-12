"""CAT - Car Adaptive Tuning: smoothed, validated steer ratio / stiffness / angle offset for the vehicle model.

SteerRatioFilter is the pure part.

Wraps what openpilot's `paramsd` already learns (`liveParameters`) with validity gates, a first-order
smoothing filter and a confidence counter, so steering geometry only changes once it is trustworthy.
Pure policy (no Params, messaging or clock): the caller passes `now` and the live values, like the other
`exopilot/controls` modules. Comma-3 inputs only (`liveParameters`, `carState`).

CAT, below, wraps the filter with the CATEnabled switch, the manual steer-ratio override (CATManualSR), the
per-car seed saved from the last drive (CATPersist) and known-good geometry for fingerprints that share CAN IDs.
The one CAT (owner 2026-11-12: one implementation per feature, written on NGP10 first).
"""
import json
import time
from dataclasses import dataclass

from openpilot.common.filter_simple import FirstOrderFilter
from openpilot.common.params import Params
from openpilot.common.realtime import DT_MDL
from openpilot.common.swaglog import cloudlog

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


class SteerRatioFilter:
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


class CAT:
  """
  Car Adaptive Tuning — smoothed steer-ratio / stiffness override.

  The smoothing, gates and confidence are SteerRatioFilter above. This class adds what needs Params: a
  persisted seed from the last drive, a manual steer-ratio override, per-car geometry presets and the
  CATEnabled switch.

  Call update(sm) each control loop tick.  If status.adaptive is True,
  consume get_adaptive_params() in controlsd / latcontrol.
  """

  PARAM_REFRESH_S  = 5.0
  PERSIST_MIN_CONF = 0.9
  PERSIST_MIN_SAMP = 60     # 2x MIN_SAMPLES

  def __init__(self, CP, filter_tc: float = 8.0, params=None):
    self.CP = CP
    self.params = params or Params()
    self.base_sr = float(CP.steerRatio)
    self.min_sr  = 0.5 * self.base_sr
    self.max_sr  = 2.0 * self.base_sr

    self._apply_model_overrides()

    self.core = SteerRatioFilter(self.base_sr, self.min_sr, self.max_sr, self._load_persisted_seed(), filter_tc, now=time.monotonic())

    self.last_param_t   = 0.0
    self.last_persist_t = 0.0

    self.manual_sr_enabled = False
    self.manual_sr         = self.base_sr
    self.cat_enabled       = self.params.get_bool("CATEnabled")

  @property
  def status(self) -> CATStatus:
    return self.core.status

  # ------------------------------------------------------------------
  # Initialisation helpers
  # ------------------------------------------------------------------

  def _apply_model_overrides(self):
    """Known-good geometry for fingerprints that share CAN IDs."""
    try:
      if self.CP.carFingerprint.startswith("TESLA_MODEL_"):
        self.base_sr = 12.0
        self.min_sr  = 0.5 * self.base_sr
        self.max_sr  = 2.0 * self.base_sr
    except Exception:
      pass

  def _load_persisted_seed(self) -> tuple[float, float, float]:
    """Return (steer_ratio, stiffness, angle_offset) from last persist."""
    try:
      raw = self.params.get("CATPersist")
      if not raw:
        return self.base_sr, 1.0, 0.0
      data = json.loads(raw.decode() if isinstance(raw, (bytes, bytearray)) else raw)
      if data.get("carFingerprint") != getattr(self.CP, "carFingerprint", None):
        return self.base_sr, 1.0, 0.0
      sr = float(min(max(data.get("steerRatio", self.base_sr), self.min_sr), self.max_sr))
      return sr, float(data.get("stiffnessFactor", 1.0)), float(data.get("angleOffsetDeg", 0.0))
    except Exception:
      return self.base_sr, 1.0, 0.0

  # ------------------------------------------------------------------
  # Update
  # ------------------------------------------------------------------

  def update(self, sm) -> CATStatus:
    now = time.monotonic()

    # Refresh user params every PARAM_REFRESH_S
    if now - self.last_param_t > self.PARAM_REFRESH_S:
      self.manual_sr_enabled = self.params.get_bool("CATManualSREnabled")
      try:
        val = self.params.get("CATManualSR")
        manual = float(val) if val not in (None, b"", "") else 0.0
        # 0 (the default) means "not set": the car's own ratio, never the clamp floor (half of it)
        self.manual_sr = manual if manual > 0.0 else self.base_sr
      except Exception:
        self.manual_sr = self.base_sr
      self.cat_enabled = self.params.get_bool("CATEnabled")
      self.last_param_t = now

    if not self.cat_enabled:
      return self.status

    if not (sm.updated["carState"] and sm.updated["liveParameters"]):
      self.core.decay(now)
      return self.status

    cs = sm["carState"]
    lp = sm["liveParameters"]
    status = self.core.step(now, live_params_gated(lp, cs), lp.steerRatio, lp.stiffnessFactor, lp.angleOffsetDeg,
                            manual_sr=self.manual_sr if self.manual_sr_enabled else None)

    # Persist after enough stable samples
    if (status.adaptive and not self.manual_sr_enabled
        and self.core.valid_samples >= self.PERSIST_MIN_SAMP
        and status.confidence >= self.PERSIST_MIN_CONF
        and now - self.last_persist_t > self.PARAM_REFRESH_S):
      self._persist(status.steer_ratio, status.stiffness_factor, status.angle_offset_deg)
      self.last_persist_t = now

    return status

  def get_adaptive_params(self) -> dict:
    """Return geometry overrides. Falls back to base values when not confident."""
    return self.core.adaptive_params()

  def reset(self):
    self._apply_model_overrides()
    self.core.base_sr, self.core.min_sr, self.core.max_sr = self.base_sr, self.min_sr, self.max_sr
    self.core.reset(self._load_persisted_seed(), now=time.monotonic())
    self.last_param_t   = 0.0
    self.last_persist_t = 0.0

  # ------------------------------------------------------------------
  # Internal helpers
  # ------------------------------------------------------------------

  def _persist(self, sr: float, stiffness: float, angle: float):
    try:
      payload = {
        "carFingerprint": getattr(self.CP, "carFingerprint", ""),
        "steerRatio":      float(sr),
        "stiffnessFactor": float(stiffness),
        "angleOffsetDeg":  float(angle),
        "ts":              time.monotonic(),
      }
      self.params.put_nonblocking("CATPersist", json.dumps(payload))
    except Exception as e:
      cloudlog.exception(f"CAT persist failed: {e}")

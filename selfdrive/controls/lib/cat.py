"""
cat.py - Car Adaptive Tuning (CAT)

Wraps the openpilot liveParameters Kalman learner with validation gates,
smoothing, and persistence so downstream controllers can trust the learned
steer ratio and stiffness factor values.

- Input:  liveParameters + carState from SubMaster
- Filter: FirstOrderFilter on steerRatio, stiffnessFactor, angleOffsetDeg
- Gates:  speed > 5 m/s, |steerAngle| < 45°, sensors valid
- Output: CATStatus with confidence and adaptive geometry params

Reference: NagasPilot np_cat_controller.py
"""

from __future__ import annotations

import json
import time
from dataclasses import dataclass

from openpilot.common.filter_simple import FirstOrderFilter
from openpilot.common.params import Params
from openpilot.common.realtime import DT_MDL
from openpilot.common.swaglog import cloudlog


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


class CAT:
  """
  Car Adaptive Tuning — smoothed steer-ratio / stiffness override.

  Call update(sm) each control loop tick.  If status.adaptive is True,
  consume get_adaptive_params() in controlsd / latcontrol.
  """

  MIN_SPEED        = 5.0    # m/s
  MAX_STEER_ANGLE  = 45.0   # deg
  MIN_SAMPLES      = 30     # valid ticks before claiming adaptive
  DECAY_TIME_S     = 10.0   # seconds to decay valid_samples when gated
  PARAM_REFRESH_S  = 5.0
  PERSIST_MIN_CONF = 0.9
  PERSIST_MIN_SAMP = 60     # 2× MIN_SAMPLES

  def __init__(self, CP, filter_tc: float = 8.0):
    self.CP = CP
    self.params = Params()
    self.base_sr = float(CP.steerRatio)
    self.min_sr  = 0.5 * self.base_sr
    self.max_sr  = 2.0 * self.base_sr

    self._apply_model_overrides()

    seed_sr, seed_stiff, seed_angle = self._load_persisted_seed()

    self.sr_filter       = FirstOrderFilter(seed_sr,    filter_tc, DT_MDL)
    self.stiffness_filter = FirstOrderFilter(seed_stiff, filter_tc, DT_MDL)
    self.angle_filter    = FirstOrderFilter(seed_angle,  filter_tc, DT_MDL)

    self.samples        = 0
    self.valid_samples  = 0
    self.last_update_t  = time.monotonic()
    self.last_param_t   = 0.0
    self.last_persist_t = 0.0

    self.manual_sr_enabled = False
    self.manual_sr         = self.base_sr
    self.cat_enabled       = self.params.get_bool("EOPCATEnabled")

    self.status = CATStatus(
      adaptive=False, confidence=0.0,
      steer_ratio=self.base_sr, stiffness_factor=1.0,
      angle_offset_deg=0.0, base_steer_ratio=self.base_sr,
      samples=0, note="init",
    )

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
      raw = self.params.get("EOPCATPersist")
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
      self.manual_sr_enabled = self.params.get_bool("EOPCATManualSREnabled")
      try:
        val = self.params.get("EOPCATManualSR")
        self.manual_sr = float(val) if val not in (None, b"", "") else self.base_sr
      except Exception:
        self.manual_sr = self.base_sr
      self.cat_enabled = self.params.get_bool("EOPCATEnabled")
      self.last_param_t = now

    if not self.cat_enabled:
      return self.status

    if not (sm.updated["carState"] and sm.updated["liveParameters"]):
      self._decay_confidence(now)
      return self.status

    cs = sm["carState"]
    lp = sm["liveParameters"]

    gated = (
      lp.valid and lp.posenetValid and lp.sensorValid
      and lp.steerRatioValid and lp.stiffnessFactorValid
      and abs(cs.steeringAngleDeg) < self.MAX_STEER_ANGLE
      and cs.vEgo > self.MIN_SPEED
    )

    if gated:
      sr_clamped = min(max(float(lp.steerRatio), self.min_sr), self.max_sr)
      tuned_sr    = self.sr_filter.update(sr_clamped)
      tuned_stiff = self.stiffness_filter.update(float(lp.stiffnessFactor))
      tuned_angle = self.angle_filter.update(float(lp.angleOffsetDeg))
      self.valid_samples += 1
      note = "adaptive"
    else:
      tuned_sr    = self.sr_filter.update(self.base_sr)
      tuned_stiff = self.stiffness_filter.update(1.0)
      tuned_angle = self.angle_filter.update(0.0)
      note = "gated"

    if self.manual_sr_enabled:
      tuned_sr    = min(max(self.manual_sr, self.min_sr), self.max_sr)
      confidence  = 1.0
      adaptive    = True
      note        = "manual_sr"
    else:
      confidence  = self._decay_confidence(now, gated)
      adaptive    = confidence >= 0.5 and self.valid_samples >= self.MIN_SAMPLES

    self.status = CATStatus(
      adaptive=adaptive, confidence=confidence,
      steer_ratio=tuned_sr, stiffness_factor=tuned_stiff,
      angle_offset_deg=tuned_angle, base_steer_ratio=self.base_sr,
      samples=int(self.valid_samples), note=note,
    )

    # Persist after enough stable samples
    if (adaptive and not self.manual_sr_enabled
        and self.valid_samples >= self.PERSIST_MIN_SAMP
        and confidence >= self.PERSIST_MIN_CONF
        and now - self.last_persist_t > self.PARAM_REFRESH_S):
      self._persist(tuned_sr, tuned_stiff, tuned_angle)
      self.last_persist_t = now

    return self.status

  def get_adaptive_params(self) -> dict:
    """Return geometry overrides. Falls back to base values when not confident."""
    if not self.status.adaptive:
      return {"steerRatio": self.base_sr, "stiffnessFactor": 1.0, "angleOffsetDeg": 0.0}
    return {
      "steerRatio":      float(self.status.steer_ratio),
      "stiffnessFactor": float(self.status.stiffness_factor),
      "angleOffsetDeg":  float(self.status.angle_offset_deg),
    }

  def reset(self):
    self._apply_model_overrides()
    seed_sr, seed_stiff, seed_angle = self._load_persisted_seed()
    self.sr_filter.x       = seed_sr
    self.stiffness_filter.x = seed_stiff
    self.angle_filter.x    = seed_angle
    self.samples        = 0
    self.valid_samples  = 0
    self.last_update_t  = time.monotonic()
    self.last_param_t   = 0.0
    self.last_persist_t = 0.0

  # ------------------------------------------------------------------
  # Internal helpers
  # ------------------------------------------------------------------

  def _decay_confidence(self, now: float, gated: bool = False) -> float:
    dt = max(0.0, now - self.last_update_t)
    self.last_update_t = now
    if not gated and self.valid_samples > 0:
      # Use float subtraction — int() would truncate to 0 at 50Hz (dt=0.05s)
      self.valid_samples = max(0.0, self.valid_samples - (dt / self.DECAY_TIME_S) * self.MIN_SAMPLES)
    return min(1.0, self.valid_samples / float(self.MIN_SAMPLES))

  def _persist(self, sr: float, stiffness: float, angle: float):
    try:
      payload = {
        "carFingerprint": getattr(self.CP, "carFingerprint", ""),
        "steerRatio":      float(sr),
        "stiffnessFactor": float(stiffness),
        "angleOffsetDeg":  float(angle),
        "ts":              time.monotonic(),
      }
      self.params.put_nonblocking("EOPCATPersist", json.dumps(payload))
    except Exception as e:
      cloudlog.exception(f"CAT persist failed: {e}")

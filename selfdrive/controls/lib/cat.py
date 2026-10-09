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

from openpilot.common.params import Params
from nagaspilot.controls.ngp_cat import CATStatus, NGPCAT, live_params_gated
from openpilot.common.swaglog import cloudlog


class CAT:
  """
  Car Adaptive Tuning — smoothed steer-ratio / stiffness override.

  The smoothing, gates and confidence live in nagaspilot/controls/ngp_cat.py (shared with NGP10). This class adds
  what ExoPilot extends it with: a persisted seed from the last drive, a manual steer-ratio override, per-car
  geometry presets and user params.

  Call update(sm) each control loop tick.  If status.adaptive is True,
  consume get_adaptive_params() in controlsd / latcontrol.
  """

  PARAM_REFRESH_S  = 5.0
  PERSIST_MIN_CONF = 0.9
  PERSIST_MIN_SAMP = 60     # 2x MIN_SAMPLES

  def __init__(self, CP, filter_tc: float = 8.0):
    self.CP = CP
    self.params = Params()
    self.base_sr = float(CP.steerRatio)
    self.min_sr  = 0.5 * self.base_sr
    self.max_sr  = 2.0 * self.base_sr

    self._apply_model_overrides()

    self.core = NGPCAT(self.base_sr, self.min_sr, self.max_sr, self._load_persisted_seed(), filter_tc, now=time.monotonic())

    self.last_param_t   = 0.0
    self.last_persist_t = 0.0

    self.manual_sr_enabled = False
    self.manual_sr         = self.base_sr
    self.cat_enabled       = self.params.get_bool("EOPCATEnabled")

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
      self.params.put_nonblocking("EOPCATPersist", json.dumps(payload))
    except Exception as e:
      cloudlog.exception(f"CAT persist failed: {e}")

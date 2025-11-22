#!/usr/bin/env python3
"""
NagasPilot Car Adaptive Tuning (CAT)

Purpose
-------
Adaptive geometry tuner that reuses the proven liveParameters learner to
stabilize steer ratio / stiffness for vehicles that share CAN fingerprints.
The controller watches the liveParameters stream, filters the learned values,
and exposes a concise status + recommended geometry that downstream planners
can apply without blocking on Params.

Design
------
- Input: liveParameters + carState from the SubMaster
- Filtering: First-order filter to smooth steer ratio / stiffness factor
- Validation: Requires healthy sensors, steady steering, and speed > 5 m/s
- Output: NpCatStatus with confidence and recommended overrides

Usage
-----
cat = NpCatController(CP)
status = cat.update(sm)
if status.adaptive:
  tuned = cat.get_adaptive_params()
  # Apply tuned["steerRatio"] / tuned["stiffnessFactor"] where needed
"""

from __future__ import annotations

import time
import json
from dataclasses import dataclass
from typing import Dict, Optional

from openpilot.common.filter_simple import FirstOrderFilter
from openpilot.common.realtime import DT_MDL
from openpilot.common.swaglog import cloudlog
from openpilot.common.params import Params


@dataclass
class NpCatStatus:
  """Lightweight status container for CAT outputs."""
  adaptive: bool
  confidence: float
  steer_ratio: float
  stiffness_factor: float
  angle_offset_deg: float
  base_steer_ratio: float
  samples: int
  note: str


class NpCatController:
  """
  Adaptive tuner for steering ratio and stiffness using liveParameters.

  This does not replace the Kalman learner; it wraps it with:
  - Validation gates (speed, steering linear region, sensor health)
  - Smoothing to avoid flicker in downstream controllers
  - Confidence scoring so callers can decide when to trust overrides
  """

  MIN_SPEED = 5.0            # m/s minimum speed to trust steering geometry
  MAX_STEER_ANGLE = 45.0     # deg linear steering region
  MIN_SAMPLES = 30           # samples before claiming adaptive readiness
  DECAY_TIME_S = 10.0        # seconds to slowly decay confidence when idle
  PARAM_KEY_STATUS = "np_cat_status"
  PARAM_KEY_PERSIST = "np_cat_persist"
  PARAM_REFRESH_S = 5.0
  PERSIST_CONFIDENCE = 0.9
  PERSIST_MIN_SAMPLES = 2 * MIN_SAMPLES

  def __init__(self, CP, params: Optional[Params] = None, filter_time_constant: float = 8.0):
    self.CP = CP
    self.params = params or Params()
    self.base_sr = float(CP.steerRatio)
    self.min_sr = 0.5 * self.base_sr
    self.max_sr = 2.0 * self.base_sr
    self._apply_model_specific_overrides()
    self.seed_sr = self.base_sr
    self.seed_stiff = 1.0
    self.seed_angle = 0.0
    self._load_persisted_seed()

    # Filters smooth noisy learned parameters
    self.sr_filter = FirstOrderFilter(self.seed_sr, filter_time_constant, DT_MDL)
    self.stiffness_filter = FirstOrderFilter(self.seed_stiff, filter_time_constant, DT_MDL)
    self.angle_filter = FirstOrderFilter(self.seed_angle, filter_time_constant, DT_MDL)

    self.samples = 0
    self.valid_samples = 0
    self.last_update_time = time.monotonic()
    self.last_param_refresh = 0.0
    self.manual_override_enabled = False
    self.manual_sr = self.base_sr
    self.last_persist_write = 0.0

    self.status = NpCatStatus(
      adaptive=False,
      confidence=0.0,
      steer_ratio=self.base_sr,
      stiffness_factor=1.0,
      angle_offset_deg=0.0,
      base_steer_ratio=self.base_sr,
      samples=0,
      note="init",
    )

    cloudlog.info("NpCatController ready: base_sr=%.3f min=%.3f max=%.3f", self.base_sr, self.min_sr, self.max_sr)

  def _apply_model_specific_overrides(self) -> None:
    """
    Apply known-good geometry defaults for specific models where fingerprints
    overlap. Example: Tesla Model 3/Y HW3 uses steerRatio ~12.0 per upstream.
    """
    try:
      if self.CP.carFingerprint.startswith("TESLA_MODEL_"):
        # Based on origin/old/spa1 tesla values: steerRatio=12.0, wheelbase ~2.875–2.89
        self.base_sr = 12.0
        self.min_sr = 0.5 * self.base_sr
        self.max_sr = 2.0 * self.base_sr
        cloudlog.info("CAT override applied for Tesla HW3 fingerprint: base_sr=%.3f", self.base_sr)
    except Exception:
      # If CP is missing fingerprint, fall back silently
      pass

  def _load_persisted_seed(self) -> None:
    """Seed filters from last good values to converge faster."""
    try:
      blob = self.params.get(self.PARAM_KEY_PERSIST) or self.params.get("NpCatPersist")
      if not blob:
        return
      import json
      data = json.loads(blob.decode("utf-8") if isinstance(blob, (bytes, bytearray)) else blob)
      if data.get("carFingerprint") != getattr(self.CP, "carFingerprint", None):
        return
      sr = float(data.get("steerRatio", self.base_sr))
      stiff = float(data.get("stiffnessFactor", 1.0))
      angle = float(data.get("angleOffsetDeg", 0.0))
      sr = min(max(sr, self.min_sr), self.max_sr)
      self.base_sr = sr
      self.seed_sr = sr
      self.seed_stiff = stiff
      self.seed_angle = angle
      cloudlog.info("CAT persisted seed applied: sr=%.3f stiff=%.3f angle=%.3f", sr, stiff, angle)
    except Exception as e:
      cloudlog.exception(f"CAT seed load failed: {e}")

  def _valid_live_parameters(self, lp) -> bool:
    return bool(
      lp.valid
      and lp.posenetValid
      and lp.sensorValid
      and lp.steerRatioValid
      and lp.stiffnessFactorValid
    )

  def _gate_conditions(self, cs) -> bool:
    return bool(abs(cs.steeringAngleDeg) < self.MAX_STEER_ANGLE and cs.vEgo > self.MIN_SPEED)

  def _update_confidence(self, gated: bool) -> float:
    now = time.monotonic()
    dt = max(0.0, now - self.last_update_time)
    self.last_update_time = now

    # Decay valid sample count when gating fails to avoid stale confidence
    if not gated and self.valid_samples > 0:
      decay = dt / self.DECAY_TIME_S
      self.valid_samples = max(0, self.valid_samples - int(decay * self.MIN_SAMPLES))

    self.samples = max(self.samples, self.valid_samples)
    return min(1.0, self.valid_samples / float(self.MIN_SAMPLES))

  def update(self, sm) -> NpCatStatus:
    """
    Process SubMaster data and return the latest status.
    Expects 'carState' and 'liveParameters' services to be present.
    """
    if not sm.updated("carState") or not sm.updated("liveParameters"):
      # No fresh data; only decay confidence
      confidence = self._update_confidence(False)
      self.status.confidence = confidence
      self.status.adaptive = confidence >= 0.5
      return self.status

    cs = sm["carState"]
    lp = sm["liveParameters"]

    now = time.monotonic()
    if now - self.last_param_refresh > self.PARAM_REFRESH_S:
      self.manual_override_enabled = self.params.get_bool("np_cat_manual_sr_enable")
      try:
        val = self.params.get("np_cat_manual_sr")
        if isinstance(val, (bytes, bytearray)):
          val = val.decode("utf-8", errors="ignore")
        self.manual_sr = float(val) if val not in (None, "") else self.base_sr
      except Exception:
        self.manual_sr = self.base_sr
      self.last_param_refresh = now

    gated = self._valid_live_parameters(lp) and self._gate_conditions(cs)
    if gated:
      # Clamp to sane range and smooth
      sr = float(lp.steerRatio)
      sr_clamped = min(max(sr, self.min_sr), self.max_sr)
      tuned_sr = self.sr_filter.update(sr_clamped)

      stiffness = float(lp.stiffnessFactor)
      tuned_stiffness = self.stiffness_filter.update(stiffness)

      angle_offset = float(lp.angleOffsetDeg)
      tuned_angle = self.angle_filter.update(angle_offset)

      self.valid_samples += 1
      note = "adaptive"
    else:
      # Keep filters biased toward base values when invalid
      tuned_sr = self.sr_filter.update(self.base_sr)
      tuned_stiffness = self.stiffness_filter.update(1.0)
      tuned_angle = self.angle_filter.update(0.0)
      note = "gated"

    if self.manual_override_enabled:
      tuned_sr = min(max(self.manual_sr, self.min_sr), self.max_sr)
      confidence = 1.0
      adaptive_ready = True
      note = "manual_sr"
    else:
      confidence = self._update_confidence(gated)
      adaptive_ready = confidence >= 0.5 and self.valid_samples >= self.MIN_SAMPLES

    self.status = NpCatStatus(
      adaptive=adaptive_ready,
      confidence=confidence,
      steer_ratio=tuned_sr,
      stiffness_factor=tuned_stiffness,
      angle_offset_deg=tuned_angle,
      base_steer_ratio=self.base_sr,
      samples=self.valid_samples,
      note=note,
    )

    if adaptive_ready and not self.manual_override_enabled and self.valid_samples >= self.PERSIST_MIN_SAMPLES and confidence >= self.PERSIST_CONFIDENCE:
      if now - self.last_persist_write > self.PARAM_REFRESH_S:
        self._persist_values(tuned_sr, tuned_stiffness, tuned_angle)
        self.last_persist_write = now
    return self.status

  def get_adaptive_params(self) -> Dict[str, float]:
    """
    Return the recommended geometry overrides. If confidence is low, fall back
    to base values so callers can safely consume this without extra guards.
    """
    if not self.status.adaptive:
      return {
        "steerRatio": self.base_sr,
        "stiffnessFactor": 1.0,
        "angleOffsetDeg": 0.0,
      }
    return {
      "steerRatio": float(self.status.steer_ratio),
      "stiffnessFactor": float(self.status.stiffness_factor),
      "angleOffsetDeg": float(self.status.angle_offset_deg),
    }

  def _persist_values(self, sr: float, stiffness: float, angle: float) -> None:
    try:
      payload = {
        "carFingerprint": getattr(self.CP, "carFingerprint", ""),
        "steerRatio": float(sr),
        "stiffnessFactor": float(stiffness),
        "angleOffsetDeg": float(angle),
        "ts": time.time(),
      }
      self.params.put_nonblocking(self.PARAM_KEY_PERSIST, json.dumps(payload))
    except Exception as e:
      cloudlog.exception(f"CAT persist failed: {e}")

  def reset(self) -> None:
    """Reset filters and counters (e.g., after a car switch)."""
    self._apply_model_specific_overrides()
    self._load_persisted_seed()
    self.sr_filter.x = self.seed_sr
    self.stiffness_filter.x = self.seed_stiff
    self.angle_filter.x = self.seed_angle
    self.samples = 0
    self.valid_samples = 0
    self.last_update_time = time.monotonic()
    self.last_param_refresh = 0.0
    self.last_persist_write = 0.0
    self.status = NpCatStatus(
      adaptive=False,
      confidence=0.0,
      steer_ratio=self.base_sr,
      stiffness_factor=1.0,
      angle_offset_deg=0.0,
      base_steer_ratio=self.base_sr,
      samples=0,
      note="reset",
    )

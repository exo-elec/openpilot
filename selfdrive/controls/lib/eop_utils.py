"""
eop_utils.py - Shared utilities for EOP control modules.

Centralizes code that was duplicated across MTSC, VTSC, CSLB, DLON, AEB.

Convention for params I/O:
  - Cache params at __init__
  - Refresh no more than once per second
  - Never read params on every frame (20-100 Hz file I/O is expensive)
"""
from __future__ import annotations

import os
from typing import cast


from openpilot.common.params import Params

# GPS tolerance — all platforms use the same standard GPS (NEO-M8U / ZED-F9P)
# RTK is VisionPilot-only. ExoPilot uses standard GPS with 50m tolerance.
GPS_TOLERANCE_M: float = 50.0


class SmoothEMA:
  """Simple exponential moving average for signal smoothing.

  Replaces over-engineered SmoothKalmanFilter (40 lines → 8 lines).
  """

  def __init__(self, alpha: float = 0.3):
    self.alpha = alpha
    self.value = 0.0
    self.confidence = 0.0
    self.initialized = False

  def update(self, measurement: float) -> float:
    if not self.initialized:
      self.value = float(measurement)
      self.initialized = True
    else:
      self.value = self.alpha * measurement + (1.0 - self.alpha) * self.value

    # Confidence: higher when measurement is close to filtered value
    if abs(measurement - self.value) < 0.1:
      self.confidence = min(1.0, self.confidence + 0.05)
    else:
      self.confidence = max(0.1, self.confidence - 0.02)

    return self.value

  def get_value(self) -> float:
    return self.value if self.initialized else 0.0

  def get_confidence(self) -> float:
    return self.confidence


def get_gps_tolerance() -> float:
  """Return GPS tolerance in meters.

  All ExoPilot platforms use standard GPS (no RTK).
  Previously had pointless per-platform mapping that all returned 50.0.
  """
  return GPS_TOLERANCE_M


def get_learned_speed(
  lat: float, lon: float, curvature: float,
  curve_learn_enabled: bool, min_speed_kph: float = 15.0,
  db_path: str | None = None,
) -> float:
  """Query learned curve speed from database.

  Unified lookup used by both MTSC and VTSC (eliminates ~40 lines of
  near-identical code between the two files).

  Args:
    lat, lon: GPS coordinates
    curvature: Road curvature (1/m)
    curve_learn_enabled: Master toggle from params
    min_speed_kph: Minimum valid speed (MTSC uses 20.0, VTSC uses 15.0)
    db_path: Optional database path override

  Returns:
    Learned speed in m/s, or 0.0 if not found / disabled
  """
  if not curve_learn_enabled or abs(curvature) < 1e-6:
    return 0.0

  try:
    radius_m = 1.0 / curvature

    # Import here to avoid circular dependency at module load time
    from openpilot.selfdrive.controls.lib.cslb import get_curve_speed

    speed_kph = get_curve_speed(lat, lon, radius_m, gps_tolerance_m=get_gps_tolerance())

    if speed_kph and min_speed_kph <= speed_kph <= 150.0:
      return speed_kph / 3.6

  except Exception:
    pass

  return 0.0


def calculate_speed_for_curvature(curvature: float, a_comfort: float, min_curvature: float = 0.001) -> float:
  """Calculate target speed for a given curvature using v = sqrt(a / kappa).

  Shared between MTSC (physics fallback) and any other curve-speed controller.

  Args:
    curvature: Road curvature in 1/m
    a_comfort: Comfortable lateral acceleration in m/s²
    min_curvature: Below this, returns inf (no limit)

  Returns:
    Target speed in m/s, or inf if curvature is too small
  """
  if curvature < min_curvature:
    return float('inf')
  return cast(float, (a_comfort / curvature) ** 0.5)


class SpeedLimitOffset:
  """Per-speed-range offset calculator (merged from FrogPilot SLC).

  Replaces global percent+fixed offset with bucket-based offsets that
  vary by posted speed limit. E.g., +5 km/h at 50 km/h, +10 at 120 km/h.
  """

  # Bucket edges in m/s: [0, 29, 49, 59, 79, 99, 119, 140] km/h
  BUCKET_EDGES_MS = [0.0, 8.1, 13.6, 16.4, 21.9, 27.5, 33.1, 38.9]

  def __init__(self, params: Params):
    self._params = params
    self._offsets = [0.0] * (len(self.BUCKET_EDGES_MS) - 1)
    self._last_update = 0.0

  def refresh(self, now: float = 0.0):
    if now - self._last_update < 2.0:
      return
    self._last_update = now
    for i in range(len(self._offsets)):
      try:
        val = self._params.get(f"EOPSLCOffset{i + 1}")
        self._offsets[i] = float(val) * (1000.0 / 3600.0) if val is not None else 0.0  # km/h -> m/s
      except (ValueError, TypeError):
        self._offsets[i] = 0.0

  def get_offset_ms(self, limit_ms: float) -> float:
    """Return offset in m/s for the given speed limit."""
    for i in range(len(self.BUCKET_EDGES_MS) - 1):
      if self.BUCKET_EDGES_MS[i] <= limit_ms < self.BUCKET_EDGES_MS[i + 1]:
        return self._offsets[i]
    return self._offsets[-1] if self._offsets else 0.0


class SpeedLimitConfirmation:
  """Require driver confirmation before applying speed limit changes.

  Merged from FrogPilot speed_limit_controller.py confirmation logic.
  """

  CONFIRMATION_TIMEOUT = 30.0  # seconds before auto-confirm
  MIN_CHANGE_MS = 2.78  # 10 km/h minimum change to trigger confirmation

  def __init__(self):
    self._confirmed_limit_ms: float | None = None
    self._unconfirmed_limit_ms: float | None = None
    self._change_time: float = 0.0
    self._confirmation_lower = False
    self._confirmation_higher = False
    self._last_param_t = 0.0

  def refresh_params(self, params: Params, now: float):
    if now - self._last_param_t < 2.0:
      return
    self._last_param_t = now
    self._confirmation_lower = params.get_bool("EOPSLCConfirmLower")
    self._confirmation_higher = params.get_bool("EOPSLCConfirmHigher")

  def update(self, new_limit_ms: float | None, driver_overriding: bool, now: float) -> float | None:
    """Return the speed limit to apply, or None if not confirmed yet."""
    if new_limit_ms is None:
      self._confirmed_limit_ms = None
      self._unconfirmed_limit_ms = None
      return None

    if self._confirmed_limit_ms is None:
      self._confirmed_limit_ms = new_limit_ms
      return new_limit_ms

    delta = new_limit_ms - self._confirmed_limit_ms
    if abs(delta) < self.MIN_CHANGE_MS:
      return self._confirmed_limit_ms

    is_lower = delta < 0
    needs_confirm = (is_lower and self._confirmation_lower) or (not is_lower and self._confirmation_higher)

    if not needs_confirm:
      self._confirmed_limit_ms = new_limit_ms
      return new_limit_ms

    # New limit needs confirmation
    if self._unconfirmed_limit_ms != new_limit_ms:
      self._unconfirmed_limit_ms = new_limit_ms
      self._change_time = now

    # Driver override (gas press) acts as confirmation
    if driver_overriding:
      self._confirmed_limit_ms = new_limit_ms
      self._unconfirmed_limit_ms = None
      return new_limit_ms

    # Auto-confirm after timeout
    if now - self._change_time > self.CONFIRMATION_TIMEOUT:
      self._confirmed_limit_ms = new_limit_ms
      self._unconfirmed_limit_ms = None
      return new_limit_ms

    # Not confirmed yet — keep old limit
    return self._confirmed_limit_ms

  def is_unconfirmed(self) -> bool:
    return self._unconfirmed_limit_ms is not None

  def unconfirmed_limit(self) -> float | None:
    return self._unconfirmed_limit_ms


def detect_exopilot_platform() -> str:
  """Detect ExoPilot platform based on hardware device tree.

  Returns 'exopilot01m' for RK3588 or 'exopilot02m' for RK3576 — both are
  openpilot-supported platforms as of 2026-08-26 (see
  docs/eop/RK3576_02M_SUPPORT.md), not just data-provenance tags for a
  VisionPilot-only board. Data merges across the whole ExoPilot fleet
  (including VisionPilot, also on 02M) still rely on this tag being
  accurate for both platforms, same as before.

  Supports the HARDWARE environment variable override for testing, matching
  system/hardware/registry.py's PlatformRegistry.detect() convention —
  added 2026-08-26, this function previously had no test-friendly way to
  exercise the rk3576 branch without a real device tree.

  EOP-CLEANUP: Extracted from cslb.py and surface_quality_db.py which had
  nearly identical copies of this function.
  """
  env_platform = os.environ.get('HARDWARE', '').lower()
  if 'rk3588' in env_platform:
    return 'exopilot01m'
  if 'rk3576' in env_platform:
    return 'exopilot02m'

  try:
    with open('/proc/device-tree/compatible') as f:
      compatible = f.read()
      if 'rk3588' in compatible:
        return 'exopilot01m'
      elif 'rk3576' in compatible:
        return 'exopilot02m'
  except Exception:
    pass

  return 'exopilot01m'

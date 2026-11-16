"""
speed_utils.py - Shared utilities for EOP control modules.

Centralizes code that was duplicated across MTSC, VTSC, CSLB, DLON, AEB.

Convention for params I/O:
  - Cache params at __init__
  - Refresh no more than once per second
  - Never read params on every frame (20-100 Hz file I/O is expensive)
"""
from __future__ import annotations

from typing import cast



# GPS tolerance for curve-speed DB lookups (CSLB/MTSC/VTSC). ExoPilot 02M's
# ZED-F9P is RTK-capable hardware, but no RTCM correction path exists yet
# (no NTRIP client) — see selfdrive/ui/settings/descriptor.py's
# RTKEnabled/NTRIPEnabled toggles, which are UI-only today. Until
# corrections actually flow, every ExoPilot platform's fix is uncorrected
# standard GPS, so 50m stays the right tolerance regardless of module.
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

  All ExoPilot platforms use uncorrected GPS today (RTK-capable hardware
  exists on 02M, but no RTCM correction path is wired up yet).
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


# SpeedLimitOffset / SpeedLimitConfirmation: the one copy is in exopilot/selfdrive/controls/lib/mslc.py (MSLC and NSLC).


def detect_exopilot_platform() -> str:
  """Use the detected SoC identity, then this build's default on a dev PC."""
  from openpilot.common.build_profile import DEFAULT_SOC
  from openpilot.system.hardware.registry import PlatformRegistry
  platform = PlatformRegistry.detect().lower()
  if platform in ('rk3576', 'exopilot02m'):
    return 'exopilot02m'
  if platform in ('rk3588', 'rk3588s', 'rk3588s2', 'exopilot01m'):
    return 'exopilot01m'
  return 'exopilot02m' if DEFAULT_SOC == 'rk3576' else 'exopilot01m'

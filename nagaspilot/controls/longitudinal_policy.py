import numpy as np

from openpilot.common.constants import CV


ADAPTIVE_ACCEL_CITY_SPEED_LIMIT = 13.9  # m/s (~50 km/h)


def apply_adaptive_accel_limit(raw_max_accel: float, v_cruise: float, v_ego: float) -> float:
  """Apply the shared low-speed and near-cruise acceleration limits."""
  low_speed_limit = np.interp(v_ego, [0.0, ADAPTIVE_ACCEL_CITY_SPEED_LIMIT / 2, ADAPTIVE_ACCEL_CITY_SPEED_LIMIT],
                              [raw_max_accel / 4, raw_max_accel / 2, raw_max_accel])
  ramp_off = np.interp(v_cruise - v_ego, [0.0, 1.0, 5.0], [0.0, 0.5, raw_max_accel])
  return min(raw_max_accel, low_speed_limit, ramp_off)


def apply_speed_offset_kph(speed_limit_kph: float, offset_kph: float) -> float:
  """Add the shared driver speed offset to a speed expressed in km/h."""
  return speed_limit_kph + offset_kph


def apply_cruise_speed_offset_mps(v_cruise: float, offset_kph: float) -> float:
  """Add a km/h driver offset to a cruise target expressed in m/s."""
  return apply_speed_offset_kph(v_cruise * CV.MS_TO_KPH, offset_kph) * CV.KPH_TO_MS

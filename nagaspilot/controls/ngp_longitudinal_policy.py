import numpy as np

from openpilot.common.constants import CV


ADAPTIVE_ACCEL_CITY_SPEED_LIMIT = 13.9  # m/s (~50 km/h)


def _apply_adaptive_accel_limit(raw_max_accel: float, v_cruise: float, v_ego: float) -> float:
  """Reduce max acceleration at low speeds and near cruise speed."""
  low_speed_limit = np.interp(v_ego, [0.0, ADAPTIVE_ACCEL_CITY_SPEED_LIMIT / 2, ADAPTIVE_ACCEL_CITY_SPEED_LIMIT],
                              [raw_max_accel / 4, raw_max_accel / 2, raw_max_accel])
  ramp_off = np.interp(v_cruise - v_ego, [0.0, 1.0, 5.0], [0.0, 0.5, raw_max_accel])
  return min(raw_max_accel, low_speed_limit, ramp_off)


def _apply_speed_offset(v_cruise: float, offset_kph: float) -> float:
  """Apply a constant driver speed offset in km/h to a m/s cruise target."""
  return (v_cruise * CV.MS_TO_KPH + offset_kph) * CV.KPH_TO_MS

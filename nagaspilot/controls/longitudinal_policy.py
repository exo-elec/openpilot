import numpy as np

from openpilot.common.constants import CV


ADAPTIVE_ACCEL_CITY_SPEED_LIMIT = 13.9  # m/s (~50 km/h)

ACCELERATION_PROFILES = {
  "normal": (1.6, 1.2, 0.8, 0.6),
  "eco": (1.2, 0.9, 0.6, 0.4),
  "sport": (2.0, 1.6, 1.2, 0.8),
}


def acceleration_profile_limit(v_ego: float, profile: str, speed_breakpoints, profiles=ACCELERATION_PROFILES) -> float:
  """Return the interpolated acceleration ceiling for a named profile."""
  values = profiles.get(profile, profiles["normal"])
  return float(np.interp(v_ego, speed_breakpoints, values))


def adaptive_follow_gap(v_ego: float, d_rel: float, v_lead: float, t_follow_base: float,
                       jerk_base: float, stop_distance: float, comfort_brake: float):
  """Return adaptive (time-gap, jerk) overrides from the current lead observation."""
  if v_lead > v_ego:
    distance_factor = max(d_rel - (v_ego * t_follow_base), 1.0)
    offset = float(np.clip(stop_distance - v_ego, 1.0, distance_factor))
    return max(t_follow_base / offset, 0.5), jerk_base / offset

  if v_lead < v_ego:
    distance_factor = max(d_rel - (v_lead * t_follow_base), 1.0)
    braking_offset = float(np.clip(min(v_ego - v_lead, v_lead) - comfort_brake, 1.0, distance_factor))
    if d_rel >= 100.0:
      braking_offset += max(d_rel - (v_ego * t_follow_base) - stop_distance, 0.0)
    return max(t_follow_base / braking_offset, 0.5), jerk_base

  return None, None


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

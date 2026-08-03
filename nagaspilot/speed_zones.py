"""Canonical NagasPilot operating-speed ranges."""

from enum import IntEnum


CRAWL_SPEED_MPS = 0.0
WALK_SPEED_MPS = 2.0
CITY_SPEED_MPS = 6.0
URBAN_SPEED_MPS = 12.0
HIGHWAY_SPEED_MPS = 24.0
MAX_SPEED_MPS = 36.0

# Positive longitudinal comfort envelope. Braking remains governed by the
# planner and hard safety limits; these values only soften gap closing.
LONGITUDINAL_PROFILE_SPEEDS_MPS = (CRAWL_SPEED_MPS, WALK_SPEED_MPS, CITY_SPEED_MPS,
                                   URBAN_SPEED_MPS, HIGHWAY_SPEED_MPS, MAX_SPEED_MPS)
LONGITUDINAL_ACCEL_MAX_MPS2 = (0.45, 0.45, 0.70, 1.0, 1.2, 1.4)
LONGITUDINAL_JERK_UP_MPS3 = (0.35, 0.35, 0.55, 0.8, 1.2, 1.5)


class SpeedZone(IntEnum):
  CRAWL = 0
  WALK = 1
  CITY = 2
  URBAN = 3
  HIGHWAY = 4


def speed_zone(v_ego: float) -> SpeedZone:
  speed = max(0.0, float(v_ego))
  if speed < WALK_SPEED_MPS:
    return SpeedZone.CRAWL
  if speed < CITY_SPEED_MPS:
    return SpeedZone.WALK
  if speed < URBAN_SPEED_MPS:
    return SpeedZone.CITY
  if speed < HIGHWAY_SPEED_MPS:
    return SpeedZone.URBAN
  return SpeedZone.HIGHWAY


def _interp_profile(v_ego: float, values: tuple[float, ...]) -> float:
  speed = max(0.0, min(float(v_ego), MAX_SPEED_MPS))
  for index in range(1, len(LONGITUDINAL_PROFILE_SPEEDS_MPS)):
    upper = LONGITUDINAL_PROFILE_SPEEDS_MPS[index]
    if speed <= upper:
      lower = LONGITUDINAL_PROFILE_SPEEDS_MPS[index - 1]
      fraction = 0.0 if upper == lower else (speed - lower) / (upper - lower)
      return values[index - 1] + fraction * (values[index] - values[index - 1])
  return values[-1]


def longitudinal_accel_max(v_ego: float) -> float:
  return _interp_profile(v_ego, LONGITUDINAL_ACCEL_MAX_MPS2)


def longitudinal_jerk_up(v_ego: float) -> float:
  return _interp_profile(v_ego, LONGITUDINAL_JERK_UP_MPS3)

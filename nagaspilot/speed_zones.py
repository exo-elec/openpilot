"""Canonical NagasPilot operating-speed ranges."""

from enum import IntEnum


CRAWL_SPEED_MPS = 0.0
WALK_SPEED_MPS = 2.0
CITY_SPEED_MPS = 6.0
URBAN_SPEED_MPS = 12.0
HIGHWAY_SPEED_MPS = 24.0
MAX_SPEED_MPS = 36.0

# Additional technical breakpoints, not driving-context zones - only used to
# give the steering angle/rate backstop LUT (opendbc/safety/modes/byd.h,
# opendbc/car/byd/values.py, TC275/TC375 BrownPanda firmware) enough
# resolution between URBAN and HIGHWAY that linear interpolation doesn't
# loosen the worst-case lateral accel bound (verified: dropping to the 6
# named points alone raises the 12-24 m/s peak from 1.35g to 1.61g).
STEER_MID_URBAN_HIGHWAY_MPS = 18.0
STEER_MID_HIGHWAY_MAX_MPS = 30.0

# Canonical 8-point grid for the steering backstop LUT specifically. All
# implementations (byd.h, values.py, TC275/TC375 firmware) must use exactly
# these breakpoints; opendbc_repo/the firmware have no dependency on this
# module, so those hardcode the same numbers with a comment citing this file.
STEER_ZONE_SPEEDS_MPS = (CRAWL_SPEED_MPS, WALK_SPEED_MPS, CITY_SPEED_MPS,
                         URBAN_SPEED_MPS, STEER_MID_URBAN_HIGHWAY_MPS,
                         HIGHWAY_SPEED_MPS, STEER_MID_HIGHWAY_MAX_MPS,
                         MAX_SPEED_MPS)

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


# BYD Atto3 steering backstop LUT, gateway/panda side (100%). Canonical
# source for opendbc/safety/modes/byd.h, opendbc/car/byd/values.py, and
# TC275/TC375 BrownPanda firmware, none of which can import this module -
# each hardcodes these same numbers with a comment citing this file. See
# nagaspilot/docs/STEERING_LIMIT_POLICY.md for the derivation and G-force
# verification. openpilot's own controller-side backstop uses 80% of these.
STEER_ZONE_ANGLE_DEG = (390.0, 390.0, 360.0, 240.0, 120.0, 60.0, 45.0, 30.0)
STEER_ZONE_RATE_DEG_20MS = (4.0, 4.0, 4.0, 4.0, 3.2, 2.4, 1.6, 1.2)


def _interp_steer_zone(v_ego: float, values: tuple[float, ...]) -> float:
  speed = max(0.0, min(float(v_ego), MAX_SPEED_MPS))
  for index in range(1, len(STEER_ZONE_SPEEDS_MPS)):
    upper = STEER_ZONE_SPEEDS_MPS[index]
    if speed <= upper:
      lower = STEER_ZONE_SPEEDS_MPS[index - 1]
      fraction = 0.0 if upper == lower else (speed - lower) / (upper - lower)
      return values[index - 1] + fraction * (values[index] - values[index - 1])
  return values[-1]


def steer_zone_max_angle_deg(v_ego: float) -> float:
  return _interp_steer_zone(v_ego, STEER_ZONE_ANGLE_DEG)


def steer_zone_max_rate_deg_20ms(v_ego: float) -> float:
  return _interp_steer_zone(v_ego, STEER_ZONE_RATE_DEG_20MS)

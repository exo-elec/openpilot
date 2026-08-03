from nagaspilot.speed_zones import (CITY_SPEED_MPS, CRAWL_SPEED_MPS, HIGHWAY_SPEED_MPS, MAX_SPEED_MPS,
                                    STEER_ZONE_SPEEDS_MPS, URBAN_SPEED_MPS, WALK_SPEED_MPS, SpeedZone,
                                    longitudinal_accel_max, longitudinal_jerk_up, speed_zone,
                                    steer_zone_max_angle_deg, steer_zone_max_rate_deg_20ms)


def test_speed_zone_anchors_are_ordered():
  assert (CRAWL_SPEED_MPS, WALK_SPEED_MPS, CITY_SPEED_MPS,
          URBAN_SPEED_MPS, HIGHWAY_SPEED_MPS, MAX_SPEED_MPS) == (0.0, 2.0, 6.0, 12.0, 24.0, 36.0)


def test_speed_zone_boundaries():
  assert speed_zone(-1.0) == SpeedZone.CRAWL
  assert speed_zone(1.999) == SpeedZone.CRAWL
  assert speed_zone(2.0) == SpeedZone.WALK
  assert speed_zone(5.999) == SpeedZone.WALK
  assert speed_zone(6.0) == SpeedZone.CITY
  assert speed_zone(11.999) == SpeedZone.CITY
  assert speed_zone(12.0) == SpeedZone.URBAN
  assert speed_zone(24.0) == SpeedZone.HIGHWAY
  assert speed_zone(36.0) == SpeedZone.HIGHWAY


def test_gap_closing_profile_is_monotonic_and_clamped():
  speeds = [-1.0, 0.0, 2.0, 6.0, 12.0, 24.0, 36.0, 50.0]
  accel = [longitudinal_accel_max(speed) for speed in speeds]
  jerk = [longitudinal_jerk_up(speed) for speed in speeds]
  assert accel == sorted(accel)
  assert jerk == sorted(jerk)
  assert accel[-1] == accel[-2]
  assert jerk[-1] == jerk[-2]


def test_steer_zone_speeds_are_ordered_8_points():
  assert STEER_ZONE_SPEEDS_MPS == (0.0, 2.0, 6.0, 12.0, 18.0, 24.0, 30.0, 36.0)


def test_steer_zone_angle_matches_panda_lut():
  # Canonical panda/gateway (100%) values - must match opendbc/safety/modes/byd.h,
  # opendbc/car/byd/values.py, and both TC275/TC375 firmware exactly.
  expected = {0: 390.0, 2: 390.0, 6: 360.0, 12: 240.0, 18: 120.0, 24: 60.0, 30: 45.0, 36: 30.0}
  for speed, angle in expected.items():
    assert steer_zone_max_angle_deg(speed) == angle


def test_steer_zone_rate_matches_panda_lut():
  expected = {0: 4.0, 2: 4.0, 6: 4.0, 12: 4.0, 18: 3.2, 24: 2.4, 30: 1.6, 36: 1.2}
  for speed, rate in expected.items():
    assert steer_zone_max_rate_deg_20ms(speed) == rate


def test_steer_zone_angle_monotonic_decreasing():
  speeds = [0.0, 2.0, 6.0, 12.0, 18.0, 24.0, 30.0, 36.0, 50.0]
  angles = [steer_zone_max_angle_deg(speed) for speed in speeds]
  assert angles == sorted(angles, reverse=True)
  assert angles[-1] == angles[-2]  # clamped beyond MAX_SPEED_MPS

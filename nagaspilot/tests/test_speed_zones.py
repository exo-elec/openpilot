from nagaspilot.speed_zones import (CITY_SPEED_MPS, CRAWL_SPEED_MPS, HIGHWAY_SPEED_MPS, MAX_SPEED_MPS,
                                    URBAN_SPEED_MPS, WALK_SPEED_MPS, SpeedZone, longitudinal_accel_max,
                                    longitudinal_jerk_up, speed_zone)


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

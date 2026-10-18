import math

import pytest

from nagaspilot.localization import lever_arm as L


def test_facing_north_with_the_phone_on_the_left_the_comma_is_east_of_it():
  lat, lon = L.phone_to_comma(13.75, 100.5, 0.0, L.LEFT, 0.2)
  assert lat == pytest.approx(13.75, abs=1e-9)
  assert lon > 100.5
  assert L.distance_m(13.75, 100.5, lat, lon) == pytest.approx(0.2, abs=1e-3)


def test_facing_east_the_phone_on_the_left_is_north_of_the_comma():
  lat, lon = L.phone_to_comma(13.75, 100.5, 90.0, L.LEFT, 0.2)
  assert lat < 13.75 and lon == pytest.approx(100.5, abs=1e-9)


def test_the_right_side_is_the_opposite_step():
  a = L.phone_to_comma(40.0, -3.0, 37.0, L.LEFT, 0.3)
  b = L.phone_to_comma(40.0, -3.0, 37.0, L.RIGHT, 0.3)
  assert (a[0] - 40.0) == pytest.approx(-(b[0] - 40.0), abs=1e-12)
  assert (a[1] + 3.0) == pytest.approx(-(b[1] + 3.0), abs=1e-12)


def test_the_step_is_the_offset_at_any_heading_and_latitude():
  for lat in (0.0, 13.75, 51.5, 64.0):
    for h in (0, 45, 90, 135, 180, 225, 270, 315):
      p = L.phone_to_comma(lat, 10.0, h, L.RIGHT, 0.25)
      assert L.distance_m(lat, 10.0, *p) == pytest.approx(0.25, abs=2e-3)


def test_the_step_is_across_the_car_not_along_it():
  # heading 60 degrees: moving 0.4 m left-right changes nothing along the heading
  lat, lon = 20.0, 30.0
  p = L.phone_to_comma(lat, lon, 60.0, L.LEFT, 0.4)
  m_lat, m_lon = L.metres_per_degree(lat)
  de, dn = (p[1] - lon) * m_lon, (p[0] - lat) * m_lat
  along = de * math.sin(math.radians(60)) + dn * math.cos(math.radians(60))
  assert along == pytest.approx(0.0, abs=1e-3)


def test_a_zero_offset_leaves_the_fix_alone_and_bad_input_is_refused():
  assert L.phone_to_comma(1.0, 2.0, 123.0, L.LEFT, 0.0) == (1.0, 2.0)
  with pytest.raises(ValueError):
    L.phone_to_comma(1.0, 2.0, 0.0, 'centre')
  with pytest.raises(ValueError):
    L.phone_to_comma(1.0, 2.0, 0.0, L.LEFT, 5.0)
  with pytest.raises(ValueError):
    L.phone_to_comma(1.0, 2.0, 0.0, L.LEFT, -0.1)

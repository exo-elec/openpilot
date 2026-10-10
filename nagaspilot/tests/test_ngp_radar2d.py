import math
from nagaspilot.controls.ngp_radar2d import blindspot_blocked, ground_range


def test_can_flags_are_side_specific_presence():
  assert blindspot_blocked(True, False, 'left')
  assert not blindspot_blocked(True, False, 'right')
  assert blindspot_blocked(False, True, 'right')
  assert not blindspot_blocked(True, True, 'none')


def test_flatten_preserves_horizontal_range_and_rejects_unknown_geometry():
  assert ground_range(10, 0) == 10
  assert math.isclose(ground_range(10, 60), 5)
  assert math.isclose(ground_range(10, -60), 5)
  for distance, elevation in [(math.nan, 0), (-1, 0), (10, math.nan), (10, 91)]:
    assert math.isnan(ground_range(distance, elevation))

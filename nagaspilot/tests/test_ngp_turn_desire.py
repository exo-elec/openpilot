from nagaspilot.controls.ngp_turn_desire import NGPTurnDesire

MAX = 9.0


def test_off_without_a_ceiling_and_outside_the_speed_band():
  assert NGPTurnDesire(0.0).update(5.0, True, False, True, False) is None
  t = NGPTurnDesire(MAX)
  assert t.update(1.0, True, False, True, False) is None  # standing still
  assert t.update(12.0, True, False, True, False) is None  # lane-change speed
  assert t.update(MAX, True, False, True, False) is None  # the ceiling is exclusive


def test_needs_exactly_one_signal_active_lateral_and_no_lane_change():
  t = NGPTurnDesire(MAX)
  assert t.update(5.0, False, False, True, False) is None
  assert t.update(5.0, True, True, True, False) is None  # hazards
  assert t.update(5.0, True, False, False, False) is None
  assert t.update(5.0, True, False, True, True) is None


def test_direction_follows_the_signal():
  t = NGPTurnDesire(MAX)
  assert t.update(5.0, True, False, True, False) == 'left'
  assert t.update(5.0, False, True, True, False) == 'right'

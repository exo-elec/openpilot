from nagaspilot.controls.ngp_cutin_speed import PlannedPath
from nagaspilot.controls.ngp_path_selector import MAX_NUDGE_M, SPEED_FACTOR_MIN, PathSelector, PObj

V = 25.0


def o(name='car', x=0.0, y=-2.2, vx=0.0, vy=0.0, conf=0.9, tid=1):
  return PObj(tid, name, x, y, vx, vy, conf)


def sel(objs, left=0.5, right=0.5, **kw):
  return PathSelector().update(V, objs, None, left, right, **kw)


def test_clear_road_no_nudge_no_slowdown():
  r = sel([])
  assert r.offset_m == 0.0 and r.speed_factor == 1.0 and r.reason == 'clear'
  r = sel([o(x=0.0, y=-4.5)])                    # an adjacent-lane car far enough sideways
  assert r.offset_m == 0.0 and r.reason == 'clear'


def test_nudges_away_from_a_close_vehicle_alongside():
  # a truck alongside on the right (y=-2.3, wide), room on both sides: move left
  r = sel([o('truck', y=-2.3)])
  assert r.offset_m > 0 and r.offset_m <= MAX_NUDGE_M + 1e-9   # a truck this close may still need 'slow' on top of the nudge
  # mirrored: truck on the left -> move right (negative)
  r = sel([o('truck', y=+2.3)])
  assert r.offset_m < 0


def test_motorcycle_filtering_closely_gets_more_room():
  r_bike = sel([o('motorcycle', y=-1.9)])
  r_car = sel([o('car', y=-1.9)])
  assert r_bike.offset_m >= r_car.offset_m and r_bike.offset_m > 0


def test_no_room_means_slowdown_not_a_nudge():
  r = sel([o('car', y=-1.5)], left=0.0, right=0.0)     # nowhere to go inside the lane
  assert r.offset_m == 0.0 and r.reason == 'slow' and SPEED_FACTOR_MIN <= r.speed_factor < 1.0


def test_offset_never_exceeds_room_and_speed_only_tightens():
  r = sel([o('truck', y=-1.6)], left=0.2, right=0.5)
  assert 0.0 <= r.offset_m <= 0.2 + 1e-9 and r.speed_factor <= 1.0


def test_low_confidence_and_behind_objects_ignored_and_gates_reset():
  assert sel([o(conf=0.2, y=-1.0)]).reason == 'clear'
  assert sel([o(x=-10.0, y=-1.0)]).reason == 'clear'
  s = PathSelector()
  s.update(V, [o('truck', y=-2.0)], None, 0.5, 0.5)
  assert s.last_offset != 0.0
  assert s.update(5.0, [o('truck', y=-2.0)], None, 0.5, 0.5).reason == 'off' and s.last_offset == 0.0
  assert s.update(V, [], None, 0.5, 0.5, enabled=False).reason == 'off'


def test_follows_a_curved_path():
  # path bends left; the car on the right lane edge stays the same distance from a curving path only if it follows it
  curve = PlannedPath([0.0, 40.0, 80.0], [0.0, 1.0, 4.0])
  r = PathSelector().update(V, [o('truck', x=20.0, y=-1.0, vx=0.0)], curve, 0.5, 0.5)
  assert r.offset_m >= 0.0


def test_smoothness_prefers_previous_offset():
  s = PathSelector()
  a = s.update(V, [o('truck', y=-2.3)], None, 0.5, 0.5).offset_m
  b = s.update(V, [o('truck', y=-2.3)], None, 0.5, 0.5).offset_m
  assert abs(b - a) < 0.21


def test_enough_room_nudge_alone_is_enough():
  r = sel([o('truck', y=-2.8)])          # 3.2 m needed centre to centre: a 0.4 m nudge clears it
  assert 0.3 <= r.offset_m <= MAX_NUDGE_M and r.reason == 'nudge' and r.speed_factor == 1.0


def test_profile_ramps_at_the_slew_rate_and_speed_never_exceeds_v_ego():
  from nagaspilot.controls.ngp_path_selector import PROFILE_SLEW_M_PER_S, Selection, build_profile
  sel = Selection(0.4, 0.9, -0.1, 1.0, 'slow')
  offs, caps = build_profile(sel, 25.0)
  assert len(offs) == len(caps) == 12
  assert abs(offs[0] - PROFILE_SLEW_M_PER_S * 0.25) < 1e-9 and offs == sorted(offs) and offs[-1] <= 0.4 + 1e-9
  assert abs(caps[0] - 25.0 * (1 - 0.1 * 0.25)) < 1e-9 and abs(caps[-1] - 22.5) < 1e-9 and max(caps) <= 25.0
  offs, caps = build_profile(Selection(0.0, 1.0, None, 0.0, 'clear'), 25.0, start_offset=0.3)
  assert offs[-1] < 0.3 and all(c == 25.0 for c in caps)           # releases toward zero, no speed request

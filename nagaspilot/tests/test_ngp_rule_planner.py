import math

from nagaspilot.controls.ngp_cutin_speed import PlannedPath
from nagaspilot.controls.ngp_path_selector import PObj
from nagaspilot.controls.ngp_rule_planner import A_BRAKE_LIMIT, RulePlanner

STRAIGHT = PlannedPath([0.0, 100.0], [0.0, 0.0])
V = 25.0


def plan(objs=(), lane=STRAIGHT, v=V, setv=25.0, **kw):
  return RulePlanner().plan(v, setv, lane, 0.5, 0.5, list(objs), **kw)


def car(x, y=0.0, vx=0.0, vy=0.0, name='car', tid=1):
  return PObj(tid, name, x, y, vx, vy, 0.9)


def test_free_road_holds_speed_and_centre():
  c = plan()
  assert c.valid and abs(c.curvature) < 1e-9 and abs(c.accel) < 0.05 and c.lead_id is None
  assert plan(v=20.0).accel > 0.5                      # below the set speed: accelerates
  assert plan(v=25.0, setv=15.0).accel < -0.5          # above it: brakes


def test_curve_is_followed_and_speed_limited_by_lateral_accel():
  lane = PlannedPath([0.0, 25.0, 50.0, 100.0], [0.0, 1.0, 4.0, 16.0])   # bends left
  c = plan(lane=lane)
  assert c.curvature > 0 and c.speed_target < 25.0
  right = PlannedPath([0.0, 25.0, 50.0, 100.0], [0.0, -1.0, -4.0, -16.0])
  assert plan(lane=right).curvature < 0


def test_follows_a_slower_lead_with_idm_and_never_exceeds_the_brake_limit():
  c = plan([car(35.0, vx=-5.0)])                       # lead at 20 m/s, 35 m ahead, closing
  assert c.lead_id == 1 and c.accel < -0.3 and c.accel >= A_BRAKE_LIMIT
  c = plan([car(8.0, vx=-15.0)])                       # hard closing, very close
  assert c.accel == A_BRAKE_LIMIT


def test_ignores_adjacent_lane_and_slows_for_a_cut_in():
  assert plan([car(20.0, y=3.6)]).lead_id is None
  c = plan([car(22.0, y=3.0, vx=-6.0, vy=-1.5)])        # cutting in from the left
  assert c.lead_id == 1 and c.accel < 0.0


def test_radar_lead_fallback_and_invalid_lane():
  c = plan(radar_leads=[(30.0, 0.0, 18.0)])
  assert c.accel < 0.0 and c.lead_id is None and c.lead_gap == 30.0
  bad = RulePlanner().plan(V, 25.0, PlannedPath([1.0], [0.0]), 0.5, 0.5, [])
  assert not bad.valid and bad.reason == 'no lane'
  assert not RulePlanner().plan(float('nan'), 25.0, STRAIGHT, 0.5, 0.5, []).valid


def test_lateral_offset_from_a_truck_alongside_shows_in_curvature():
  c = plan([car(0.0, y=-2.4, name='truck')])
  assert c.offset_m > 0 and c.curvature > 0 and math.isfinite(c.curvature)

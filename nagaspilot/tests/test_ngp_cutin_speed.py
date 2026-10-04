import math

from nagaspilot.controls.ngp_cutin_speed import CAR, HOLD_S, MIN_TARGET_SPEED, CutInSpeed, Obj, evaluate


def obj(x=22.0, y=3.0, vx=-6.0, vy=-1.5, sigma=2.0, conf=0.8, tid=1, name='car'):
  return Obj(tid, x, y, vx, vy, sigma, conf, name)


def run(c, o, n=3, v=25.0, dt=0.2, **kw):
  r = None
  for _ in range(n):
    r = c.update(v, [o] if o else [], dt, **kw)
  return r


def test_cut_in_from_left_and_right_triggers_only_after_confirmation():
  for side in (1, -1):
    c = CutInSpeed()
    o = obj(y=3.0 * side, vy=-1.5 * side)
    assert not c.update(25.0, [o], 0.2).active          # first update: not confirmed
    r = c.update(25.0, [o], 0.2)
    assert r.active and r.target_speed < 25.0 and r.track_id == 1


def test_target_matches_object_speed_but_is_capped_and_floored():
  c = CutInSpeed()
  r = run(c, obj(x=12.0, vx=-2.0))          # object at 23 m/s: target ~23
  assert abs(r.target_speed - 23.0) < 1e-6
  c = CutInSpeed()
  r = run(c, obj(vx=-20.0, x=60.0))         # object far slower: capped at 25 % below
  assert abs(r.target_speed - 25.0 * (1 - CAR.max_reduction)) < 1e-6
  c = CutInSpeed()
  r = run(c, obj(vx=-15.0, x=60.0), v=10.0)  # 25 % of 10 is 7.5 < floor
  assert r.target_speed >= min(MIN_TARGET_SPEED, 10.0) - 1e-9


def test_no_action_cases():
  assert evaluate(25.0, obj(vy=0.0)) is None                      # steady in the adjacent lane
  assert evaluate(25.0, obj(y=0.5)) is None                       # already in the corridor
  assert evaluate(25.0, obj(vy=+1.5)) is None                     # moving away
  assert evaluate(25.0, obj(y=9.0, vy=-1.0)) is None              # enters in > 3 s
  assert evaluate(25.0, obj(conf=0.0)) is None                    # coasting (occluded) track
  assert evaluate(25.0, obj(sigma=15.0)) is None                  # range too uncertain
  assert evaluate(25.0, obj(x=2.0)) is None and evaluate(25.0, obj(x=120.0)) is None
  assert evaluate(25.0, obj(vx=+2.0, x=60.0)) is None             # pulling away with a big gap
  assert evaluate(25.0, obj(x=3.5, vx=-30.0)) is None             # passes behind before entry? gap<=0
  assert evaluate(float('nan'), obj()) is None


def test_headway_trigger_when_not_closing():
  o = obj(x=15.0, vx=0.0)                    # same speed, 0.6 s gap at 25 m/s
  assert evaluate(25.0, o) is not None
  assert evaluate(25.0, obj(x=60.0, vx=0.0)) is None


def test_hold_then_release_and_never_raises():
  c = CutInSpeed()
  r = run(c, obj())
  t0 = r.target_speed
  r = run(c, None, n=3, dt=0.2)              # criteria gone, within the hold
  assert r.active and r.target_speed == t0
  r = run(c, None, n=int(HOLD_S / 0.2) + 2, dt=0.2)
  assert not r.active and r.target_speed is None


def test_gates_reset_state():
  c = CutInSpeed()
  run(c, obj())
  assert not c.update(25.0, [obj()], 0.2, enabled=False).active
  assert not c.update(25.0, [obj()], 0.2, fresh=False).active
  assert not c.update(5.0, [obj()], 0.2).active                    # crawling speed
  assert not c.update(25.0, [obj()], 0.2).active                   # confirmation restarted after the reset


def test_most_urgent_object_wins_and_track_switch_restarts_confirmation():
  c = CutInSpeed()
  a, b = obj(tid=1, x=40.0), obj(tid=2, x=20.0)
  c.update(25.0, [a, b], 0.2)
  r = c.update(25.0, [a, b], 0.2)
  assert r.active and r.track_id == 2
  assert math.isfinite(r.ttc)


def test_two_wheeler_triggers_where_a_car_would_not():
  # slow lateral drift (0.25 m/s) and a looser gap: below the car criteria, inside the two-wheeler ones
  m = obj(y=2.0, vy=-0.25, x=33.0, vx=-3.0, name='motorcycle')
  c = obj(y=2.0, vy=-0.25, x=33.0, vx=-3.0, name='car')
  assert evaluate(25.0, c) is None and evaluate(25.0, m) is not None


def test_two_wheeler_gets_a_stronger_cap():
  c = CutInSpeed()
  r = run(c, obj(vx=-20.0, x=60.0, name='motorcycle'))
  assert abs(r.target_speed - 25.0 * 0.70) < 1e-6


def test_non_cut_in_classes_ignored():
  assert evaluate(25.0, obj(name='person')) is None
  assert evaluate(25.0, obj(name='traffic light')) is None

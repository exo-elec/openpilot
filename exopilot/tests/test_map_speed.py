import math
from types import SimpleNamespace as NS

from exopilot.selfdrive.controls.lib.map_speed import MAP_HOLD_S, MapSpeed, distraction_status
from exopilot.selfdrive.controls.lib.feature_keys import MAP_SPEED_KEYS

XS = [float(i * 6) for i in range(33)]


class Clock:
  t = 100.0

  def __call__(self):
    return self.t


def make(flags=('mtsc', 'mslc', 'tlsc', 'ddsc')):
  clk = Clock()
  bools = {MAP_SPEED_KEYS[k]: True for k in flags}
  return MapSpeed(lambda k: bools.get(k, False), lambda k: "", clk), clk


def sm(mapd=None, updated=False, lights=(), lead=False, awareness=1.0, gas=False, v=25.0, vlead=20.0):
  """`mapd` = (mapdOut, mapdExtendedOut) as the offline mapd publishes them."""
  out, ext = mapd or (None, None)
  s = type('S', (dict,), {})({
    'carState': NS(gasPressed=gas, vEgo=v, standstill=False),
    'radarState': NS(leadOne=NS(status=lead, vLead=vlead)),
    'mapdOut': out, 'mapdExtendedOut': ext,
    'stereoObjects': NS(objects=[NS(obstacleType='trafficLight', trafficLightState=st, trafficLightConfidence=0.5, dRel=d, yRel=y) for st, d, y in lights]),
    'driverMonitoringState': NS(awarenessStatus=awareness),
    'modelV2': NS(position=NS(x=XS, y=[0.0] * 33))})
  s.updated = {'mapdOut': updated, 'mapdExtendedOut': updated}
  s.valid = {'carState': True, 'radarState': True, 'mapdOut': out is not None, 'mapdExtendedOut': ext is not None, 'stereoObjects': True,
             'driverMonitoringState': True, 'modelV2': True}
  return s


M_PER_DEG_LAT = 6371000.0 * 3.141592653589793 / 180.0


def mapd(limit_ms=0.0, curve_at=None, k=0.004):
  """mapdOut + mapdExtendedOut: a straight path north from (0, 0) with one curved point `curve_at` metres ahead."""
  points = [NS(latitude=d / M_PER_DEG_LAT, longitude=0.0, curvature=(k if d == curve_at else 0.0)) for d in range(0, 501, 50)]
  return (NS(speedLimit=limit_ms, nextSpeedLimit=0.0, nextSpeedLimitDistance=0.0),
          NS(position=NS(latitude=0.0, longitude=0.0), path=points))


def curve(x=300, y=0.004):
  return mapd(curve_at=x, k=y)


def test_nothing_enabled_means_no_cap():
  ms, _ = make(flags=())
  assert ms.update(sm(), 25.0, 25.0, None, False, 0.05).cap is None


def test_map_result_is_held_between_one_hz_messages_and_expires():
  ms, clk = make(flags=('mtsc',))
  r = ms.update(sm(curve(), updated=True), 25.0, 25.0, None, False, 0.05)
  assert abs(r.cap - 21.213) < 1e-2 and r.sources == {'mtsc': r.cap}
  for _ in range(19):                                                      # the next 19 planner cycles carry no new map message
    clk.t += 0.05
    r = ms.update(sm(curve(), updated=False), 25.0, 25.0, None, False, 0.05)
    assert r.cap is not None and abs(r.cap - 21.213) < 1e-2                  # EOP10's inline code dropped the target on every one of these
  clk.t += MAP_HOLD_S + 0.1
  assert ms.update(sm(curve(), updated=False), 25.0, 25.0, None, False, 0.05).cap is None


def test_mslc_cap_never_raises_the_cruise_speed_and_follows_the_posted_limit():
  ms, _ = make(flags=('mslc',))
  r = ms.update(sm(mapd(limit_ms=80.0 / 3.6), updated=True), 20.0, 30.0, None, False, 0.05)
  assert abs(r.cap - 80.0 / 3.6) < 1e-6 and r.cap <= 30.0 + 1e-9


def test_traffic_light_on_our_path_stops_and_a_side_road_light_is_ignored():
  ms, _ = make(flags=('tlsc',))
  r = ms.update(sm(lights=[('red', 40.0, 1.0)]), 15.0, 25.0, None, False, 0.05)
  assert abs(r.cap - math.sqrt(2.0 * 1.5 * (40.0 - 3.0))) < 1e-2 and r.tlsc_distance == 40.0
  assert ms.update(sm(lights=[('green', 40.0, 1.0)]), 15.0, 25.0, None, False, 0.05).cap is None          # confirmed green releases immediately
  ms, _ = make(flags=('tlsc',))
  assert ms.update(sm(lights=[('red', 40.0, 12.0)]), 15.0, 25.0, None, False, 0.05).cap is None          # a signal for another road
  ms, _ = make(flags=('tlsc',))
  assert ms.update(sm(lights=[('green', 40.0, 1.0)]), 15.0, 25.0, None, False, 0.05).cap is None
  assert ms.update(sm(lights=[('red', 40.0, 1.0)], lead=True), 15.0, 25.0, None, False, 0.05).cap is None  # a lead is ACC's job


def test_light_target_is_held_briefly_between_object_messages():
  ms, clk = make(flags=('tlsc',))
  ms.update(sm(lights=[('red', 40.0, 1.0)]), 15.0, 25.0, None, False, 0.05)
  clk.t += 0.3
  assert ms.update(sm(lights=[]), 15.0, 25.0, None, False, 0.05).cap is not None
  clk.t += 0.4
  assert ms.update(sm(lights=[]), 15.0, 25.0, None, False, 0.05).cap is None


def test_tlsc_clears_immediately_on_gas_or_lead_even_without_fresh_stereo_objects():
  ms, clk = make(flags=('tlsc',))
  r0 = ms.update(sm(lights=[('red', 20.0, 0.0)]), 10.0, 25.0, None, False, 0.05)
  assert r0.cap is not None and r0.tlsc_distance == 20.0
  # Next frame stereoObjects is invalid, driver presses gas -> must clear immediately, not hold 0.5 s.
  s_gas = sm(lights=[], gas=True)
  s_gas.valid['stereoObjects'] = False
  r_gas = ms.update(s_gas, 10.0, 25.0, None, False, 0.05)
  assert r_gas.cap is None and r_gas.tlsc_distance is None
  # Same for a new real lead appearing while stereoObjects is invalid.
  ms.update(sm(lights=[('red', 20.0, 0.0)]), 10.0, 25.0, None, False, 0.05)
  s_lead = sm(lights=[], lead=True)
  s_lead.valid['stereoObjects'] = False
  r_lead = ms.update(s_lead, 10.0, 25.0, None, False, 0.05)
  assert r_lead.cap is None and r_lead.tlsc_distance is None


def test_tlsc_expires_if_stereo_objects_producer_dies():
  ms, clk = make(flags=('tlsc',))
  s_live = sm(lights=[('red', 25.0, 0.0)])
  s_live.alive = {'stereoObjects': True}
  assert ms.update(s_live, 12.0, 25.0, None, False, 0.05).cap is not None
  # Producer stops (alive=False, valid still True): hold must expire after LIGHT_HOLD_S instead of refreshing forever.
  s_dead = sm(lights=[('red', 25.0, 0.0)])
  s_dead.alive = {'stereoObjects': False}
  clk.t += 0.3
  assert ms.update(s_dead, 12.0, 25.0, None, False, 0.05).cap is not None
  clk.t += 0.3
  r_exp = ms.update(s_dead, 12.0, 25.0, None, False, 0.05)
  assert r_exp.cap is None and r_exp.tlsc_distance is None


def test_ddsc_input_comes_from_the_driver_activity_monitor():
  assert distraction_status(0.9, 0.0).safeSpeedLimitMps == 0.0
  assert abs(distraction_status(0.2, 0.0).safeSpeedLimitMps - 16.67) < 1e-9 and not distraction_status(0.2, 0.0).unconsciousActive
  assert distraction_status(0.0, 25.0).unconsciousActive
  ms, _ = make(flags=('ddsc',))
  assert ms.update(sm(awareness=0.9), 25.0, 30.0, None, False, 0.05).cap is None
  assert abs(ms.update(sm(awareness=0.2, v=25.0), 25.0, 30.0, None, False, 0.05).cap - 22.2) < 1e-2     # highway latch: 80 km/h
  assert ms.update(sm(awareness=0.2, gas=True), 25.0, 30.0, None, False, 0.05).cap is None               # gas press cancels it


def test_the_lowest_cap_wins_and_it_is_never_negative():
  ms, _ = make()
  r = ms.update(sm(curve(), updated=True, lights=[('red', 40.0, 1.0)], awareness=0.2), 15.0, 25.0, None, False, 0.05)
  assert r.cap == min(r.sources.values()) and set(r.sources) >= {'tlsc', 'ddsc'} and r.cap >= 0.0


def test_rcsc_cap_comes_from_a_surface_source_and_is_off_without_one():
  ms, clk = make(flags=('rcsc',))
  s = sm()
  assert ms.update(s, 25.0, 25.0, None, False, 0.05).cap is None                     # NGP10: no surfaceStatus, nothing to say
  s['surfaceStatus'] = NS(hasSurfaceQuality=True, surfaceQuality=NS(score=0.55, texture='x'))
  s.valid['surfaceStatus'] = True
  caps = []
  for _ in range(8):
    clk.t += 0.05
    caps.append(ms.update(s, 25.0, 25.0, None, False, 0.05).cap)
  assert caps[-1] == 12.0 and caps[-1] is not None                                    # wet road: 12 m/s, from the first active cycle


def test_curves_from_path_starts_at_our_position_and_adds_distance_along_the_path():
  from exopilot.selfdrive.controls.lib.curve_speed import curves_from_path
  _, ext = mapd(curve_at=300, k=0.01)
  ext.position = NS(latitude=100.0 / M_PER_DEG_LAT, longitude=0.0)        # already 100 m along: the first two points are behind us
  curves = curves_from_path(ext.position, ext.path)
  assert len(curves) == 9 and abs(curves[0][0]) < 1e-6
  d, k = next(c for c in curves if c[1] > 0)
  assert abs(d - 200.0) < 0.5 and k == 0.01
  assert curves_from_path(ext.position, []) == []


def with_nav(s, nav_ms):
  s['navInstruction'] = NS(speedLimit=nav_ms)
  s.valid['navInstruction'] = True
  return s


def make_limits(flags=('mslc', 'nslc'), policy=None, offsets=None, confirm=()):
  clk = Clock()
  bools = {MAP_SPEED_KEYS[k]: True for k in flags}
  bools.update(dict.fromkeys(confirm, True))
  values = {"SpeedLimitPolicy": policy}
  values.update({f"SLCOffset{i + 1}": v for i, v in enumerate(offsets or [])})
  return MapSpeed(lambda k: bools.get(k, False), values.get, clk), clk


def test_map_and_navigation_limits_are_one_speed_limit_lowest_by_default():
  ms, _ = make_limits()
  r = ms.update(with_nav(sm(mapd(limit_ms=22.22), updated=True, v=20.0), 16.67), 20.0, 30.0, None, False, 0.05)
  assert abs(r.mslc - 22.22) < 0.01 and abs(r.nslc - 16.67) < 1e-6
  assert r.limit.source.name == 'NAVIGATION' and r.sources == {'speed_limit': r.nslc} and r.cap == r.nslc


def test_policy_map_ignores_the_navigation_limit():
  ms, _ = make_limits(policy=2)
  r = ms.update(with_nav(sm(mapd(limit_ms=22.22), updated=True, v=20.0), 16.67), 20.0, 30.0, None, False, 0.05)
  assert r.limit.source.name == 'MAP' and abs(r.cap - 22.22) < 0.01


def test_policy_none_applies_no_limit():
  ms, _ = make_limits(policy=0)
  r = ms.update(with_nav(sm(mapd(limit_ms=22.22), updated=True, v=20.0), 16.67), 20.0, 30.0, None, False, 0.05)
  assert r.limit is None and r.cap is None


def test_offsets_apply_to_both_sources_by_posted_limit():
  # 60 km/h sits in the 59-78 km/h bucket (SLCOffset4)
  ms, _ = make_limits(flags=('nslc',), offsets=[0, 0, 0, 10])
  r = ms.update(with_nav(sm(v=15.0), 60 / 3.6), 15.0, 30.0, None, False, 0.05)
  assert abs(r.cap - 70 / 3.6) < 1e-6
  ms, _ = make_limits(flags=('mslc',), offsets=[0, 0, 0, 10])
  r = ms.update(sm(mapd(limit_ms=60 / 3.6), updated=True, v=15.0), 15.0, 30.0, None, False, 0.05)
  assert abs(r.cap - 70 / 3.6) < 0.01


def test_a_lower_navigation_limit_waits_for_confirmation_when_asked():
  ms, clk = make_limits(flags=('nslc',), confirm=("SLCConfirmLower",))
  assert abs(ms.update(with_nav(sm(v=20.0), 80 / 3.6), 20.0, 30.0, None, False, 0.05).cap - 80 / 3.6) < 1e-6
  clk.t += 1.0
  assert abs(ms.update(with_nav(sm(v=20.0), 50 / 3.6), 20.0, 30.0, None, False, 0.05).cap - 80 / 3.6) < 1e-6
  clk.t += 1.0
  assert abs(ms.update(with_nav(sm(v=20.0, gas=True), 50 / 3.6), 20.0, 30.0, None, False, 0.05).cap - 50 / 3.6) < 1e-6


def test_learned_curve_speed_is_used_where_one_is_given():
  clk = Clock()
  bools = {MAP_SPEED_KEYS['mtsc']: True}
  ms = MapSpeed(lambda k: bools.get(k, False), lambda k: None, clk, learned_speed=lambda lat, lon, k: 12.0)
  r = ms.update(sm(curve(), updated=True), 25.0, 25.0, None, False, 0.05)
  assert r.mtsc_using_learned and r.cap is not None

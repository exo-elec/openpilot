from types import SimpleNamespace as NS

from nagaspilot.runtime.map_speed import MAP_HOLD_S, MapSpeed, distraction_status
from nagaspilot.runtime.feature_keys import EOP_MAP_KEYS

XS = [float(i * 6) for i in range(33)]


class Clock:
  t = 100.0

  def __call__(self):
    return self.t


def make(flags=('mtsc', 'mslc', 'tlsc', 'ddsc')):
  clk = Clock()
  bools = {EOP_MAP_KEYS[k]: True for k in flags}
  return MapSpeed(lambda k: bools.get(k, False), lambda k: "", clk), clk


def sm(map_data=None, updated=False, lights=(), lead=False, awareness=1.0, gas=False, v=25.0, vlead=20.0):
  s = type('S', (dict,), {})({
    'carState': NS(gasPressed=gas, vEgo=v, standstill=False),
    'radarState': NS(leadOne=NS(status=lead, vLead=vlead)),
    'mapData': map_data or NS(), 'stereoObjects': NS(objects=[NS(obstacleType='trafficLight', trafficLightState=st, trafficLightConfidence=0.5, dRel=d, yRel=y) for st, d, y in lights]),
    'driverMonitoringState': NS(awarenessStatus=awareness),
    'modelV2': NS(position=NS(x=XS, y=[0.0] * 33))})
  s.updated = {'mapData': updated}
  s.valid = {'carState': True, 'radarState': True, 'mapData': map_data is not None, 'stereoObjects': True, 'driverMonitoringState': True, 'modelV2': True}
  return s


def curve(x=300.0, y=0.004):
  return NS(upcomingCurvatureDEPRECATED=[NS(x=x, y=y)], speedLimit=0.0, nextSpeedLimit=0.0, nextSpeedLimitDistance=0.0)


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
  md = NS(upcomingCurvatureDEPRECATED=[], speedLimit=80.0, nextSpeedLimit=0.0, nextSpeedLimitDistance=0.0)
  r = ms.update(sm(md, updated=True), 20.0, 30.0, None, False, 0.05)
  assert abs(r.cap - 80.0 / 3.6) < 1e-6 and r.cap <= 30.0 + 1e-9


def test_traffic_light_on_our_path_stops_and_a_side_road_light_is_ignored():
  ms, _ = make(flags=('tlsc',))
  assert abs(ms.update(sm(lights=[('red', 40.0, 1.0)]), 15.0, 25.0, None, False, 0.05).cap - 10.954) < 1e-2
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


def test_rcd_cap_comes_from_a_surface_source_and_is_off_without_one():
  ms, clk = make(flags=('rcd',))
  s = sm()
  assert ms.update(s, 25.0, 25.0, None, False, 0.05).cap is None                     # NGP10: no surfaceStatus, nothing to say
  s['surfaceStatus'] = NS(hasSurfaceQuality=True, surfaceQuality=NS(score=0.55, texture='x'))
  s.valid['surfaceStatus'] = True
  caps = []
  for _ in range(8):
    clk.t += 0.05
    caps.append(ms.update(s, 25.0, 25.0, None, False, 0.05).cap)
  assert caps[-1] == 12.0 and caps[-1] is not None                                    # wet road: 12 m/s, from the first active cycle

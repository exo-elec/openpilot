from types import SimpleNamespace as NS

from nagaspilot.controls.ngp_path_selector import PObj
from nagaspilot.runtime.nudge_extras import NudgeExtras, boundaries_from_model
from nagaspilot.runtime.pathd import Extras

XS = [float(i * 6) for i in range(33)]


def model(left=-1.8, right=1.8, pl=0.9, pr=0.9):
  line = lambda y: NS(x=XS, y=[y] * 33)
  return NS(laneLines=[line(-3.6), line(left), line(right), line(3.6)], laneLineProbs=[0.9, pl, pr, 0.9])


def test_boundaries_from_model_y_right_frame_and_distrust():
  l, r = boundaries_from_model(model(-1.5, 2.1))
  assert len(l) == len(r) == 7 and l[0] == -1.5 and r[0] == 2.1
  assert boundaries_from_model(model(pl=0.2)) is None and boundaries_from_model(model(pr=0.2)) is None
  assert boundaries_from_model(NS()) is None
  assert boundaries_from_model(model(left=0.5)) is None            # a left boundary on the right side: nonsense


def run(n=20, objs=(), mdl=None, v=25.0, **kw):
  ne = NudgeExtras(**kw)
  out = None
  for _ in range(n):
    out = ne.update(mdl or model(), list(objs), v)
  return out


def test_clear_road_asks_for_nothing():
  e = run()
  assert isinstance(e, Extras) and e.offset_m is None and e.speed_factor is None


def test_lane_edge_obstacle_gives_a_lateral_nudge_in_the_left_positive_frame():
  right_obstacle = PObj(1, 'car', 10.0, -1.0, 0.0, 0.0, 0.9)       # 1 m right of us (left positive: -1.0), inside the lane
  e = run(objs=[right_obstacle], v=15.0)                              # LatNudge is off above 80 km/h by design
  assert e.offset_m is not None and e.offset_m > 0                  # nudge left, away from it
  assert run(objs=[right_obstacle], v=25.0).offset_m is None
  left_obstacle = PObj(1, 'car', 10.0, 1.0, 0.0, 0.0, 0.9)
  assert run(objs=[left_obstacle], v=15.0).offset_m < 0
  assert run(objs=[right_obstacle], mdl=model(pl=0.1), v=15.0).offset_m is None   # unseen line: no lateral nudge


def test_in_lane_lead_trim_and_the_distance_scale_fix():
  lead = PObj(1, 'car', 25.0, 0.0, -6.0, 0.0, 0.9)                  # closing at 6 m/s, 25 m ahead (ratio 0.31)
  fixed, legacy = run(objs=[lead]), run(objs=[lead], legacy_scale_bug=True)
  assert legacy.speed_factor is None                                # EOP10's lookup: the reduction never applies
  assert fixed.speed_factor is not None and 0.8 <= fixed.speed_factor < 1.0     # the corrected scale asks for a trim


def test_host_uses_the_extras_provider_when_no_extras_are_passed():
  from nagaspilot.runtime.object_sources import GriddSource
  from nagaspilot.runtime.pathd import SharedPathdHost
  calls = []

  class PM:
    sent = []

    def send(self, n, m):
      PM.sent.append(m)

  class Params:
    def get(self, k):
      return 0

  class PA(NS):
    def init(self, name, n):
      setattr(self, name, [0.0] * n)
      return getattr(self, name)

  h = SharedPathdHost(Params(), PM(), GriddSource(), new_message=lambda name, valid=True: NS(pathAdjust=PA(), valid=valid),
                      extras_provider=lambda sm, objs, v: calls.append(v) or Extras(offset_m=0.3, speed_factor=0.9))
  sm = type('S', (dict,), {})({'modelV2': NS(frameId=1, laneLines=[NS(x=XS, y=[0.0] * 33)] * 4, laneLineProbs=[0.9] * 4, position=NS(x=XS, y=[0.0] * 33),
                                           action=NS(desiredCurvature=0.0, desiredAcceleration=0.0)),
                               'carState': NS(vEgo=25.0, cruiseState=NS(speed=25.0), steeringPressed=False, brakePressed=False, gasPressed=False),
                               'radarState': NS(leadOne=NS(status=False), leadTwo=NS(status=False)), 'stereoObjects': NS(objects=[])})
  sm.updated, sm.valid, sm.alive = {'modelV2': True}, {'modelV2': True, 'carState': True, 'stereoObjects': True, 'radarState': True}, {'stereoObjects': True}
  assert h.tick(sm) is True and calls == [25.0]
  pa = PM.sent[-1].pathAdjust
  assert abs(pa.speedFactor - 0.9) < 1e-9

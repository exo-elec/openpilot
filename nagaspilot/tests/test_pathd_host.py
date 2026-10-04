from types import SimpleNamespace as NS

from nagaspilot.controls.ngp_path_selector import Selection
from nagaspilot.runtime.object_sources import GriddSource
from nagaspilot.runtime.pathd import Extras, SharedPathdHost, merge_extras

XS = [float(i * 6) for i in range(33)]


def model():
  line = lambda y: NS(x=XS, y=[y] * 33)
  return NS(laneLines=[line(-3.6), line(-1.8), line(1.8), line(3.6)], laneLineProbs=[0.9] * 4, frameId=11,
            position=NS(x=XS, y=[0.0] * 33), action=NS(desiredCurvature=0.0, desiredAcceleration=0.0))


class PA(NS):
  def init(self, name, n):
    setattr(self, name, [0.0] * n)
    return getattr(self, name)


class PM:
  def __init__(self):
    self.sent = []

  def send(self, name, msg):
    self.sent.append((name, msg))


class Params:
  def __init__(self, ceiling=0):
    self.c = ceiling

  def get(self, k):
    return self.c


def sm_for(objs, mono=True):
  sm = type('S', (dict,), {})({'modelV2': model(), 'carState': NS(vEgo=25.0, cruiseState=NS(speed=25.0), steeringPressed=False, brakePressed=False, gasPressed=False),
                              'radarState': NS(leadOne=NS(status=False), leadTwo=NS(status=False)), 'stereoObjects': NS(objects=objs)})
  sm.updated = {'modelV2': True}
  sm.valid = {'modelV2': True, 'carState': True, 'stereoObjects': True, 'radarState': True}
  sm.alive = {'stereoObjects': True}
  return sm


def host(ceiling=0):
  pm = PM()
  h = SharedPathdHost(Params(ceiling), pm, GriddSource(), new_message=lambda name, valid=True: NS(pathAdjust=PA(), valid=valid))
  return h, pm


def g(y, vyRel=0.0, obstacle='vehicle', d=0.0):
  return NS(trackId=1, obstacleType=obstacle, prob=0.9, dRel=d, yRel=y, vRel=0.0, vyRel=vyRel)


def test_tick_publishes_with_the_gridd_source_and_gates_on_modelV2():
  h, pm = host()
  assert h.tick(sm_for([g(-2.6, obstacle='vehicle')])) is True
  name, msg = pm.sent[-1]
  assert name == 'pathAdjust' and msg.pathAdjust.frameId == 11 and msg.pathAdjust.numObjects == 1
  sm = sm_for([])
  sm.updated['modelV2'] = False
  assert h.tick(sm) is False and len(pm.sent) == 1


def test_idle_ceiling_publishes_mode_zero_and_a_positive_ceiling_runs_the_rule_channel():
  h, pm = host(0)
  h.tick(sm_for([]))
  assert pm.sent[-1][1].pathAdjust.dppMode == 0
  h, pm = host(5)
  h._ceiling_t = -10.0
  h.tick(sm_for([]))
  assert pm.sent[-1][1].pathAdjust.ruleValid is True and pm.sent[-1][1].pathAdjust.dppMode >= 1


def test_extras_merge_tighten_only_with_the_same_rules():
  room = (0.5, 0.5)
  sel = Selection(0.2, 1.0, None, 0.0, 'nudge')
  m = merge_extras(sel, room, Extras(offset_m=0.4, speed_factor=0.9))
  assert m.offset_m == 0.4 and m.speed_factor == 0.9 and m.reason == 'slow'          # same side: the larger offset wins; the lower speed wins
  m = merge_extras(sel, room, Extras(offset_m=-0.3))
  assert m.offset_m == 0.0                                                             # opposite sides conflict: policy path
  m = merge_extras(sel, room, Extras(speed_factor=2.0))
  assert m.speed_factor == 1.0 and m.offset_m == 0.2                                   # an extra can never raise speed
  assert merge_extras(sel, room, None) is sel and merge_extras(sel, room, Extras()) is sel
  m = merge_extras(sel, (0.1, 0.5), Extras(offset_m=0.5))
  assert m.offset_m == 0.1                                                             # bounded by the room


def test_extras_flow_into_the_published_message():
  h, pm = host()
  h.tick(sm_for([]), Extras(offset_m=0.3, speed_factor=0.85))
  pa = pm.sent[-1][1].pathAdjust
  assert abs(pa.offsetM - 0.3) < 1e-9 and abs(pa.speedFactor - 0.85) < 1e-9


def test_extras_from_eop_flips_the_lateral_frame_and_keeps_the_speed_contract():
  from nagaspilot.runtime.pathd import extras_from_eop
  e = extras_from_eop(-2.5, -0.5, [0.3, 0.2], 25.0)
  assert abs(e.offset_m + 0.3) < 1e-9                                 # +0.3 right in EOP's frame is -0.3 in the left-positive frame
  assert abs(e.speed_factor - (1.0 - 3.0 / 25.0)) < 1e-9
  e = extras_from_eop(float('inf'), 0.0, [0.0], 25.0)
  assert e.offset_m is None and e.speed_factor is None               # nothing asked
  assert extras_from_eop(-30.0, 0.0, [], 25.0).speed_factor == 0.8    # a hard emergency still only asks 20 % here
  assert extras_from_eop(-1.0, 0.0, [float('nan')], 25.0).offset_m is None

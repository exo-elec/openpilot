from types import SimpleNamespace as NS

from nagaspilot.controls.ngp_path_selector import PObj
from nagaspilot.controls.ngp_policy_arbiter import Mode
from nagaspilot.runtime.pathd import PathD, fill_path_adjust
from nagaspilot.runtime.rule_channel import RuleChannel, RuleChannelConsumer, apply_accel, apply_curvature, lane_polyline

XS = [float(i * 6) for i in range(33)]


def model(left=-1.8, right=1.8, pl=0.9, pr=0.9, curv=0.0, acc=0.0):
  line = lambda y: NS(x=XS, y=[y] * 33)
  return NS(laneLines=[line(-3.6), line(left), line(right), line(3.6)], laneLineProbs=[0.9, pl, pr, 0.9],
            position=NS(x=XS, y=[0.0] * 33), action=NS(desiredCurvature=curv, desiredAcceleration=acc))


def step(ch, objs=(), ceiling=5, mdl=None, v=25.0, setv=25.0, ok=True, drv=False, n=1, **kw):
  out = None
  for _ in range(n):
    out = ch.step(v, setv, mdl or model(), list(objs), [], (0.5, 0.5), ok, drv, ceiling, 0.05, **kw)
  return out


def test_lane_polyline_centre_flip_and_fallbacks():
  p, conf = lane_polyline(model(left=-1.2, right=2.4))          # model y-right: centre is 0.6 to the RIGHT -> -0.6 left-positive
  assert abs(p.y_at(30.0) + 0.6) < 1e-9 and conf == 0.9
  p, conf = lane_polyline(model(pl=0.2))                        # unseen line: fall back to the planned path
  assert p is not None and conf == 0.0 and p.y_at(30.0) == 0.0
  assert lane_polyline(NS()) == (None, 0.0)


def test_idle_ceiling_means_no_rule_channel_and_mode_zero():
  out = step(RuleChannel(), ceiling=0)
  assert out.mode == 0 and not out.cmd.valid and out.case == 'off'


def test_cut_in_case_selects_primary_long_inside_the_ceiling_and_cruise_stays_shadow():
  ch = RuleChannel()
  cut = [PObj(1, 'car', 22.0, 3.0, -6.0, -1.5, 0.9)]
  out = step(ch, cut, n=10)
  assert out.mode == int(Mode.PRIMARY_LONG) and out.case == 'cut_in'
  assert step(RuleChannel(), cut, ceiling=2, n=10).mode == int(Mode.SUPERVISE)        # ceiling caps it
  assert step(RuleChannel(), [], n=10).mode == int(Mode.SHADOW)


def test_unhealthy_perception_or_driver_override_drops_the_mode():
  ch = RuleChannel()
  cut = [PObj(1, 'car', 22.0, 3.0, -6.0, -1.5, 0.9)]
  step(ch, cut, n=10)
  assert step(ch, cut, ok=False).mode == 0
  ch = RuleChannel()
  step(ch, cut, n=10)
  assert step(ch, cut, drv=True, n=10).mode <= int(Mode.SHADOW)


def test_sustained_policy_disagreement_pulls_dpp_back_to_shadow():
  ch = RuleChannel()
  cut = [PObj(1, 'car', 22.0, 3.0, -6.0, -1.5, 0.9)]
  out = step(ch, cut, mdl=model(acc=1.5), n=30)                 # the policy wants to accelerate, the rule channel brakes
  assert out.disagree and out.mode == int(Mode.SHADOW) and out.case == 'disagree'


class SM(dict):
  def __init__(self, mode, alive=True, valid=True, **kw):
    super().__init__({'pathAdjust': NS(dppMode=mode, ruleValid=True, ruleCurvature=kw.get('rc', 0.01), ruleAccel=kw.get('ra', -2.0)),
                      'carState': NS(brakePressed=False, gasPressed=False)})
    self.alive, self.valid = {'pathAdjust': alive}, {'pathAdjust': valid}


def test_consumers_pass_the_policy_through_without_a_fresh_message_or_authority():
  c = RuleChannelConsumer()
  assert apply_curvature(c, SM(4, alive=False), 0.002, True, False, 0.01) == 0.002
  assert apply_curvature(c, SM(1), 0.002, True, False, 0.01) == 0.002                    # shadow
  assert apply_curvature(c, SM(4), 0.002, False, False, 0.01) == 0.002                   # lateral not active
  assert apply_accel(c, SM(4, alive=False), 0.5, 0.05) == 0.5
  assert apply_accel(c, SM(4), 0.5, 0.05) == 0.5                                         # primary_lat has no longitudinal authority
  assert apply_accel(c, SM(1), 0.5, 0.05) == 0.5


def test_consumers_apply_authority_slewed_and_driver_wins():
  c = RuleChannelConsumer()
  v = apply_curvature(c, SM(4), 0.0, True, False, 0.01)
  assert 0.0 < v <= 0.004 * 0.01 + 1e-12                                                 # slewed correction toward the rule curvature
  assert apply_curvature(c, SM(4), 0.0, True, True, 0.01) == 0.0                         # driver override
  a = apply_accel(c, SM(2), 0.5, 0.05)
  assert a == -2.0                                                                       # supervise: min(policy, rule)
  a = apply_accel(RuleChannelConsumer(), SM(3, ra=-2.0), 0.5, 0.05)
  assert 0.3 < a < 0.5                                                                   # primary_long: correction builds at the jerk limit


def test_message_fields_filled_for_the_new_rule_channel():
  class B:
    def init(self, name, n):
      setattr(self, name, [0.0] * n)
      return getattr(self, name)

  d = PathD()
  sm = SM(3)
  sm.update({'monoDetections': NS(detections=[], modelExecutionTime=0.02), 'modelV2': model()})
  sm.alive['monoDetections'] = sm.valid['monoDetections'] = sm.valid['modelV2'] = True
  sel, room, n = d.step(sm, 25.0)
  rule = d.step_rule(sm, 25.0, 25.0, False, 5, 0.05)
  pa = B()
  fill_path_adjust(pa, sel, room, n, 3, 25.0, rule)
  assert pa.ruleValid is True and pa.dppMode == rule.mode and pa.dppCase == rule.case and abs(pa.ruleAccel - rule.cmd.accel) < 1e-6

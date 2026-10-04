from types import SimpleNamespace as NS

from nagaspilot.controls.ngp_soc import NGPSOC, SOCInput, curvature_bias, threats_from


def _lines(width=3.2):
  ys = [-1.5 * width, -0.5 * width, 0.5 * width, 1.5 * width]
  return tuple(tuple([y] * 10) for y in ys)


def _sample(left=False, right=False, v=30.0, probs=(0.9,) * 4, stds=(0.1,) * 4, lines=None):
  return SOCInput(v, left, right, lines or _lines(), probs, stds)


def _run(soc, sample, frames):
  result = None
  for _ in range(frames):
    result = soc.update(sample)
  return result


def test_nothing_happens_before_confirmation_or_without_one_sided_threat():
  assert _run(NGPSOC(), _sample(left=True), 19).offset_m == 0.0
  assert _run(NGPSOC(), _sample(left=True, right=True), 100).offset_m == 0.0
  assert _run(NGPSOC(), _sample(), 100).offset_m == 0.0


def test_gates_on_speed_and_lane_geometry():
  assert _run(NGPSOC(), _sample(left=True, v=15.0), 100).offset_m == 0.0
  assert _run(NGPSOC(), _sample(left=True, probs=(0.9, 0.5, 0.9, 0.9)), 100).offset_m == 0.0
  assert _run(NGPSOC(), _sample(left=True, lines=_lines(width=4.5)), 100).offset_m == 0.0


def test_offset_is_away_from_the_threat_and_ramps_slowly():
  left = _run(NGPSOC(), _sample(left=True), 21)
  assert left.active_suggestion and -0.2 < left.offset_m < 0.0  # moving right, still ramping
  full = _run(NGPSOC(), _sample(left=True), 200)
  assert abs(full.offset_m + NGPSOC.OFFSET_M) < 1e-9
  right = _run(NGPSOC(), _sample(right=True), 200)
  assert abs(right.offset_m - NGPSOC.OFFSET_M) < 1e-9


def test_release_is_gradual_not_a_step():
  soc = NGPSOC()
  _run(soc, _sample(left=True), 200)
  after = soc.update(_sample())
  assert after.offset_m < 0.0 and abs(after.offset_m) > 0.19  # one frame later it has barely moved
  assert _run(soc, _sample(), 100).offset_m == 0.0


def test_curvature_bias_follows_the_sign_convention():
  soc = NGPSOC()
  assert curvature_bias(_run(soc, _sample(left=True), 200)) < 0  # threat left -> bias right (negative)
  assert curvature_bias(_run(NGPSOC(), _sample(right=True), 200)) > 0
  assert curvature_bias(_run(NGPSOC(), _sample(), 5)) == 0.0


def test_threats_from_blindspot_flags_and_neighbouring_lane_leads():
  none = NS(leftBlindspot=False, rightBlindspot=False)
  assert threats_from(none, NS(leadsV3=[])) == (False, False)
  assert threats_from(NS(leftBlindspot=True, rightBlindspot=False), NS(leadsV3=[])) == (True, False)
  # modelV2 y is right-positive: y = -3 is the LEFT lane
  left_lead = NS(prob=0.9, x=[15.0], y=[-3.0])
  right_lead = NS(prob=0.9, x=[15.0], y=[3.0])
  assert threats_from(none, NS(leadsV3=[left_lead])) == (True, False)
  assert threats_from(none, NS(leadsV3=[right_lead])) == (False, True)
  assert threats_from(none, NS(leadsV3=[NS(prob=0.9, x=[15.0], y=[0.2])])) == (False, False)  # same lane
  assert threats_from(none, NS(leadsV3=[NS(prob=0.2, x=[15.0], y=[-3.0])])) == (False, False)
  assert threats_from(none, NS(leadsV3=[NS(prob=0.9, x=[60.0], y=[-3.0])])) == (False, False)  # too far

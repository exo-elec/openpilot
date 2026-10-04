from nagaspilot.controls.ngp_dpp import DEESCALATE_S, DPP, Situation, classify
from nagaspilot.controls.ngp_policy_arbiter import Mode


def sit(**kw):
  base = dict(v_ego=25.0, perception_ok=True, rule_valid=True, lane_conf=0.9, curvature_ahead=0.001, lead_gap_m=None,
              cut_in_risk=False, vru_alongside=False, large_alongside=False, disagree=False)
  base.update(kw)
  return Situation(**base)


def run(dpp, s, ceiling, seconds, dt=0.05):
  d = None
  for _ in range(int(seconds / dt)):
    d = dpp.update(s, ceiling, dt)
  return d


def test_cases_map_to_modes_within_the_ceiling():
  assert classify(sit(), Mode.PRIMARY_BOTH)[:2] == (Mode.SHADOW, 'cruise')
  assert classify(sit(cut_in_risk=True), Mode.PRIMARY_BOTH)[:2] == (Mode.PRIMARY_LONG, 'cut_in')
  assert classify(sit(cut_in_risk=True), Mode.SUPERVISE)[0] == Mode.SUPERVISE            # never above the ceiling
  assert classify(sit(lead_gap_m=20.0), Mode.PRIMARY_BOTH)[:2] == (Mode.SUPERVISE, 'close_lead')   # 0.8 s headway
  assert classify(sit(lead_gap_m=60.0), Mode.PRIMARY_BOTH)[1] == 'cruise'
  assert classify(sit(vru_alongside=True), Mode.PRIMARY_BOTH)[:2] == (Mode.PRIMARY_LAT, 'alongside')
  assert classify(sit(large_alongside=True, lane_conf=0.3), Mode.PRIMARY_BOTH)[:2] == (Mode.SUPERVISE, 'alongside_lanes_weak')
  assert classify(sit(curvature_ahead=0.02), Mode.PRIMARY_BOTH)[1] == 'weak_lanes_or_curve'


def test_degraded_disagree_driver_lowspeed_and_off():
  assert classify(sit(perception_ok=False), Mode.PRIMARY_BOTH)[:2] == (Mode.OFF, 'degraded')
  assert classify(sit(rule_valid=False), Mode.PRIMARY_BOTH)[:2] == (Mode.OFF, 'degraded')
  assert classify(sit(disagree=True, cut_in_risk=True), Mode.PRIMARY_BOTH)[1] == 'disagree'
  assert classify(sit(driver_override=True, cut_in_risk=True), Mode.PRIMARY_BOTH)[1] == 'driver'
  assert classify(sit(v_ego=5.0, cut_in_risk=True), Mode.PRIMARY_BOTH)[1] == 'low_speed'
  assert classify(sit(cut_in_risk=True), Mode.OFF)[:2] == (Mode.OFF, 'off')


def test_escalates_quickly_and_deescalates_slowly():
  d = DPP()
  assert run(d, sit(), Mode.PRIMARY_BOTH, 1.0).mode == Mode.SHADOW
  assert run(d, sit(cut_in_risk=True), Mode.PRIMARY_BOTH, 0.2).mode == Mode.SHADOW       # not yet
  assert run(d, sit(cut_in_risk=True), Mode.PRIMARY_BOTH, 0.3).mode == Mode.PRIMARY_LONG
  assert run(d, sit(), Mode.PRIMARY_BOTH, DEESCALATE_S - 0.5).mode == Mode.PRIMARY_LONG  # dwell holds it
  assert run(d, sit(), Mode.PRIMARY_BOTH, 1.0).mode == Mode.SHADOW


def test_urgent_drops_are_immediate_and_a_flicker_does_not_change_the_mode():
  d = DPP()
  run(d, sit(cut_in_risk=True), Mode.PRIMARY_BOTH, 1.0)
  assert d.mode == Mode.PRIMARY_LONG
  assert d.update(sit(perception_ok=False), Mode.PRIMARY_BOTH, 0.05).mode == Mode.OFF     # immediate
  d = DPP()
  run(d, sit(cut_in_risk=True), Mode.PRIMARY_BOTH, 1.0)
  for _ in range(20):
    d.update(sit(), Mode.PRIMARY_BOTH, 0.05)
    d.update(sit(cut_in_risk=True), Mode.PRIMARY_BOTH, 0.05)
  assert d.mode == Mode.PRIMARY_LONG


def test_lowering_the_ceiling_takes_effect_at_once():
  d = DPP()
  run(d, sit(cut_in_risk=True), Mode.PRIMARY_BOTH, 1.0)
  assert d.update(sit(cut_in_risk=True), Mode.SHADOW, 0.05).mode == Mode.SHADOW
  assert d.update(sit(cut_in_risk=True), 99, 0.05).mode <= Mode.PRIMARY_BOTH

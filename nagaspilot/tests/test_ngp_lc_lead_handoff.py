from types import SimpleNamespace

from cereal import log
from nagaspilot.controls.ngp_lc_lead_handoff import NGPLeadHandoff, LANE_CHANGE_SPEED_MIN

LaneChangeState = log.LaneChangeState
LaneChangeDirection = log.LaneChangeDirection


def radar_state(lead_one_status=False):
  lead_one = SimpleNamespace(status=lead_one_status, dRel=20.0, yRel=0.0, vRel=0.0)
  return SimpleNamespace(leadOne=lead_one, leadTwo=SimpleNamespace(status=False))


def model_v2_with_lead(x=10.0, y=3.0, v=15.0, a=0.0, prob=0.9):
  lead = SimpleNamespace(x=[x], y=[y], v=[v], a=[a], prob=prob)
  return SimpleNamespace(leadsV3=[lead])


def test_disabled_returns_original_radar_state():
  handoff = NGPLeadHandoff()
  base = radar_state()
  out = handoff.update(enabled=False, model_v2=model_v2_with_lead(), radar_state=base,
                        lc_state=LaneChangeState.laneChangeStarting, lc_dir=LaneChangeDirection.left,
                        v_ego=20.0, now=0.0)
  assert out is base


def test_below_speed_floor_returns_original_radar_state():
  handoff = NGPLeadHandoff()
  base = radar_state()
  out = handoff.update(enabled=True, model_v2=model_v2_with_lead(), radar_state=base,
                        lc_state=LaneChangeState.laneChangeStarting, lc_dir=LaneChangeDirection.left,
                        v_ego=LANE_CHANGE_SPEED_MIN - 1.0, now=0.0)
  assert out is base


def test_lane_change_starting_wraps_adjacent_left_lead():
  handoff = NGPLeadHandoff()
  base = radar_state()
  out = handoff.update(enabled=True, model_v2=model_v2_with_lead(x=12.0, y=3.0, v=18.0), radar_state=base,
                        lc_state=LaneChangeState.laneChangeStarting, lc_dir=LaneChangeDirection.left,
                        v_ego=20.0, now=0.0)
  assert out is not base
  assert out.leadOne.status
  assert out.leadOne.vLead == 18.0


def test_lane_change_starting_wraps_adjacent_right_lead():
  handoff = NGPLeadHandoff()
  base = radar_state()
  out = handoff.update(enabled=True, model_v2=model_v2_with_lead(x=12.0, y=-3.0, v=18.0), radar_state=base,
                        lc_state=LaneChangeState.laneChangeStarting, lc_dir=LaneChangeDirection.right,
                        v_ego=20.0, now=0.0)
  assert out is not base
  assert out.leadOne.status
  assert out.leadOne.vLead == 18.0


def test_lead_on_wrong_side_is_ignored():
  handoff = NGPLeadHandoff()
  base = radar_state()
  # direction is left, but lead is to the right (y < 0) -- should not be picked
  out = handoff.update(enabled=True, model_v2=model_v2_with_lead(y=-3.0), radar_state=base,
                        lc_state=LaneChangeState.laneChangeStarting, lc_dir=LaneChangeDirection.left,
                        v_ego=20.0, now=0.0)
  assert out is base


def test_low_probability_lead_is_ignored():
  handoff = NGPLeadHandoff()
  base = radar_state()
  out = handoff.update(enabled=True, model_v2=model_v2_with_lead(prob=0.1), radar_state=base,
                        lc_state=LaneChangeState.laneChangeStarting, lc_dir=LaneChangeDirection.left,
                        v_ego=20.0, now=0.0)
  assert out is base


def test_wrapped_state_demotes_real_current_lane_lead_to_lead_two():
  """The current-lane lead must still be visible (as leadTwo) so MPC keeps
  braking for it during the merge -- only leadOne is replaced."""
  handoff = NGPLeadHandoff()
  base = radar_state(lead_one_status=True)
  out = handoff.update(enabled=True, model_v2=model_v2_with_lead(), radar_state=base,
                        lc_state=LaneChangeState.laneChangeStarting, lc_dir=LaneChangeDirection.left,
                        v_ego=20.0, now=0.0)
  assert out is not base
  assert out.leadOne is not base.leadOne
  assert out.leadTwo is base.leadOne
  assert out.leadTwo.status


def test_lanechangefinishing_keeps_existing_handoff_active():
  """laneChangeFinishing must not drop an already-active handoff mid-merge."""
  handoff = NGPLeadHandoff()
  base = radar_state()
  handoff.update(enabled=True, model_v2=model_v2_with_lead(), radar_state=base,
                 lc_state=LaneChangeState.laneChangeStarting, lc_dir=LaneChangeDirection.left,
                 v_ego=20.0, now=0.0)
  during_finish = handoff.update(enabled=True, model_v2=model_v2_with_lead(), radar_state=base,
                                 lc_state=LaneChangeState.laneChangeFinishing, lc_dir=LaneChangeDirection.left,
                                 v_ego=20.0, now=0.5)
  assert during_finish is not base
  assert during_finish.leadOne.status


def test_handoff_ends_immediately_once_lane_change_state_goes_off():
  """Once lc_state leaves laneChangeStarting/Finishing, the wrapper stops
  being returned on the very next call -- _persist_until/_proxy retention
  (ported verbatim from EOP10) affects only internal state, not the return
  value, since self._active is cleared unconditionally in that branch."""
  handoff = NGPLeadHandoff()
  base = radar_state()
  handoff.update(enabled=True, model_v2=model_v2_with_lead(), radar_state=base,
                 lc_state=LaneChangeState.laneChangeStarting, lc_dir=LaneChangeDirection.left,
                 v_ego=20.0, now=0.0)
  after_off = handoff.update(enabled=True, model_v2=model_v2_with_lead(), radar_state=base,
                             lc_state=LaneChangeState.off, lc_dir=LaneChangeDirection.left,
                             v_ego=20.0, now=0.1)
  assert after_off is base

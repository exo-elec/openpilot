from nagaspilot.controls.ngp_alcc import ALCCInput, ALCCState, NGPALCC
from nagaspilot.controls.ngp_road_edge import evaluate_road_edges
from nagaspilot.controls.ngp_speed_policy import (
  NGPSpeedPolicy, SpeedLimitObservation, SpeedLimitPolicy, SpeedLimitSource,
)


def test_speed_policy_uses_map_nav_then_car_fallback_without_control():
  policy = NGPSpeedPolicy(SpeedLimitPolicy.MAP_NAV_WITH_CAR_FALLBACK)
  car = SpeedLimitObservation(SpeedLimitSource.CAR, 15.0)
  nav = SpeedLimitObservation(SpeedLimitSource.NAVIGATION, 20.0)
  result = policy.evaluate(25.0, 30.0, (car, nav))
  assert result.source is SpeedLimitSource.NAVIGATION
  assert result.suggested_cruise_mps == 20.0
  assert not result.control_applied
  fallback = policy.evaluate(10.0, 30.0, (car,))
  assert fallback.source is SpeedLimitSource.CAR


def test_alcc_latches_pauses_and_never_has_authority():
  alcc = NGPALCC()
  result = alcc.update(ALCCInput(True, engage_request=True))
  assert result.state is ALCCState.ENABLED
  assert result.active_suggestion
  assert not result.control_authority
  assert alcc.update(ALCCInput(True, pause_condition=True)).state is ALCCState.PAUSED
  assert alcc.update(ALCCInput(True)).state is ALCCState.ENABLED


def test_road_edge_gate_blocks_the_unreliable_side():
  edges = evaluate_road_edges((0.2, 0.9), (0.1, 0.9, 0.9, 0.8))
  assert edges.valid and edges.left_blocked and not edges.right_blocked

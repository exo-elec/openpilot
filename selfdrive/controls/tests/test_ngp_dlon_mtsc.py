from openpilot.selfdrive.controls.lib.ngp_dlon import DLONInput, NGPDLON
from openpilot.selfdrive.controls.lib.ngp_mtsc import MTSCState, NGPMTSC


def test_dlon_hysteresis_and_triggers():
  dlon = NGPDLON(enter_frames=2, exit_frames=2)
  sample = DLONInput(8.0, should_stop=True, traffic_control=True)
  result = dlon.evaluate(sample)
  assert result.triggers == ("low_speed", "stop_prediction", "traffic_control")
  assert not result.e2e_suggestion
  assert dlon.evaluate(sample).e2e_suggestion
  assert dlon.evaluate(DLONInput(25.0)).e2e_suggestion
  assert not dlon.evaluate(DLONInput(25.0)).e2e_suggestion


def test_dlon_fcw_is_immediate_but_shadow_only():
  result = NGPDLON().evaluate(DLONInput(20.0, mpc_fcw=True))
  assert result.e2e_suggestion


def test_mtsc_restrictive_curve_and_handover():
  mtsc = NGPMTSC()
  result = mtsc.update([(450.0, 0.01), (300.0, 0.03)])
  assert result.state is MTSCState.APPROACHING
  assert result.curvature == 0.03
  assert result.target_speed is not None
  result = mtsc.update([(150.0, 0.03)])
  assert result.state is MTSCState.HANDOVER
  assert result.target_speed is None


def test_mtsc_ignores_out_of_range_and_disable():
  mtsc = NGPMTSC()
  assert mtsc.update([(100.0, 0.1), (600.0, 0.1)]).state is MTSCState.DISABLED
  assert NGPMTSC(enabled=False).update([(300.0, 0.03)]).target_speed is None

from types import SimpleNamespace

from nagaspilot.controls.ngp_dlon import NGPDLON, NGPDLONMode, NGPDriveMode
from nagaspilot.controls.ngp_mtsc import MTSCState, NGPMTSC


class FakeSubMaster(dict):
  def __init__(self, *args, **kwargs):
    super().__init__(*args, **kwargs)
    self.valid = {"radarState": True, "navInstruction": False}


def make_dlon_sm(v_ego=5.0, should_stop=False, has_lead=False, gas_pressed=False):
  return FakeSubMaster({
    "carState": SimpleNamespace(vEgo=v_ego, gasPressed=gas_pressed, leftBlinker=False, rightBlinker=False),
    "modelV2": SimpleNamespace(action=SimpleNamespace(shouldStop=should_stop), orientationRate=SimpleNamespace(z=[0.0] * 10)),
    "radarState": SimpleNamespace(leadOne=SimpleNamespace(status=has_lead, vLead=v_ego)),
  })


def test_dlon_matches_eop_force_stop_debounce_and_gas_override():
  dlon = NGPDLON()
  dlon.update_params = lambda: None
  dlon.force_stops_enabled = True
  sm = make_dlon_sm(should_stop=True)
  assert not dlon.update(sm)["force_stop"]
  dlon.force_stop_timer = 1.0
  assert dlon.update(sm)["force_stop"]
  sm["carState"].gasPressed = True
  assert not dlon.update(sm)["force_stop"]


def test_dlon_matches_eop_chill_experimental_and_auto_modes():
  dlon = NGPDLON()
  dlon.update_params = lambda: None
  sm = make_dlon_sm()
  dlon.mode = NGPDLONMode.CHILL
  assert not dlon.update(sm)["e2e_enabled"]
  dlon.mode = NGPDLONMode.EXPERIMENTAL
  dlon._active = True
  dlon.mode_manager.current_mode = NGPDriveMode.E2E
  assert dlon.update(sm)["e2e_enabled"]


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

from types import SimpleNamespace

from nagaspilot.controls.ngp_dlon import NGPDLON


class FakeSubMaster(dict):
  def __init__(self, *args, **kwargs):
    super().__init__(*args, **kwargs)
    self.valid = {"radarState": True, "navInstruction": False}


def make_dlon_sm(v_ego=20.0, dlat_laneless=None, dlat_confidence=0.5, controls_state_valid=True):
  sm = FakeSubMaster({
    "carState": SimpleNamespace(vEgo=v_ego, gasPressed=False, leftBlinker=False, rightBlinker=False),
    "modelV2": SimpleNamespace(action=SimpleNamespace(shouldStop=False), orientationRate=SimpleNamespace(z=[0.0] * 10)),
    "radarState": SimpleNamespace(leadOne=SimpleNamespace(status=False, vLead=0.0)),
  })
  if dlat_laneless is not None:
    sm["controlsState"] = SimpleNamespace(ngpDlatUseLaneless=dlat_laneless, ngpDlatLaneConfidence=dlat_confidence)
    sm.valid["controlsState"] = controls_state_valid
  return sm


def test_missing_controls_state_is_neutral_not_e2e_favoring():
  dlon = NGPDLON()
  sm = make_dlon_sm()  # no controlsState published at all
  assert dlon.detect_lane_confidence_trigger(sm) is False


def test_stale_controls_state_is_neutral_not_e2e_favoring():
  dlon = NGPDLON()
  sm = make_dlon_sm(dlat_laneless=True, dlat_confidence=0.1, controls_state_valid=False)
  assert dlon.detect_lane_confidence_trigger(sm) is False


def test_dlat_laneless_favors_e2e():
  dlon = NGPDLON()
  sm = make_dlon_sm(dlat_laneless=True, dlat_confidence=0.2)
  assert dlon.detect_lane_confidence_trigger(sm) is True


def test_dlat_laneful_does_not_favor_e2e():
  dlon = NGPDLON()
  sm = make_dlon_sm(dlat_laneless=False, dlat_confidence=0.9)
  assert dlon.detect_lane_confidence_trigger(sm) is False


def test_auto_mode_switches_to_e2e_on_low_lane_confidence_alone():
  dlon = NGPDLON()
  sm = make_dlon_sm(dlat_laneless=True, dlat_confidence=0.15)
  assert dlon._evaluate_auto_mode(sm["carState"], sm["modelV2"], sm["radarState"], sm) is True


def test_lane_confidence_trigger_toggle_disables_coupling():
  dlon = NGPDLON()
  dlon._trigger_enabled["lane_confidence"] = False
  sm = make_dlon_sm(dlat_laneless=True, dlat_confidence=0.15)
  assert dlon._evaluate_auto_mode(sm["carState"], sm["modelV2"], sm["radarState"], sm) is False

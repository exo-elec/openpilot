"""Tests for EOP DLON (Dynamic Longitudinal Profile) force-stop and E2E triggers."""
import sys
from unittest.mock import MagicMock  # noqa: TID251

# Stub out missing Cython extensions before any imports
_fake_params_pyx = MagicMock()
_fake_params_pyx.Params = MagicMock
_fake_params_pyx.ParamKeyFlag = MagicMock()
_fake_params_pyx.ParamKeyType = MagicMock()
_fake_params_pyx.UnknownKeyName = Exception
sys.modules['openpilot.common.params_pyx'] = _fake_params_pyx

_fake_msgq = MagicMock()
_fake_msgq.Context = MagicMock
_fake_msgq.Poller = MagicMock
_fake_msgq.SubSocket = MagicMock
_fake_msgq.PubSocket = MagicMock
_fake_msgq.SocketEventHandle = MagicMock
_fake_msgq.toggle_fake_events = MagicMock()
_fake_msgq.fake_event_callback = MagicMock()
_fake_msgq.async_sleep = MagicMock()
_fake_msgq.async_wait_for_one_event = MagicMock()
_fake_msgq.MAX_FDS = 64
sys.modules['msgq.ipc_pyx'] = _fake_msgq


from openpilot.selfdrive.controls.lib.dlon import DLON, DLONMode, DriveMode


def _make_car_state(v_ego=0.0, gas_pressed=False, left_blinker=False, right_blinker=False):
  cs = MagicMock()
  cs.vEgo = v_ego
  cs.gasPressed = gas_pressed
  cs.leftBlinker = left_blinker
  cs.rightBlinker = right_blinker
  return cs


def _make_model_v2(should_stop=False):
  mv = MagicMock()
  mv.action.shouldStop = should_stop
  return mv


def _make_radar_state(has_lead=False):
  rs = MagicMock()
  rs.leadOne.status = has_lead
  return rs


def _make_sm(v_ego=0.0, gas_pressed=False, has_lead=False,
             should_stop=False, left_blinker=False, right_blinker=False,
             dlat_laneless=None, dlat_confidence=0.5, controls_state_valid=True):
  sm = MagicMock()
  sm.valid = {'radarState': True, 'navInstruction': False}
  values = {
    'carState': _make_car_state(v_ego, gas_pressed, left_blinker, right_blinker),
    'modelV2': _make_model_v2(should_stop),
    'radarState': _make_radar_state(has_lead),
  }
  if dlat_laneless is not None:
    cs = MagicMock()
    cs.dlatUseLaneless = dlat_laneless
    cs.dlatLaneConfidence = dlat_confidence
    values['controlsState'] = cs
    sm.valid['controlsState'] = controls_state_valid
  sm.__getitem__ = lambda _self, key: values.get(key, MagicMock())
  return sm


class TestDLONForceStop:
  """Test DLON force-stop detection logic."""

  def setup_method(self):
    self.dlon = DLON()
    self.dlon.force_stops_enabled = True
    self.dlon.mode = DLONMode.AUTO
    # Prevent update_params from resetting our toggles
    self.dlon.update_params = lambda: None

  def test_detect_traffic_control_all_conditions(self):
    model_v2 = _make_model_v2(should_stop=True)
    radar_state = _make_radar_state(has_lead=False)
    assert self.dlon.detect_traffic_control(model_v2, radar_state, v_ego=5.0) is True

  def test_detect_traffic_control_missing_should_stop(self):
    model_v2 = _make_model_v2(should_stop=False)
    radar_state = _make_radar_state(has_lead=False)
    assert self.dlon.detect_traffic_control(model_v2, radar_state, v_ego=5.0) is False

  def test_detect_traffic_control_with_lead(self):
    model_v2 = _make_model_v2(should_stop=True)
    radar_state = _make_radar_state(has_lead=True)
    assert self.dlon.detect_traffic_control(model_v2, radar_state, v_ego=5.0) is False

  def test_detect_traffic_control_high_speed(self):
    model_v2 = _make_model_v2(should_stop=True)
    radar_state = _make_radar_state(has_lead=False)
    assert self.dlon.detect_traffic_control(model_v2, radar_state, v_ego=15.0) is False

  def test_detect_traffic_control_none_model(self):
    assert self.dlon.detect_traffic_control(None, None, v_ego=5.0) is False

  def test_force_stop_triggered_after_debounce(self):
    sm = _make_sm(v_ego=5.0, should_stop=True, has_lead=False)
    result = self.dlon.update(sm)
    assert result['force_stop'] is False
    self.dlon.force_stop_timer = 1.0
    result = self.dlon.update(sm)
    assert result['force_stop'] is True

  def test_force_stop_overridden_by_gas(self):
    sm = _make_sm(v_ego=5.0, should_stop=True, has_lead=False, gas_pressed=True)
    self.dlon.force_stop_timer = 1.0
    result = self.dlon.update(sm)
    assert result['force_stop'] is False

  def test_force_stop_not_triggered_when_disabled(self):
    self.dlon.force_stops_enabled = False
    sm = _make_sm(v_ego=5.0, should_stop=True, has_lead=False)
    self.dlon.force_stop_timer = 1.0
    result = self.dlon.update(sm)
    assert result['force_stop'] is False

  def test_e2e_triggered_by_traffic_control(self):
    sm = _make_sm(v_ego=5.0, should_stop=True, has_lead=False)
    # Pre-set filter so traffic control is considered active
    self.dlon._has_traffic_control = True
    self.dlon._should_stop = True
    # Bypass mode_manager hysteresis
    self.dlon.mode_manager.current_mode = DriveMode.E2E
    result = self.dlon.update(sm)
    assert result['triggers']['traffic_control'] is True
    assert result['e2e_enabled'] is True

  def test_e2e_not_triggered_when_chill_mode(self):
    self.dlon.mode = DLONMode.CHILL
    sm = _make_sm(v_ego=5.0, should_stop=True, has_lead=False)
    result = self.dlon.update(sm)
    assert result['e2e_enabled'] is False

  def test_e2e_always_on_in_experimental_mode(self):
    self.dlon.mode = DLONMode.EXPERIMENTAL
    self.dlon._active = True  # Bypass enter debounce
    self.dlon.mode_manager.current_mode = DriveMode.E2E
    sm = _make_sm(v_ego=5.0)
    result = self.dlon.update(sm)
    assert result['e2e_enabled'] is True

  def test_lane_confidence_missing_controls_state_is_neutral(self):
    sm = _make_sm(v_ego=20.0)  # no controlsState published at all
    assert self.dlon.detect_lane_confidence_trigger(sm) is False

  def test_lane_confidence_stale_controls_state_is_neutral(self):
    sm = _make_sm(v_ego=20.0, dlat_laneless=True, dlat_confidence=0.1, controls_state_valid=False)
    assert self.dlon.detect_lane_confidence_trigger(sm) is False

  def test_lane_confidence_dlat_laneless_favors_e2e(self):
    sm = _make_sm(v_ego=20.0, dlat_laneless=True, dlat_confidence=0.2)
    assert self.dlon.detect_lane_confidence_trigger(sm) is True

  def test_lane_confidence_dlat_laneful_does_not_favor_e2e(self):
    sm = _make_sm(v_ego=20.0, dlat_laneless=False, dlat_confidence=0.9)
    assert self.dlon.detect_lane_confidence_trigger(sm) is False

  def test_auto_mode_switches_to_e2e_on_low_lane_confidence_alone(self):
    sm = _make_sm(v_ego=20.0, dlat_laneless=True, dlat_confidence=0.15)
    use_e2e = self.dlon._evaluate_auto_mode(sm['carState'], sm['modelV2'], sm['radarState'], sm)
    assert use_e2e is True

  def test_lane_confidence_trigger_toggle_disables_coupling(self):
    self.dlon._trigger_enabled['lane_confidence'] = False
    sm = _make_sm(v_ego=20.0, dlat_laneless=True, dlat_confidence=0.15)
    use_e2e = self.dlon._evaluate_auto_mode(sm['carState'], sm['modelV2'], sm['radarState'], sm)
    assert use_e2e is False


if __name__ == '__main__':
  import traceback
  t = TestDLONForceStop()
  failures = 0
  for name in dir(t):
    if name.startswith('test_'):
      try:
        t.setup_method()
        getattr(t, name)()
        print(f"  PASS: {name}")
      except Exception:
        failures += 1
        print(f"  FAIL: {name}")
        traceback.print_exc()
  msg = 'All passed' if failures == 0 else f'{failures} failure(s)'
  print(f'\n{msg}')
  exit(0 if failures == 0 else 1)

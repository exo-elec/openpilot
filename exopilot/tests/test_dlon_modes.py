from types import SimpleNamespace

from exopilot.controls.dlon import DLON, DLONMode, DriveMode


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


def test_dlon_force_stop_debounce_and_gas_override():
  dlon = DLON()
  dlon.update_params = lambda: None
  dlon.force_stops_enabled = True
  sm = make_dlon_sm(should_stop=True)
  assert not dlon.update(sm)["force_stop"]
  dlon.force_stop_timer = 1.0
  assert dlon.update(sm)["force_stop"]
  sm["carState"].gasPressed = True
  assert not dlon.update(sm)["force_stop"]


def test_dlon_has_chill_experimental_and_auto_modes():
  dlon = DLON()
  dlon.update_params = lambda: None
  sm = make_dlon_sm()
  dlon.mode = DLONMode.CHILL
  assert not dlon.update(sm)["e2e_enabled"]
  dlon.mode = DLONMode.EXPERIMENTAL
  dlon._active = True
  dlon.mode_manager.current_mode = DriveMode.E2E
  assert dlon.update(sm)["e2e_enabled"]


def test_speed_limit_trigger_prefers_the_map_limit_then_navigation():
  dlon = DLON()
  sm = make_dlon_sm(v_ego=30.0)
  assert not dlon.detect_speed_limit_trigger(sm, v_ego=30.0)

  # navigation only (m/s): a limit just below v_ego is noise, a lower one triggers
  sm.valid["navInstruction"] = True
  sm["navInstruction"] = SimpleNamespace(speedLimit=29.0)
  assert not dlon.detect_speed_limit_trigger(sm, v_ego=30.0)
  sm["navInstruction"] = SimpleNamespace(speedLimit=20.0)
  assert dlon.detect_speed_limit_trigger(sm, v_ego=30.0)

  # the map limit (km/h) wins over navigation: 108 km/h = 30 m/s, so no trigger
  sm.valid["mapData"] = True
  sm["mapData"] = SimpleNamespace(speedLimit=108.0)
  assert not dlon.detect_speed_limit_trigger(sm, v_ego=30.0)
  sm["mapData"] = SimpleNamespace(speedLimit=60.0)
  assert dlon.detect_speed_limit_trigger(sm, v_ego=30.0)


def test_drive_mode_param_picks_the_mode_and_defaults_to_dynamic():
  for raw, mode in [(0, DLONMode.CHILL), (1, DLONMode.EXPERIMENTAL), (2, DLONMode.AUTO), (None, DLONMode.AUTO), (7, DLONMode.AUTO)]:
    dlon = DLON(get_bool=lambda k: True, get=lambda k, raw=raw: raw)
    dlon.update_params()
    assert dlon.mode is mode
  dlon = DLON(get_bool=lambda k: True)
  dlon.update_params()
  assert dlon.mode is DLONMode.AUTO

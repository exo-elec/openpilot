"""Tests for EOP NSLC (Navigation Speed Limit Controller) unit conversion and logic."""
import sys
import time
from unittest.mock import MagicMock

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

from cereal import log
from openpilot.common.constants import CV

from openpilot.selfdrive.controls.lib.nslc import NSLC, get_nslc_speed


class MockParams:
  """In-memory mock of Params."""

  def __init__(self):
    self._data = {}

  def get(self, key, block=False):
    return self._data.get(key)

  def get_bool(self, key):
    return self._data.get(key) == b"1"

  def put(self, key, val):
    self._data[key] = val


class TestNSLC:
  """Test NSLC unit conversion and source priority."""

  def setup_method(self):
    self.nslc = NSLC()
    self.nslc._params = MockParams()
    self.nslc._params.put("EOPNSLCEnabled", b"1")
    self.nslc._enabled = True
    # Neutralize offset and confirmation so tests see raw converted values
    self.nslc._offset.get_offset_ms = lambda limit_ms: 0.0
    self.nslc._confirmation.update = lambda limit_ms, override, t: limit_ms

  def test_map_data_kmh_converted_to_ms(self):
    map_data = log.MapData.new_message()
    map_data.speedLimit = 50.0  # km/h
    limit_ms, status = self.nslc.update(
      nav_instruction=None,
      map_data=map_data,
      v_ego=10.0,
      driver_overriding=False,
      current_time=time.monotonic()
    )
    expected_ms = 50.0 * CV.KPH_TO_MS
    assert abs(limit_ms - expected_ms) < 0.01
    assert status == "active"

  def test_nav_instruction_ms_not_converted(self):
    nav_instruction = log.NavInstruction.new_message()
    nav_instruction.speedLimit = 13.89  # m/s
    limit_ms, status = self.nslc.update(
      nav_instruction=nav_instruction,
      map_data=None,
      v_ego=10.0,
      driver_overriding=False,
      current_time=time.monotonic()
    )
    assert abs(limit_ms - 13.89) < 0.01
    assert status == "active"

  def test_map_data_priority_over_nav_instruction(self):
    map_data = log.MapData.new_message()
    map_data.speedLimit = 60.0  # km/h
    nav_instruction = log.NavInstruction.new_message()
    nav_instruction.speedLimit = 20.0  # m/s
    limit_ms, status = self.nslc.update(
      nav_instruction=nav_instruction,
      map_data=map_data,
      v_ego=10.0,
      driver_overriding=False,
      current_time=time.monotonic()
    )
    expected_ms = 60.0 * CV.KPH_TO_MS
    assert abs(limit_ms - expected_ms) < 0.01

  def test_no_limit_returns_none(self):
    limit_ms, status = self.nslc.update(
      nav_instruction=None,
      map_data=None,
      v_ego=10.0,
      driver_overriding=False,
      current_time=time.monotonic()
    )
    assert limit_ms is None
    assert status == "unavailable"

  def test_disabled_returns_none(self):
    self.nslc._params.put("EOPNSLCEnabled", b"0")
    self.nslc._enabled = False
    map_data = log.MapData.new_message()
    map_data.speedLimit = 50.0
    limit_ms, status = self.nslc.update(
      nav_instruction=None,
      map_data=map_data,
      v_ego=10.0,
      driver_overriding=False,
      current_time=time.monotonic()
    )
    assert limit_ms is None
    assert status == "disabled"

  def test_get_nslc_speed_helper(self):
    nav_instruction = log.NavInstruction.new_message()
    nav_instruction.speedLimit = 13.89
    speed = get_nslc_speed(
      nav_instruction=nav_instruction,
      map_data=None,
      v_ego=10.0
    )
    assert speed is not None
    assert abs(speed - 13.89) < 0.5


if __name__ == '__main__':
  import traceback
  t = TestNSLC()
  failures = 0
  for name in dir(t):
    if name.startswith('test_'):
      try:
        t.setup_method()
        getattr(t, name)()
        print(f"  PASS: {name}")
      except Exception as e:
        failures += 1
        print(f"  FAIL: {name}")
        traceback.print_exc()
  msg = 'All passed' if failures == 0 else f'{failures} failure(s)'
  print(f'\n{msg}')
  exit(0 if failures == 0 else 1)

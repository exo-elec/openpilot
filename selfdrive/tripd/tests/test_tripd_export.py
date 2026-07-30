"""Tests for EOP TripD drive-end export and save logic."""
import sys
import time
import json
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

from openpilot.selfdrive.tripd.tripd import TripD, TripStats, DriveState


class MockParams:
  """In-memory mock of Params that records all put calls."""

  def __init__(self):
    self._data = {}
    self._puts = []

  def get(self, key, block=False, encoding=None):
    return self._data.get(key)

  def get_bool(self, key):
    return self._data.get(key) == b"1"

  def put(self, key, val):
    self._data[key] = val
    self._puts.append((key, val))


class TestTripDExport:
  """Test TripD drive-end export and personality time finalization."""

  def setup_method(self):
    self.tripd = TripD()
    self.tripd.params = MockParams()
    self.tripd.is_driving = True
    self.tripd.session_start_time = time.monotonic() - 60.0
    self.tripd.current_session = TripStats()
    self.tripd.current_session.distance = 1234.5
    self.tripd.current_session.max_accel = 2.5
    self.tripd.current_session.longest_override_free_distance = 800.0
    self.tripd._current_personality = log.LongitudinalPersonality.standard
    self.tripd._personality_segment_start = time.monotonic() - 30.0
    self.tripd.lifetime = TripStats()

  def test_drive_end_finalizes_personality_time(self):
    self.tripd._handle_drive_end()
    assert 'standard' in self.tripd.current_session.personality_time
    assert self.tripd.current_session.personality_time['standard'] > 0

  def test_drive_end_exports_to_params(self):
    self.tripd._handle_drive_end()
    puts = {k: v for k, v in self.tripd.params._puts}
    assert "EOPTripLastDistance" in puts
    assert "EOPTripLastMaxAccel" in puts
    assert "EOPTripLastOverrideFreeDistance" in puts
    assert "EOPTripLastDuration" in puts
    assert "EOPTripLastPersonalityTime" in puts

  def test_drive_end_distance_is_float(self):
    self.tripd._handle_drive_end()
    puts = {k: v for k, v in self.tripd.params._puts}
    val = puts["EOPTripLastDistance"]
    assert isinstance(val, float)
    assert abs(val - 1234.5) < 0.1

  def test_drive_end_personality_json_is_string(self):
    self.tripd._handle_drive_end()
    puts = {k: v for k, v in self.tripd.params._puts}
    val = puts["EOPTripLastPersonalityTime"]
    assert isinstance(val, str)
    parsed = json.loads(val)
    assert isinstance(parsed, dict)

  def test_drive_end_increments_lifetime_drives(self):
    before = self.tripd.lifetime.drives
    self.tripd._handle_drive_end()
    assert self.tripd.lifetime.drives == before + 1

  def test_drive_end_sets_is_driving_false(self):
    self.tripd._handle_drive_end()
    assert self.tripd.is_driving is False

  def test_drive_end_no_crash_when_not_driving(self):
    self.tripd.is_driving = False
    self.tripd._handle_drive_end()  # Should not crash
    assert len(self.tripd.params._puts) == 0

  def test_save_stats_exports_float_params(self):
    self.tripd.lifetime.distance = 5000
    self.tripd.lifetime.onroad_time = 3600
    self.tripd.lifetime.engaged_time = 1800
    self.tripd._save_stats()
    puts = {k: v for k, v in self.tripd.params._puts}
    assert "EOPTripTotalDistance" in puts
    assert "EOPTripUptimeOnroad" in puts
    assert "EOPTripUptimeEngaged" in puts
    # Verify values are floats (or at least numeric)
    assert isinstance(puts["EOPTripTotalDistance"], (int, float))


if __name__ == '__main__':
  import traceback
  t = TestTripDExport()
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

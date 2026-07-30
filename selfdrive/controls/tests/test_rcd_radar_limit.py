"""Tests for the radar4d weather-severity RCD speed cap."""

from openpilot.common.params import Params
from openpilot.selfdrive.controls.lib.rcd import RCD


class _FakeRadar4d:
  def __init__(self, severity):
    self.weatherSeverity = severity


class _FakeSM:
  """Minimal SubMaster stand-in: valid dict + radar4d accessor only."""

  def __init__(self, severity=None):
    self._radar = _FakeRadar4d(severity if severity is not None else 0)
    self.valid = {'radar4d': severity is not None}

  def __getitem__(self, key):
    if key == 'radar4d':
      return self._radar
    raise KeyError(key)


def test_radar_limit_table():
  rcd = RCD()
  for severity, expected in enumerate(RCD.RADAR_WEATHER_LIMITS_MS):
    limit, sev = rcd._radar_weather_limit(_FakeSM(severity))
    assert limit == expected
    assert sev == severity


def test_invalid_radar_no_limit():
  rcd = RCD()
  limit, sev = rcd._radar_weather_limit(_FakeSM(None))
  assert limit == 0.0
  assert sev == 0


def test_severity_clamped():
  rcd = RCD()
  limit, sev = rcd._radar_weather_limit(_FakeSM(9))
  assert limit == RCD.RADAR_WEATHER_LIMITS_MS[3]
  assert sev == 3


def test_update_heavy_no_camera_source_still_limits():
  params = Params()
  params.put_bool("EOPRCDEnabled", True)
  try:
    rcd = RCD()
    state = rcd.update(_FakeSM(3))
    assert state.is_active
    assert state.speed_limit_ms == 12.0
    assert "radarSeverity=3" in state.reason
  finally:
    params.put_bool("EOPRCDEnabled", False)


def test_update_light_weather_no_speed_cap():
  params = Params()
  params.put_bool("EOPRCDEnabled", True)
  try:
    rcd = RCD()
    state = rcd.update(_FakeSM(1))
    assert not state.is_active
    assert state.speed_limit_ms == 0.0
  finally:
    params.put_bool("EOPRCDEnabled", False)

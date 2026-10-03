"""Tests for EOP speed limit utilities: SpeedLimitOffset, SpeedLimitConfirmation, SpeedLimitResolver."""
import sys
import time
from unittest.mock import MagicMock  # noqa: TID251

# Stub out missing Cython extensions before any imports
_fake_params_pyx = MagicMock()
_fake_params_pyx.Params = MagicMock
_fake_params_pyx.ParamKeyFlag = MagicMock()
_fake_params_pyx.ParamKeyType = MagicMock()
_fake_params_pyx.UnknownKeyName = Exception
sys.modules['openpilot.common.params_pyx'] = _fake_params_pyx

from openpilot.selfdrive.controls.lib.eop_utils import SpeedLimitOffset, SpeedLimitConfirmation
from openpilot.selfdrive.controls.lib.speed_limit_resolver import SpeedLimitResolver


class MockParams:
  """In-memory mock of Params."""

  def __init__(self):
    self._data = {}

  def get(self, key, block=False, encoding=None):
    val = self._data.get(key)
    if val is not None and encoding:
      return val.decode(encoding)
    return val

  def get_bool(self, key):
    return self._data.get(key) == b"1"

  def put(self, key, val):
    self._data[key] = val


class TestSpeedLimitOffset:
  """Test per-speed-range offset calculator."""

  def test_default_offsets_are_zero(self):
    p = MockParams()
    slo = SpeedLimitOffset(p)
    assert all(o == 0.0 for o in slo._offsets)

  def test_offset_lookup_by_bucket(self):
    p = MockParams()
    # Set offset for bucket 0 (0-29 km/h range, i.e. 0-8.1 m/s)
    p.put("EOPSLCOffset1", "5.0")  # 5 km/h offset
    slo = SpeedLimitOffset(p)
    slo.refresh(now=time.monotonic())
    # At 5 m/s (18 km/h), should be in bucket 0
    offset = slo.get_offset_ms(5.0)
    expected_ms = 5.0 * (1000.0 / 3600.0)
    assert abs(offset - expected_ms) < 0.01

  def test_offset_lookup_second_bucket(self):
    p = MockParams()
    p.put("EOPSLCOffset2", "10.0")  # 10 km/h offset
    slo = SpeedLimitOffset(p)
    slo.refresh(now=time.monotonic())
    # At 10 m/s (36 km/h), should be in bucket 1 (8.1-13.6 m/s)
    offset = slo.get_offset_ms(10.0)
    expected_ms = 10.0 * (1000.0 / 3600.0)
    assert abs(offset - expected_ms) < 0.01

  def test_offset_cache_avoids_repeated_io(self):
    p = MockParams()
    p.put("EOPSLCOffset1", "3.0")
    slo = SpeedLimitOffset(p)
    slo.refresh(now=0.0)
    first = slo.get_offset_ms(5.0)
    p.put("EOPSLCOffset1", "99.0")
    slo.refresh(now=1.0)  # Within 2s TTL, should not read
    second = slo.get_offset_ms(5.0)
    assert first == second

  def test_offset_cache_expires(self):
    p = MockParams()
    p.put("EOPSLCOffset1", "3.0")
    slo = SpeedLimitOffset(p)
    slo.refresh(now=0.0)
    first = slo.get_offset_ms(5.0)
    p.put("EOPSLCOffset1", "7.0")
    slo.refresh(now=3.0)  # Past 2s TTL
    second = slo.get_offset_ms(5.0)
    assert second > first

  def test_invalid_param_defaults_to_zero(self):
    p = MockParams()
    p.put("EOPSLCOffset1", "bad")
    slo = SpeedLimitOffset(p)
    slo.refresh(now=time.monotonic())
    assert slo.get_offset_ms(5.0) == 0.0

  def test_fallback_for_high_speed(self):
    p = MockParams()
    p.put("EOPSLCOffset7", "15.0")
    slo = SpeedLimitOffset(p)
    slo.refresh(now=time.monotonic())
    # 40 m/s (~144 km/h) is above all bucket edges
    offset = slo.get_offset_ms(40.0)
    expected_ms = 15.0 * (1000.0 / 3600.0)
    assert abs(offset - expected_ms) < 0.01


class TestSpeedLimitConfirmation:
  """Test driver confirmation gate for speed limit changes."""

  def setup_method(self):
    self.sc = SpeedLimitConfirmation()
    self.sc._confirmation_lower = False
    self.sc._confirmation_higher = False

  def test_first_limit_no_confirmation_needed(self):
    result = self.sc.update(13.89, driver_overriding=False, now=0.0)
    assert abs(result - 13.89) < 0.01

  def test_small_change_no_confirmation_needed(self):
    self.sc._confirmed_limit_ms = 13.89
    result = self.sc.update(14.0, driver_overriding=False, now=0.0)
    assert result is not None

  def test_lower_limit_blocked_when_confirmation_required(self):
    self.sc._confirmation_lower = True
    self.sc._confirmed_limit_ms = 13.89
    result = self.sc.update(8.0, driver_overriding=False, now=0.0)
    # Should return the old confirmed limit, not the new lower one
    assert result is not None
    assert abs(result - 13.89) < 0.01

  def test_higher_limit_blocked_when_confirmation_required(self):
    self.sc._confirmation_higher = True
    self.sc._confirmed_limit_ms = 13.89
    result = self.sc.update(20.0, driver_overriding=False, now=0.0)
    assert result is not None
    assert abs(result - 13.89) < 0.01

  def test_driver_override_confirms_change(self):
    self.sc._confirmation_lower = True
    self.sc._confirmed_limit_ms = 13.89
    result = self.sc.update(8.0, driver_overriding=True, now=0.0)
    assert abs(result - 8.0) < 0.01

  def test_timeout_auto_confirms(self):
    self.sc._confirmation_lower = True
    self.sc._confirmed_limit_ms = 13.89
    self.sc.update(8.0, driver_overriding=False, now=0.0)
    result = self.sc.update(8.0, driver_overriding=False, now=31.0)
    assert abs(result - 8.0) < 0.01

  def test_none_input_clears_state(self):
    self.sc._confirmed_limit_ms = 13.89
    result = self.sc.update(None, driver_overriding=False, now=0.0)
    assert result is None
    assert self.sc._confirmed_limit_ms is None


class TestSpeedLimitResolver:
  """Test SpeedLimitResolver source policies."""

  def setup_method(self):
    self.resolver = SpeedLimitResolver()
    self.resolver.params = MockParams()

  def test_policy_none_ignores_all(self):
    self.resolver.params.put("SpeedLimitPolicy", b"0")
    resolved = self.resolver.update(
      mslc_limit_mps=10.0,
      nslc_limit_mps=12.0,
      car_limit_mps=11.0,
      v_ego=10.0,
      distance_to_change_m=100.0
    )
    assert not resolved.active

  def test_policy_car_only(self):
    self.resolver.params.put("SpeedLimitPolicy", b"1")
    resolved = self.resolver.update(
      mslc_limit_mps=10.0,
      nslc_limit_mps=12.0,
      car_limit_mps=11.0,
      v_ego=10.0,
      distance_to_change_m=100.0
    )
    assert resolved.active
    assert abs(resolved.limit_mps - 11.0) < 0.01

  def test_policy_map_only(self):
    self.resolver.params.put("SpeedLimitPolicy", b"2")
    resolved = self.resolver.update(
      mslc_limit_mps=10.0,
      nslc_limit_mps=12.0,
      car_limit_mps=11.0,
      v_ego=10.0,
      distance_to_change_m=100.0
    )
    assert resolved.active
    assert abs(resolved.limit_mps - 10.0) < 0.01

  def test_policy_nav_only(self):
    self.resolver.params.put("SpeedLimitPolicy", b"3")
    resolved = self.resolver.update(
      mslc_limit_mps=10.0,
      nslc_limit_mps=12.0,
      car_limit_mps=11.0,
      v_ego=10.0,
      distance_to_change_m=100.0
    )
    assert resolved.active
    assert abs(resolved.limit_mps - 12.0) < 0.01

  def test_policy_both_uses_lowest(self):
    self.resolver.params.put("SpeedLimitPolicy", b"4")
    resolved = self.resolver.update(
      mslc_limit_mps=10.0,
      nslc_limit_mps=12.0,
      car_limit_mps=11.0,
      v_ego=10.0,
      distance_to_change_m=100.0
    )
    assert resolved.active
    assert abs(resolved.limit_mps - 10.0) < 0.01

  def test_policy_car_fallback(self):
    self.resolver.params.put("SpeedLimitPolicy", b"5")
    # When MSLC/NSLC available, prefer them
    resolved = self.resolver.update(
      mslc_limit_mps=10.0,
      nslc_limit_mps=None,
      car_limit_mps=11.0,
      v_ego=10.0,
      distance_to_change_m=100.0
    )
    assert resolved.active
    assert abs(resolved.limit_mps - 10.0) < 0.01
    # When MSLC/NSLC unavailable, fallback to car
    resolved2 = self.resolver.update(
      mslc_limit_mps=None,
      nslc_limit_mps=None,
      car_limit_mps=11.0,
      v_ego=10.0,
      distance_to_change_m=100.0
    )
    assert resolved2.active
    assert abs(resolved2.limit_mps - 11.0) < 0.01

  def test_apply_to_v_cruise_lowers_when_active(self):
    self.resolver.params.put("SpeedLimitPolicy", b"4")
    resolved = self.resolver.update(
      mslc_limit_mps=10.0,
      nslc_limit_mps=None,
      car_limit_mps=None,
      v_ego=15.0,
      distance_to_change_m=100.0
    )
    v_cruise = self.resolver.apply_to_v_cruise(v_cruise=20.0, resolved=resolved)
    assert abs(v_cruise - 10.0) < 0.01

  def test_apply_to_v_cruise_ignores_when_inactive(self):
    self.resolver.params.put("SpeedLimitPolicy", b"0")
    resolved = self.resolver.update(
      mslc_limit_mps=10.0,
      nslc_limit_mps=None,
      car_limit_mps=None,
      v_ego=15.0,
      distance_to_change_m=100.0
    )
    v_cruise = self.resolver.apply_to_v_cruise(v_cruise=20.0, resolved=resolved)
    assert abs(v_cruise - 20.0) < 0.01


if __name__ == '__main__':
  import traceback
  failures = 0
  for cls in (TestSpeedLimitOffset, TestSpeedLimitConfirmation, TestSpeedLimitResolver):
    t = cls()
    for name in dir(t):
      if name.startswith('test_'):
        try:
          if hasattr(t, 'setup_method'):
            t.setup_method()
          getattr(t, name)()
          print(f"  PASS: {cls.__name__}.{name}")
        except Exception:
          failures += 1
          print(f"  FAIL: {cls.__name__}.{name}")
          traceback.print_exc()
  msg = 'All passed' if failures == 0 else f'{failures} failure(s)'
  print(f'\n{msg}')
  exit(0 if failures == 0 else 1)

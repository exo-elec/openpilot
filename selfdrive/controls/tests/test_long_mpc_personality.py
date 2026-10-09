"""Tests for EOP longitudinal personality param loading in long_mpc."""
import time

import pytest
from cereal import log


class MockParams:
  """In-memory mock of Params for unit testing without Cython extension."""

  _data: dict[str, str] = {}

  def __init__(self):
    pass

  def get(self, key, block=False):
    return self._data.get(key)

  def put(self, key, val):
    self._data[key] = val

  def remove(self, key):
    self._data.pop(key, None)

  @classmethod
  def clear(cls):
    cls._data.clear()


@pytest.fixture(autouse=True)
def mock_params_module(monkeypatch):
  """Replace Params import in long_mpc with our mock factory."""
  monkeypatch.setattr(
    'openpilot.common.params.Params',
    MockParams,
  )


class TestPersonalityParams:
  """Test per-personality param loading and caching."""

  def setup_method(self):
    # Import here so mock_params_module has patched Params first
    from openpilot.selfdrive.controls.lib.longitudinal_mpc_lib.long_mpc import (
      _PERSONALITY_DEFAULTS, _PERSONALITY_NAME_MAP, _personality_param_cache,
    )
    self.PERSONALITY_DEFAULTS = _PERSONALITY_DEFAULTS
    self.PERSONALITY_NAME_MAP = _PERSONALITY_NAME_MAP
    # Reset global cache and mock params before each test
    _personality_param_cache['ts'] = 0.0
    _personality_param_cache['vals'] = {}
    self._cache = _personality_param_cache
    MockParams.clear()

  def test_defaults_when_no_params(self):
    from openpilot.selfdrive.controls.lib.longitudinal_mpc_lib.long_mpc import _load_personality_params
    vals = _load_personality_params(now=time.monotonic())
    for pname in ('aggressive', 'standard', 'relaxed', 'traffic'):
      enum_val = getattr(log.LongitudinalPersonality, pname)
      assert vals[pname]['jerk'] == self.PERSONALITY_DEFAULTS[enum_val]['jerk']
      assert vals[pname]['t_follow'] == self.PERSONALITY_DEFAULTS[enum_val]['t_follow']

  def test_custom_params_override_defaults(self):
    from openpilot.selfdrive.controls.lib.longitudinal_mpc_lib.long_mpc import _load_personality_params
    p = MockParams()
    p.put("EOPAggressiveJerk", "0.9")
    p.put("EOPAggressiveFollow", "1.35")
    p.put("EOPRelaxedJerk", "1.5")
    p.put("EOPRelaxedFollow", "2.0")

    # Pre-seed mock into the module by calling with a fresh cache
    self._cache['ts'] = 0.0
    self._cache['vals'] = {}
    vals = _load_personality_params(now=time.monotonic())
    assert vals['aggressive']['jerk'] == pytest.approx(0.9)
    assert vals['aggressive']['t_follow'] == pytest.approx(1.35)
    assert vals['relaxed']['jerk'] == pytest.approx(1.5)
    assert vals['relaxed']['t_follow'] == pytest.approx(2.0)
    # Standard and traffic should still be defaults
    assert vals['standard']['jerk'] == self.PERSONALITY_DEFAULTS[log.LongitudinalPersonality.standard]['jerk']
    assert vals['traffic']['jerk'] == self.PERSONALITY_DEFAULTS[log.LongitudinalPersonality.traffic]['jerk']

  def test_invalid_param_falls_back_to_default(self):
    from openpilot.selfdrive.controls.lib.longitudinal_mpc_lib.long_mpc import _load_personality_params
    p = MockParams()
    p.put("EOPStandardJerk", "not_a_number")
    self._cache['ts'] = 0.0
    self._cache['vals'] = {}
    vals = _load_personality_params(now=time.monotonic())
    assert vals['standard']['jerk'] == self.PERSONALITY_DEFAULTS[log.LongitudinalPersonality.standard]['jerk']

  def test_cache_returns_same_values(self):
    from openpilot.selfdrive.controls.lib.longitudinal_mpc_lib.long_mpc import _load_personality_params
    now = time.monotonic()
    vals1 = _load_personality_params(now=now)
    # Change mock param after cache is populated — cache should shield us
    p = MockParams()
    p.put("EOPStandardJerk", "99.0")
    vals2 = _load_personality_params(now=now + 1.0)  # Within 2s TTL
    assert vals1 == vals2
    assert vals2['standard']['jerk'] != 99.0

  def test_cache_expires_after_ttl(self):
    from openpilot.selfdrive.controls.lib.longitudinal_mpc_lib.long_mpc import _load_personality_params
    now = time.monotonic()
    _ = _load_personality_params(now=now)
    p = MockParams()
    p.put("EOPStandardJerk", "3.14")
    self._cache['ts'] = 0.0  # Force cache expiry
    self._cache['vals'] = {}
    vals2 = _load_personality_params(now=now + 2.5)
    assert vals2['standard']['jerk'] == pytest.approx(3.14)

  def test_get_jerk_factor_all_personalities(self):
    from openpilot.selfdrive.controls.lib.longitudinal_mpc_lib.long_mpc import get_jerk_factor
    self._cache['ts'] = 0.0
    self._cache['vals'] = {}
    for enum_val in [
      log.LongitudinalPersonality.aggressive,
      log.LongitudinalPersonality.standard,
      log.LongitudinalPersonality.relaxed,
      log.LongitudinalPersonality.traffic,
    ]:
      jerk = get_jerk_factor(enum_val)
      assert jerk == self.PERSONALITY_DEFAULTS[enum_val]['jerk']

  def test_get_t_follow_all_personalities(self):
    from openpilot.selfdrive.controls.lib.longitudinal_mpc_lib.long_mpc import get_T_FOLLOW
    self._cache['ts'] = 0.0
    self._cache['vals'] = {}
    for enum_val in [
      log.LongitudinalPersonality.aggressive,
      log.LongitudinalPersonality.standard,
      log.LongitudinalPersonality.relaxed,
      log.LongitudinalPersonality.traffic,
    ]:
      t_follow = get_T_FOLLOW(enum_val)
      assert t_follow == self.PERSONALITY_DEFAULTS[enum_val]['t_follow']

  def test_personality_name_map_complete(self):
    # Verify reverse map covers all personalities
    for pname, enum_val in log.LongitudinalPersonality.schema.enumerants.items():
      assert self.PERSONALITY_NAME_MAP[enum_val] == pname

  def test_get_jerk_factor_with_custom_params(self):
    from openpilot.selfdrive.controls.lib.longitudinal_mpc_lib.long_mpc import get_jerk_factor
    p = MockParams()
    p.put("EOPTrafficJerk", "0.6")
    self._cache['ts'] = 0.0
    self._cache['vals'] = {}
    jerk = get_jerk_factor(log.LongitudinalPersonality.traffic)
    assert jerk == pytest.approx(0.6)

  def test_get_t_follow_with_custom_params(self):
    from openpilot.selfdrive.controls.lib.longitudinal_mpc_lib.long_mpc import get_T_FOLLOW
    p = MockParams()
    p.put("EOPTrafficFollow", "1.5")
    self._cache['ts'] = 0.0
    self._cache['vals'] = {}
    t_follow = get_T_FOLLOW(log.LongitudinalPersonality.traffic)
    assert t_follow == pytest.approx(1.5)

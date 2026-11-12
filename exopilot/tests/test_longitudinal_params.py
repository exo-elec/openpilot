import pytest

from exopilot.runtime.feature_keys import LONGITUDINAL_KEYS as KEYS
from exopilot.runtime.longitudinal_params import LongitudinalSettings


class Params:
  def __init__(self, values):
    self.values, self.reads = values, []

  def get(self, key):
    self.reads.append(key)
    return self.values.get(key)


def test_settings_preserve_defaults_and_refresh_after_two_seconds():
  params = Params({})
  now = [0.0]
  settings = LongitudinalSettings(KEYS, lambda: params, lambda: now[0])
  assert settings.load_accel_profile() == 'normal'
  assert settings.load_adaptive_gap_enabled() is False
  params.values.update({KEYS['accel']: b'sport', KEYS['gap']: b'1'})
  now[0] = 1.99
  assert settings.load_accel_profile() == 'normal'
  assert settings.load_adaptive_gap_enabled() is False
  assert len(params.reads) == 2
  now[0] = 2.0
  assert settings.load_accel_profile() == 'sport'
  assert settings.load_adaptive_gap_enabled() is True
  assert params.reads == [KEYS['accel'], KEYS['gap']] * 2


def test_typed_values_match_compiled_params_contract():
  settings = LongitudinalSettings(params_factory=lambda: Params({'AdaptiveGapEnabled': True, 'AccelerationProfile': 'sport'}))
  assert settings.load_adaptive_gap_enabled()
  assert settings.load_accel_profile() == 'sport'


@pytest.mark.parametrize('raw', [None, b'', b'warp-speed'])
def test_unknown_acceleration_profiles_fall_back(raw):
  settings = LongitudinalSettings(params_factory=lambda: Params({KEYS['accel']: raw}))
  assert settings.load_accel_profile() == 'normal'


def test_gap_accepts_only_the_enabled_parameter_value():
  for raw in [None, b'', b'0', b'true', b'1']:
    settings = LongitudinalSettings(params_factory=lambda raw=raw: Params({KEYS['gap']: raw}))
    assert settings.load_adaptive_gap_enabled() is (raw == b'1')

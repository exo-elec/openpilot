import pytest

from nagaspilot.runtime.feature_keys import EOP_KEYS, NGP_KEYS
from nagaspilot.runtime.longitudinal_params import LongitudinalSettings


class Params:
  def __init__(self, values):
    self.values, self.reads = values, []

  def get(self, key):
    self.reads.append(key)
    return self.values.get(key)


@pytest.mark.parametrize('keys', [NGP_KEYS, EOP_KEYS])
def test_settings_preserve_defaults_and_refresh_after_two_seconds(keys):
  params = Params({})
  now = [0.0]
  settings = LongitudinalSettings(keys, lambda: params, lambda: now[0])
  assert settings.load_accel_profile() == 'normal'
  assert settings.load_adaptive_gap_enabled() is False
  params.values.update({keys['accel']: b'sport', keys['gap']: b'1'})
  now[0] = 1.99
  assert settings.load_accel_profile() == 'normal'
  assert settings.load_adaptive_gap_enabled() is False
  assert len(params.reads) == 2
  now[0] = 2.0
  assert settings.load_accel_profile() == 'sport'
  assert settings.load_adaptive_gap_enabled() is True
  assert params.reads == [keys['accel'], keys['gap']] * 2


def test_product_settings_have_independent_caches_and_never_read_other_keys():
  params = Params({NGP_KEYS['accel']: b'eco', EOP_KEYS['accel']: b'sport', EOP_KEYS['gap']: b'1'})
  ngp = LongitudinalSettings(NGP_KEYS, lambda: params)
  eop = LongitudinalSettings(EOP_KEYS, lambda: params)
  assert ngp.load_accel_profile() == 'eco'
  assert eop.load_accel_profile() == 'sport'
  assert ngp.load_adaptive_gap_enabled() is False
  assert eop.load_adaptive_gap_enabled() is True


@pytest.mark.parametrize('raw', [None, b'', b'warp-speed'])
def test_unknown_acceleration_profiles_fall_back(raw):
  settings = LongitudinalSettings(params_factory=lambda: Params({NGP_KEYS['accel']: raw}))
  assert settings.load_accel_profile() == 'normal'


def test_gap_accepts_only_the_enabled_parameter_value():
  for raw in [None, b'', b'0', b'true', b'1']:
    settings = LongitudinalSettings(params_factory=lambda raw=raw: Params({NGP_KEYS['gap']: raw}))
    assert settings.load_adaptive_gap_enabled() is (raw == b'1')

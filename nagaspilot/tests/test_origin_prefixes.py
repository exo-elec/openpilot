import importlib

import pytest
from nagaspilot.runtime.feature_keys import EOP_LEGACY_KEYS, OriginParams

EOP_PORTS = ('lat_nudge', 'lon_nudge', 'predict', 'speed_reduction', 'mtsc', 'mslc', 'tlsc', 'ddsc', 'rcd', 'curve_speed')


@pytest.mark.parametrize('feature', EOP_PORTS)
def test_eop_origin_legacy_import_is_the_same_canonical_module(feature):
  canonical = importlib.import_module(f'nagaspilot.controls.eop_{feature}')
  legacy = importlib.import_module(f'nagaspilot.controls.ngp_{feature}')
  assert canonical is legacy
  assert canonical.__name__ == f'nagaspilot.controls.eop_{feature}'


class Params:
  def __init__(self, values):
    self.values = values

  def get(self, key):
    return self.values.get(key)

  def get_bool(self, key):
    return self.get(key) == b'1'


@pytest.mark.parametrize('canonical,legacy', EOP_LEGACY_KEYS.items())
def test_saved_legacy_values_survive_but_canonical_off_has_priority(canonical, legacy):
  params = Params({legacy: b'1'})
  adapter = OriginParams(params)
  assert adapter.get(canonical) == b'1'
  assert adapter.get(legacy) == b'1'
  assert adapter.get_bool(canonical)
  params.values[canonical] = b'0'
  assert adapter.get(canonical) == b'0'
  assert adapter.get(legacy) == b'0'
  assert not adapter.get_bool(canonical)
  params.values.pop(legacy)
  params.values.pop(canonical)
  assert adapter.get(canonical) is None
  assert not adapter.get_bool(canonical)


def test_ngp_origin_switches_are_not_remapped():
  adapter = OriginParams(Params({'ngp_lat_alcc': b'1', 'ngp_lon_brsc': b'0'}))
  assert adapter.get_bool('ngp_lat_alcc')
  assert not adapter.get_bool('ngp_lon_brsc')


def test_saved_offset_bytes_are_decoded_before_map_configuration():
  adapter = OriginParams(Params({'ngp_lon_slc_offsets': b'5,5,5,5,5,5,5'}))
  assert adapter.get_text('EOPSharedSLCOffsets') == '5,5,5,5,5,5,5'
  from nagaspilot.runtime.map_speed import MapSpeed
  controller = MapSpeed(adapter.get_bool, adapter.get_text)
  controller._refresh_config(100.0)
  assert all(abs(value - 5 / 3.6) < 1e-9 for value in controller.mslc._offset._offsets)

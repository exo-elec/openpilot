from nagaspilot.runtime.feature_keys import EOP_LEGACY_KEYS, OriginParams, migrate_origin_params
from nagaspilot.runtime.longitudinal_params import LongitudinalSettings


class Params:
  def __init__(self, values): self.values=values
  def get(self,key): return self.values.get(key)
  def put(self,key,value): self.values[key]=value
  def get_bool(self,key): return self.get(key) is True


def test_typed_booleans_and_strings_match_compiled_params_contract():
  p=Params({'ngp_lon_adaptive_gap':True,'ngp_lon_accel_profile':'sport','ngp_lon_mtsc':True})
  settings=LongitudinalSettings(params_factory=lambda:p)
  assert settings.load_adaptive_gap_enabled()
  assert settings.load_accel_profile()=='sport'
  assert OriginParams(p).get_bool('EOPMTSCEnabled')


def test_migration_precedes_defaults_is_idempotent_and_preserves_explicit_off():
  p=Params(dict.fromkeys(EOP_LEGACY_KEYS.values(), True))
  p.values['EOPMSLCEnabled']=False
  migrate_origin_params(p)
  assert p.values['EOPMTSCEnabled'] is True
  assert p.values['EOPMSLCEnabled'] is False
  before=dict(p.values)
  migrate_origin_params(p)
  assert p.values==before

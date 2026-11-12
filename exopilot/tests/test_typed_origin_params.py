from exopilot.runtime.feature_keys import EOP_LEGACY_KEYS, OriginParams, migrate_origin_params
from exopilot.runtime.longitudinal_params import LongitudinalSettings


class Params:
  def __init__(self, values): self.values=values
  def get(self,key): return self.values.get(key)
  def put(self,key,value): self.values[key]=value
  def get_bool(self,key): return self.get(key) is True


def test_typed_booleans_and_strings_match_compiled_params_contract():
  p=Params({'AdaptiveGapEnabled':True,'AccelerationProfile':'sport','MTSCEnabled':True})
  settings=LongitudinalSettings(params_factory=lambda:p)
  assert settings.load_adaptive_gap_enabled()
  assert settings.load_accel_profile()=='sport'
  assert OriginParams(p).get_bool('MTSCEnabled')


def test_migration_precedes_defaults_is_idempotent_and_preserves_explicit_off():
  p=Params(dict.fromkeys(EOP_LEGACY_KEYS.values(), True))
  p.values['MSLCEnabled']=False
  migrate_origin_params(p)
  assert p.values['MTSCEnabled'] is True
  assert p.values['MSLCEnabled'] is False
  before=dict(p.values)
  migrate_origin_params(p)
  assert p.values==before

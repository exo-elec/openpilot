from types import SimpleNamespace as NS
from nagaspilot.runtime.lateral_authorization import lateral_authorized


def setup():
  return [NS(safetyModel='tesla',safetyParam=1,alternativeExperience=0,controlsAllowed=True)], [NS(safetyModel='tesla',safetyParam=1)]


def test_requires_live_matched_authority():
  states,configs=setup()
  assert lateral_authorized(states,configs,0,True)
  assert not lateral_authorized(states,configs,0,False)
  assert not lateral_authorized([],configs,0,True)
  assert not lateral_authorized(states,[],0,True)
  for key,value in [('safetyModel','toyota'),('safetyParam',2),('alternativeExperience',1),('controlsAllowed',False)]:
    states,configs=setup()
    setattr(states[0],key,value)
    assert not lateral_authorized(states,configs,0,True)


def test_passive_configurations_cannot_authorize_steering():
  states,configs=setup()
  configs[0].safetyModel='noOutput'
  assert not lateral_authorized(states,configs,0,True)

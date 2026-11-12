"""ALCC cannot create safety authority; it consumes the live Panda contract."""

def lateral_authorized(states, configs, alternative_experience, fresh):
  if not fresh or not configs or len(states) < len(configs):
    return False
  active = False
  for state, config in zip(states, configs, strict=False):
    # noOutput/silent configurations cannot authorize steering.
    if str(config.safetyModel) in ('noOutput', 'silent'):
      continue
    active = True
    if (state.safetyModel != config.safetyModel or state.safetyParam != config.safetyParam or
        state.alternativeExperience != alternative_experience or not state.controlsAllowed):
      return False
  return active

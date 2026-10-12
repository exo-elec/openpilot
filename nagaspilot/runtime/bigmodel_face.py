"""What modeld reads off a big model, for NGP10: the action function.

The parser and constants are NGP10's own (v0.10.0 Parser reads the big model's plan, lanes, lead and pose the same
way; `action` passes through unparsed). The action function is the only thing that differs: a big model outputs
an `action` head, which upstream's modeld uses directly (ngp_bigmodel_action.py); an output without one falls back
to NGP10's plan-based function, passed in because this module must not import modeld.

Not verified against a real big-model output: the parser's expected slices and the action head's units have to be
checked on a recorded route before the phone model may drive.
"""
from nagaspilot.controls.ngp_bigmodel_action import action_from_head


def make_action_function(fallback):
  def get_action_from_model(model_output, prev_action, lat_action_t, long_action_t, v_ego):
    if 'action' not in model_output:
      if fallback is None:
        raise RuntimeError("the big model has no action head and there is no plan-based fallback")
      return fallback(model_output, prev_action, lat_action_t, long_action_t, v_ego)
    from cereal import log
    from openpilot.selfdrive.controls.lib.drive_helpers import smooth_value
    curvature, accel, stop = action_from_head(model_output['action'], v_ego, prev_action.desiredAcceleration,
                                              prev_action.desiredCurvature, smooth_value)
    return log.ModelDataV2.Action(desiredCurvature=curvature, desiredAcceleration=accel, shouldStop=stop)

  return get_action_from_model

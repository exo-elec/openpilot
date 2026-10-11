"""What modeld reads off a big model (jetlink.openpilot.interface.ModelFace), for NGP10.

The parser and constants are NGP10's own (v0.10.0 Parser reads the big model's plan, lanes, lead and pose the same
way; `action` passes through unparsed). The action function is the only thing that differs: a big model outputs
an `action` head, which upstream's modeld uses directly (ngp_bigmodel_action.py); an output without one falls back
to NGP10's plan-based function, passed in because this module must not import modeld.

Not verified against a real big-model output: the parser's expected slices and the action head's units have to be
checked on a recorded route before the phone model may drive.
"""
from nagaspilot.controls.ngp_bigmodel_action import BIG_LAT_SMOOTH_SECONDS, BIG_LONG_SMOOTH_SECONDS, action_from_head


def frame_size(cam_w: int, cam_h: int) -> int:
  """The camera buffer's size in bytes (NV12, camerad's 128/32/16 alignment). Jetlink's tinygrad warp checks it;
  NGP10's OpenCL warp reads the VisionBuf itself, so this is informational."""
  stride = -(-cam_w // 128) * 128
  y_rows = -(-cam_h // 32) * 32
  uv_rows = -(-(cam_h // 2) // 16) * 16
  return stride * (y_rows + uv_rows)


def make_action_function(fallback):
  from cereal import log
  from openpilot.selfdrive.controls.lib.drive_helpers import smooth_value

  def get_action_from_model(model_output, prev_action, lat_action_t, long_action_t, v_ego):
    if 'action' not in model_output:
      if fallback is None:
        raise RuntimeError("the big model has no action head and there is no plan-based fallback")
      return fallback(model_output, prev_action, lat_action_t, long_action_t, v_ego)
    curvature, accel, stop = action_from_head(model_output['action'], v_ego, prev_action.desiredAcceleration,
                                              prev_action.desiredCurvature, smooth_value)
    return log.ModelDataV2.Action(desiredCurvature=curvature, desiredAcceleration=accel, shouldStop=stop)

  return get_action_from_model


def model_face(fallback_action=None):
  from jetlink.openpilot.interface import ModelFace
  from openpilot.selfdrive.modeld.constants import ModelConstants
  from openpilot.selfdrive.modeld.parse_model_outputs import Parser

  return ModelFace(parser=Parser, frame_size=frame_size, desire_len=ModelConstants.DESIRE_LEN, constants=ModelConstants,
                   lat_smooth_seconds=BIG_LAT_SMOOTH_SECONDS, long_smooth_seconds=BIG_LONG_SMOOTH_SECONDS,
                   get_action_from_model=make_action_function(fallback_action))

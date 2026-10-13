"""Driving action from the big model's `action` head.

Pure policy. Combined big models (upstream's driving model) output `action`, a
(curvature * v_ego^2, acceleration) pair the model itself decides, where the v0.10.0 model has only the
plan, from which NGP10's `get_action_from_model` derives the action. Ported from upstream's
`get_action_from_model` (commit 084747c75, `action` branch): the same conversion, stop rule and smoothing,
with the smoothing function and constants passed in so this module imports nothing from `selfdrive`.

The big model's smoothing is upstream's, not NGP10's small-model value: LAT 0.0 s and LONG 0.3 s.
"""
BIG_LAT_SMOOTH_SECONDS = 0.0
BIG_LONG_SMOOTH_SECONDS = 0.3
MIN_LAT_CONTROL_SPEED = 0.3  # m/s, as modeld
MODEL_DT = 0.05  # 20 Hz (modeld's DT_MDL)


def should_stop(v_ego: float, a_target: float) -> bool:
  return bool(v_ego < 0.3 and a_target < 0.1)


def action_from_head(action, v_ego: float, prev_accel: float, prev_curvature: float, smooth,
                     lat_smooth_s: float = BIG_LAT_SMOOTH_SECONDS,
                     long_smooth_s: float = BIG_LONG_SMOOTH_SECONDS) -> tuple[float, float, bool]:
  """(desired_curvature, desired_acceleration, should_stop) from `action[0] = (curv * v^2, accel)`.

  `smooth(value, previous, tau)` is drive_helpers.smooth_value.
  """
  desired_accel = float(action[0, 1])
  desired_curvature = float(action[0, 0]) / max(1.0, v_ego) ** 2
  stop = should_stop(v_ego, desired_accel)
  desired_accel = smooth(desired_accel, prev_accel, long_smooth_s)
  if v_ego > MIN_LAT_CONTROL_SPEED:
    desired_curvature = smooth(desired_curvature, prev_curvature, lat_smooth_s)
  else:
    desired_curvature = prev_curvature
  return float(desired_curvature), float(desired_accel), stop


def action_times(lateral_delay: float, longitudinal_actuator_delay: float,
                 lat_smooth_s: float = BIG_LAT_SMOOTH_SECONDS, long_smooth_s: float = BIG_LONG_SMOOTH_SECONDS,
                 dt: float = MODEL_DT) -> tuple[float, float]:
  """The big model's `action_t` input, (lateral, longitudinal) seconds ahead, as upstream builds it.

  delay (plus the model's own smoothing) + frame_delay (the camera frame is already a frame old) +
  action_delay (half a frame: the middle of the interval to the next model output). The model is conditioned
  on this input, so it uses the big model's smoothing, not NGP10's small-model value.
  """
  frame_delay, action_delay = dt, dt / 2
  return (lateral_delay + lat_smooth_s + frame_delay + action_delay,
          longitudinal_actuator_delay + long_smooth_s + frame_delay + action_delay)

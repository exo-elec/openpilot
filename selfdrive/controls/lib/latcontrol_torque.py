import json
import math
import os
import numpy as np
from openpilot.common.swaglog import cloudlog
from collections import deque

from cereal import log
from opendbc.car import get_friction
from opendbc.car.interfaces import FRICTION_THRESHOLD
from openpilot.common.constants import ACCELERATION_DUE_TO_GRAVITY
from openpilot.selfdrive.controls.lib.latcontrol import LatControl
from openpilot.common.pid import PIDController
from openpilot.selfdrive.controls.lib.nnlc.model import NNTorqueModel
from openpilot.selfdrive.controls.lib.nnlc.helpers import get_nn_model_path
from openpilot.common.params import Params
from openpilot.selfdrive.modeld.constants import ModelConstants

# --- NNFF constants (merged from FrogPilot) ---
_T_DIFFS = np.diff(ModelConstants.T_IDXS)
ERROR_BLEND_BP = [1.0, 2.0]
ERROR_BLEND_V = [0.0, 1.0]
FRICTION_LOOKAHEAD_BP = [12.0, 36.0]
FRICTION_LOOKAHEAD_V = [1.4, 2.0]
NNFF_PAST_TIMES = [-0.3, -0.2, -0.1]
NNFF_FUTURE_TIMES = [0.3, 0.6, 1.0, 1.5]

LOW_SPEED_X = [0, 12, 24, 36]
LOW_SPEED_Y = [15, 13, 10, 5]


def _sign(x: float) -> float:
  return 1.0 if x > 0 else -1.0 if x < 0 else 0.0


def _get_predicted_lateral_jerk(lat_accels, t_diffs):
  if len(lat_accels) < 2 or len(t_diffs) < 1:
    return []
  jerks = []
  for i in range(min(len(lat_accels), len(t_diffs))):
    if i + 1 < len(lat_accels) and t_diffs[i] > 0.001:
      jerks.append((lat_accels[i + 1] - lat_accels[i]) / t_diffs[i])
    else:
      jerks.append(0.0)
  return jerks


def _get_lookahead_value(future_values, current_value):
  if not future_values:
    return current_value
  current_sign = _sign(current_value)
  for v in future_values:
    if _sign(v) == current_sign and abs(v) > abs(current_value):
      return v
  return future_values[-1]


class LatControlTorque(LatControl):
  def __init__(self, CP, CI):
    super().__init__(CP, CI)
    self.torque_params = CP.lateralTuning.torque.as_builder()
    self.torque_from_lateral_accel = CI.torque_from_lateral_accel() if CI else lambda lat_accel, params: lat_accel
    self.lateral_accel_from_torque = CI.lateral_accel_from_torque() if CI else lambda torque, params: torque
    self.pid = PIDController(self.torque_params.kp, self.torque_params.ki,
                             k_f=self.torque_params.kf)
    self.update_limits()
    self.steering_angle_deadzone_deg = self.torque_params.steeringAngleDeadzoneDeg

    self.nn_model = None
    self.model_data = None
    self._init_nn_model(CP)

    # History deques for NNFF temporal features (100 Hz -> 0.01s per frame)
    hist_frames = [int(abs(t) * 100) for t in NNFF_PAST_TIMES]
    self._history_frame_offsets = [hist_frames[0] - f for f in hist_frames]
    maxlen = max(hist_frames[0], 10)
    self._lat_accel_deque = deque(maxlen=maxlen)
    self._roll_deque = deque(maxlen=maxlen)

    # NNFF friction scaling (tunable via model metadata or params in future)
    self._lat_accel_friction_factor = 0.7
    self._lat_jerk_friction_factor = 0.4
    self._nn_friction_override = False

  def _init_nn_model(self, CP):
    if not Params().get_bool("NeuralNetworkLateralControl"):
      return
    model_path, model_name, exact_match = get_nn_model_path(CP.carFingerprint)
    if model_path and os.path.isfile(model_path):
      try:
        self.nn_model = NNTorqueModel(model_path)
      except Exception as e:
        cloudlog.error(f"NN model load failed: {e}")
        self.nn_model = None
        return
      # Optional metadata from sidecar JSON
      meta_path = model_path + '.json'
      if os.path.isfile(meta_path):
        try:
          with open(meta_path, 'r') as f:
            meta = json.load(f)
          self._nn_friction_override = meta.get('friction_override', False)
        except Exception as e:
          cloudlog.error(f"NN metadata load failed: {e}")

  def set_model_data(self, model_data):
    self.model_data = model_data

  @staticmethod
  def _roll_pitch_adjust(roll: float, pitch: float) -> float:
    return roll * math.cos(pitch)

  def _build_nn_inputs(self, CS, desired_lateral_accel, actual_lateral_accel, roll, pitch,
                       setpoint, measurement, lateral_jerk_setpoint, lateral_jerk_measurement):
    v_ego = CS.vEgo
    roll_adj = self._roll_pitch_adjust(roll, pitch)

    past_rolls = [self._roll_deque[min(len(self._roll_deque) - 1, offset)] for offset in self._history_frame_offsets]
    past_lat_accels = [self._lat_accel_deque[min(len(self._lat_accel_deque) - 1, offset)] for offset in self._history_frame_offsets]

    future_rolls = [roll_adj] * len(NNFF_FUTURE_TIMES)
    future_lat_accels = [desired_lateral_accel] * len(NNFF_FUTURE_TIMES)

    if self.model_data is not None:
      ori = getattr(self.model_data, 'orientation', None)
      acc = getattr(self.model_data, 'acceleration', None)
      for i, ft in enumerate(NNFF_FUTURE_TIMES):
        adjusted_t = ft + 0.5 * CS.aEgo * (ft / max(CS.vEgo, 1.0))
        if ori is not None and len(ori.x) > 0 and len(ori.y) > 0:
          future_rolls[i] = self._roll_pitch_adjust(
            float(np.interp(adjusted_t, ModelConstants.T_IDXS, ori.x)) + roll,
            float(np.interp(adjusted_t, ModelConstants.T_IDXS, ori.y)) + pitch
          )
        if acc is not None and len(acc.y) > 0:
          future_lat_accels[i] = float(np.interp(adjusted_t, ModelConstants.T_IDXS[:len(acc.y)], acc.y))

    common = past_rolls + future_rolls
    base = [v_ego, roll_adj]
    pf_len = len(NNFF_PAST_TIMES) + len(NNFF_FUTURE_TIMES)

    setpoint_in = base[:1] + [setpoint, lateral_jerk_setpoint] + base[1:] + [setpoint] * pf_len + common
    measurement_in = base[:1] + [measurement, lateral_jerk_measurement] + base[1:] + [measurement] * pf_len + common
    err_delta = setpoint - measurement
    err_jerk = lateral_jerk_setpoint - lateral_jerk_measurement
    error_in = [v_ego, err_delta, err_jerk, roll_adj] + [err_delta] * pf_len + common

    error = desired_lateral_accel - actual_lateral_accel
    friction_in = self._lat_accel_friction_factor * error + self._lat_jerk_friction_factor * lateral_jerk_setpoint
    ff_in = [v_ego, desired_lateral_accel, friction_in, roll_adj] + past_lat_accels + future_lat_accels + common

    return setpoint_in, measurement_in, error_in, ff_in

  def update_live_torque_params(self, latAccelFactor, latAccelOffset, friction):
    self.torque_params.latAccelFactor = latAccelFactor
    self.torque_params.latAccelOffset = latAccelOffset
    self.torque_params.friction = friction
    self.update_limits()

  def update_limits(self):
    self.pid.set_limits(self.lateral_accel_from_torque(self.steer_max, self.torque_params),
                        self.lateral_accel_from_torque(-self.steer_max, self.torque_params))

  def update(self, active, CS, VM, params, steer_limited_by_safety, desired_curvature, curvature_limited):
    pid_log = log.ControlsState.LateralTorqueState.new_message()
    if not active:
      return 0.0, 0.0, pid_log

    actual_curvature = -VM.calc_curvature(math.radians(CS.steeringAngleDeg - params.angleOffsetDeg), CS.vEgo, params.roll)
    roll_compensation = params.roll * ACCELERATION_DUE_TO_GRAVITY
    curvature_deadzone = abs(VM.calc_curvature(math.radians(self.steering_angle_deadzone_deg), CS.vEgo, 0.0))
    desired_lateral_accel = desired_curvature * CS.vEgo ** 2
    actual_lateral_accel = actual_curvature * CS.vEgo ** 2
    lateral_accel_deadzone = curvature_deadzone * CS.vEgo ** 2

    low_speed_factor = np.interp(CS.vEgo, LOW_SPEED_X, LOW_SPEED_Y) ** 2
    setpoint = desired_lateral_accel + low_speed_factor * desired_curvature
    measurement = actual_lateral_accel + low_speed_factor * actual_curvature
    gravity_adjusted_lateral_accel = desired_lateral_accel - roll_compensation

    self._lat_accel_deque.append(desired_lateral_accel)
    self._roll_deque.append(params.roll)

    pid_log.error = float(setpoint - measurement)
    ff = gravity_adjusted_lateral_accel
    ff += get_friction(desired_lateral_accel - actual_lateral_accel, lateral_accel_deadzone, FRICTION_THRESHOLD,
                       self.torque_params, friction_compensation=True)

    if self.nn_model is not None:
      try:
        pitch = 0.0
        if self.model_data is not None:
          ori = getattr(self.model_data, 'orientation', None)
          if ori is not None and len(ori.y) > 0:
            pitch = float(ori.y[0])

        lookahead_jerk = 0.0
        actual_jerk = 0.0
        if self.model_data is not None:
          acc = getattr(self.model_data, 'acceleration', None)
          if acc is not None and len(acc.y) >= 2:
            rate = -VM.calc_curvature(math.radians(CS.steeringRateDeg), CS.vEgo, 0.0)
            actual_jerk = rate * CS.vEgo ** 2

            lookahead_s = float(np.interp(CS.vEgo, FRICTION_LOOKAHEAD_BP, FRICTION_LOOKAHEAD_V))
            t_lookahead_idx = min(
              next((i for i, t in enumerate(ModelConstants.T_IDXS) if t > lookahead_s), len(ModelConstants.T_IDXS) - 1),
              len(acc.y)  # predicted has len(acc.y)-1 elements; guard against over-indexing
            )
            predicted = _get_predicted_lateral_jerk(acc.y, _T_DIFFS)
            desired_jerk = (float(np.interp(0.1, ModelConstants.T_IDXS, acc.y)) - desired_lateral_accel) / 0.1
            lookahead_jerk = _get_lookahead_value(predicted[1:t_lookahead_idx], desired_jerk)

        jerk_setpoint = self._lat_jerk_friction_factor * lookahead_jerk
        jerk_measurement = self._lat_jerk_friction_factor * actual_jerk

        sp_in, meas_in, err_in, ff_in = self._build_nn_inputs(
          CS, desired_lateral_accel, actual_lateral_accel, params.roll, pitch,
          setpoint, measurement, jerk_setpoint, jerk_measurement
        )

        # Validate all input sizes before any inference
        if (len(ff_in) == self.nn_model.input_size and
            len(sp_in) == self.nn_model.input_size and
            len(meas_in) == self.nn_model.input_size):
          # Error in torque space (nonlinear rack compensation)
          pid_log.error = float(self.nn_model.evaluate(sp_in) - self.nn_model.evaluate(meas_in))

          # Blend stronger NN error response at high lateral accel
          blend = float(np.interp(abs(desired_lateral_accel), ERROR_BLEND_BP, ERROR_BLEND_V))
          if blend > 0.0 and len(err_in) == self.nn_model.input_size:
            err_torque = self.nn_model.evaluate(err_in)
            if _sign(pid_log.error) == _sign(err_torque) and abs(pid_log.error) < abs(err_torque):
              pid_log.error = pid_log.error * (1.0 - blend) + err_torque * blend

          # Feedforward via NN with friction-compensated input
          nn_torque = float(np.clip(self.nn_model.evaluate(ff_in), -self.steer_max, self.steer_max))
          ff = self.lateral_accel_from_torque(nn_torque, self.torque_params)
          if self._nn_friction_override:
            ff += self.torque_from_lateral_accel(0.0, self.torque_params)
      except Exception:
        cloudlog.exception("NN model inference failed; falling back to PID")

    freeze_integrator = steer_limited_by_safety or CS.steeringPressed or CS.vEgo < 5
    output_lataccel = self.pid.update(pid_log.error, feedforward=ff, speed=CS.vEgo, freeze_integrator=freeze_integrator)
    output_torque = self.torque_from_lateral_accel(output_lataccel, self.torque_params)

    pid_log.active = True
    pid_log.p = float(self.pid.p)
    pid_log.i = float(self.pid.i)
    pid_log.d = float(self.pid.d)
    pid_log.f = float(self.pid.f)
    pid_log.output = float(-output_torque)
    pid_log.actualLateralAccel = float(actual_lateral_accel)
    pid_log.desiredLateralAccel = float(desired_lateral_accel)
    pid_log.saturated = bool(self._check_saturation(self.steer_max - abs(output_torque) < 1e-3, CS, steer_limited_by_safety, curvature_limited))

    return -output_torque, 0.0, pid_log

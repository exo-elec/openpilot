#!/usr/bin/env python3
import math
import numpy as np

import time

import cereal.messaging as messaging
from cereal import custom
from opendbc.car.interfaces import ACCEL_MIN, ACCEL_MAX
from openpilot.common.constants import CV
from openpilot.common.filter_simple import FirstOrderFilter
from openpilot.common.params import Params
from openpilot.common.realtime import DT_MDL
from openpilot.selfdrive.modeld.constants import ModelConstants
from openpilot.selfdrive.controls.lib.longcontrol import LongCtrlState
from openpilot.selfdrive.controls.lib.longitudinal_mpc_lib.long_mpc import LongitudinalMpc
from openpilot.selfdrive.controls.lib.longitudinal_mpc_lib.long_mpc import T_IDXS as T_IDXS_MPC
from openpilot.selfdrive.controls.lib.drive_helpers import CONTROL_N, get_accel_from_plan
from openpilot.selfdrive.car.cruise import V_CRUISE_MAX, V_CRUISE_UNSET
from openpilot.common.swaglog import cloudlog
from nagaspilot.selfdrive.controls.lib.np_tsc_controller import NpTscController
from nagaspilot.selfdrive.controls.lib.np_dem_controller import NpDemController

LON_MPC_STEP = 0.2  # first step is 0.2s
A_CRUISE_MAX_VALS = [1.6, 1.2, 0.8, 0.6]
A_CRUISE_MAX_BP = [0., 10.0, 25., 40.]
CONTROL_N_T_IDX = ModelConstants.T_IDXS[:CONTROL_N]
ALLOW_THROTTLE_THRESHOLD = 0.4
MIN_ALLOW_THROTTLE_SPEED = 2.5

# Lookup table for turns
_A_TOTAL_MAX_V = [1.7, 3.2]
_A_TOTAL_MAX_BP = [20., 40.]

PLAN_EXT_SOURCE_MAP = {
  'cruise': custom.LongitudinalPlanExt.LongitudinalPlanExtSource.cruise,
  'lead0': custom.LongitudinalPlanExt.LongitudinalPlanExtSource.lead0,
  'lead1': custom.LongitudinalPlanExt.LongitudinalPlanExtSource.lead1,
  'lead2': custom.LongitudinalPlanExt.LongitudinalPlanExtSource.lead2,
  'e2e': custom.LongitudinalPlanExt.LongitudinalPlanExtSource.e2e,
  'turn': custom.LongitudinalPlanExt.LongitudinalPlanExtSource.turn,
  'limit': custom.LongitudinalPlanExt.LongitudinalPlanExtSource.limit,
  'turnlimit': custom.LongitudinalPlanExt.LongitudinalPlanExtSource.turnlimit,
}

def get_max_accel(v_ego):
  return np.interp(v_ego, A_CRUISE_MAX_BP, A_CRUISE_MAX_VALS)

def get_coast_accel(pitch):
  return np.sin(pitch) * -5.65 - 0.3  # fitted from data using xx/projects/allow_throttle/compute_coast_accel.py


def limit_accel_in_turns(v_ego, angle_steers, a_target, CP):
  """
  This function returns a limited long acceleration allowed, depending on the existing lateral acceleration
  this should avoid accelerating when losing the target in turns
  """
  # FIXME: This function to calculate lateral accel is incorrect and should use the VehicleModel
  # The lookup table for turns should also be updated if we do this
  a_total_max = np.interp(v_ego, _A_TOTAL_MAX_BP, _A_TOTAL_MAX_V)
  a_y = v_ego ** 2 * angle_steers * CV.DEG_TO_RAD / (CP.steerRatio * CP.wheelbase)
  a_x_allowed = math.sqrt(max(a_total_max ** 2 - a_y ** 2, 0.))

  return [a_target[0], min(a_target[1], a_x_allowed)]


class LongitudinalPlanner:
  def __init__(self, CP, init_v=0.0, init_a=0.0, dt=DT_MDL):
    self.CP = CP
    self.mpc = LongitudinalMpc(dt=dt)
    # TODO remove mpc modes when TR released
    self.mpc.mode = 'acc'
    self.fcw = False
    self.dt = dt
    self.allow_throttle = True

    self.a_desired = init_a
    self.v_desired_filter = FirstOrderFilter(init_v, 2.0, self.dt)
    self.prev_accel_clip = [ACCEL_MIN, ACCEL_MAX]
    self.output_a_target = 0.0
    self.output_should_stop = False

    self.v_desired_trajectory = np.zeros(CONTROL_N)
    self.a_desired_trajectory = np.zeros(CONTROL_N)
    self.j_desired_trajectory = np.zeros(CONTROL_N)
    self.solverExecutionTime = 0.0
    self.dem = NpDemController(CP)
    self.params = Params()
    self.tsc = NpTscController(CP)
    self.tsc.set_enabled(True)
    self.frame = 0  # Frame counter for DEM logging
    self._vision_turn_speed = init_v
    self._vision_turn_source = 'cruise'
    self._vision_turn_active = False
    self._vision_turn_enabled = True
    self._vision_turn_last_param_read = -10.0
    self._path_w_lines_x = []
    self._path_w_lines_y = []

  @staticmethod
  def parse_model(model_msg):
    if (len(model_msg.position.x) == ModelConstants.IDX_N and
      len(model_msg.velocity.x) == ModelConstants.IDX_N and
      len(model_msg.acceleration.x) == ModelConstants.IDX_N):
      x = np.interp(T_IDXS_MPC, ModelConstants.T_IDXS, model_msg.position.x)
      v = np.interp(T_IDXS_MPC, ModelConstants.T_IDXS, model_msg.velocity.x)
      a = np.interp(T_IDXS_MPC, ModelConstants.T_IDXS, model_msg.acceleration.x)
      j = np.zeros(len(T_IDXS_MPC))
    else:
      x = np.zeros(len(T_IDXS_MPC))
      v = np.zeros(len(T_IDXS_MPC))
      a = np.zeros(len(T_IDXS_MPC))
      j = np.zeros(len(T_IDXS_MPC))
    if len(model_msg.meta.disengagePredictions.gasPressProbs) > 1:
      throttle_prob = model_msg.meta.disengagePredictions.gasPressProbs[1]
    else:
      throttle_prob = 1.0
    return x, v, a, j, throttle_prob

  def update(self, sm):
    self.frame += 1  # Increment frame counter
    v_ego = sm['carState'].vEgo

    # --- Calculate current cycle variables needed for mode decision ---
    x, v, a, j, throttle_prob = self.parse_model(sm['modelV2'])
    # Don't clip at low speeds since throttle_prob doesn't account for creep
    self.allow_throttle = throttle_prob > ALLOW_THROTTLE_THRESHOLD or v_ego <= MIN_ALLOW_THROTTLE_SPEED

    # --- Mode Decision Logic: DEM has full control ---
    # Enable DEM by default for experimental mode functionality
    if not self.dem.enabled:
      self.dem.set_enabled(True)
      cloudlog.info("DEM enabled for longitudinal control")

    # Use DEM for mode decision when enabled
    if self.dem.enabled and self.dem.active():
      # Update DEM with current sensor data
      self.dem.update(sm)
      mode = self.dem.get_mode()
      dem_health = self.dem.get_health_status()
      
      # Log DEM decisions for debugging
      if self.frame % 50 == 0:  # Every 2.5 seconds
        cloudlog.debug(f"DEM Health: overall={dem_health['overall_health']:.3f}, "
                      f"mode={dem_health['current_mode']}, "
                      f"stability={dem_health['scenario_stability']:.3f}")
    else:
      # Default to ACC mode when DEM is not active
      mode = 'acc'
      dem_health = {'overall_health': 0.0, 'current_mode': 'off', 'scenario_stability': 0.0}

    if len(sm['carControl'].orientationNED) == 3:
      accel_coast = get_coast_accel(sm['carControl'].orientationNED[1])
    else:
      accel_coast = ACCEL_MAX

    v_ego = sm['carState'].vEgo
    v_cruise_kph = min(sm['carState'].vCruise, V_CRUISE_MAX)
    v_cruise = v_cruise_kph * CV.KPH_TO_MS
    v_cruise_plan = v_cruise
    v_cruise_initialized = sm['carState'].vCruise != V_CRUISE_UNSET

    long_control_off = sm['controlsState'].longControlState == LongCtrlState.off
    force_slow_decel = sm['controlsState'].forceDecel

    # Reset current state when not engaged, or user is controlling the speed
    reset_state = long_control_off if self.CP.openpilotLongitudinalControl else not sm['selfdriveState'].enabled
    # PCM cruise speed may be updated a few cycles later, check if initialized
    reset_state = reset_state or not v_cruise_initialized

    # Update turn controller before using the cruise target in downstream logic
    # Refresh user toggle periodically (default enabled when unset)
    now = time.monotonic()
    if now - self._vision_turn_last_param_read > 2.0:
      raw = self.params.get("np_tsc_enable")
      enabled = True if raw is None else raw == b"1"
      if enabled != self._vision_turn_enabled:
        self._vision_turn_enabled = enabled
      self._vision_turn_last_param_read = now

    self.tsc.set_enabled(self._vision_turn_enabled)

    self._vision_turn_source = 'cruise'
    self._vision_turn_speed = v_cruise_plan
    self._vision_turn_active = False

    # Update unified turn speed controller (EnhancedPilot-style fusion)
    self.tsc.update(sm, not reset_state, v_ego, sm['carState'].aEgo, v_cruise_plan)
    if self.tsc.is_active:
      self._vision_turn_active = True
      self._vision_turn_speed = min(v_cruise_plan, self.tsc.v_turn)
      if self._vision_turn_speed < v_cruise_plan - 0.1:
        self._vision_turn_source = 'tsc'
    else:
      self._vision_turn_speed = self.tsc.v_turn

    v_cruise_plan = min(v_cruise_plan, self._vision_turn_speed)



    # No change cost when user is controlling the speed, or when standstill
    prev_accel_constraint = not (reset_state or sm['carState'].standstill)

    if mode == 'acc':
      accel_clip = [ACCEL_MIN, get_max_accel(v_ego)]
      steer_angle_without_offset = sm['carState'].steeringAngleDeg - sm['liveParameters'].angleOffsetDeg
      accel_clip = limit_accel_in_turns(v_ego, steer_angle_without_offset, accel_clip, self.CP)
    else:
      accel_clip = [ACCEL_MIN, ACCEL_MAX]

    if self._vision_turn_active:
      accel_clip[0] = min(accel_clip[0], self.tsc.a_target)

    if reset_state:
      self.v_desired_filter.x = v_ego
      # Clip aEgo to cruise limits to prevent large accelerations when becoming active
      self.a_desired = np.clip(sm['carState'].aEgo, accel_clip[0], accel_clip[1])

    # Prevent divergence, smooth in current v_ego
    self.v_desired_filter.x = max(0.0, self.v_desired_filter.update(v_ego))

    if not self.allow_throttle:
      clipped_accel_coast = max(accel_coast, accel_clip[0])
      clipped_accel_coast_interp = np.interp(v_ego, [MIN_ALLOW_THROTTLE_SPEED, MIN_ALLOW_THROTTLE_SPEED*2], [accel_clip[1], clipped_accel_coast])
      accel_clip[1] = min(accel_clip[1], clipped_accel_coast_interp)

    if force_slow_decel:
      v_cruise_plan = 0.0

    # Update personality for DEM when active
    if self.dem.enabled and self.dem.active():
      self.dem.set_personality(v_ego, sm['selfdriveState'].personality)
      personality_for_mpc = self.dem.personality
    else:
      personality_for_mpc = sm['selfdriveState'].personality

    # DEM telemetry for downstream consumers
    self.dem_active = self.dem.enabled and self.dem.active()
    self.dem_health_score = float(dem_health.get('overall_health', 0.0)) if self.dem.enabled else 0.0
    
    self.mpc.set_weights(prev_accel_constraint, personality=personality_for_mpc)
    self.mpc.set_cur_state(self.v_desired_filter.x, self.a_desired)
    self.mpc.update(sm['radarState'], v_cruise_plan, x, v, a, j, personality=personality_for_mpc)

    self._update_lateral_ext_path(sm['modelV2'])

    self.v_desired_trajectory = np.interp(CONTROL_N_T_IDX, T_IDXS_MPC, self.mpc.v_solution)
    self.a_desired_trajectory = np.interp(CONTROL_N_T_IDX, T_IDXS_MPC, self.mpc.a_solution)
    self.j_desired_trajectory = np.interp(CONTROL_N_T_IDX, T_IDXS_MPC[:-1], self.mpc.j_solution)

    # TODO counter is only needed because radar is glitchy, remove once radar is gone
    self.fcw = self.mpc.crash_cnt > 2 and not sm['carState'].standstill
    if self.fcw:
      cloudlog.info("FCW triggered")

    # Interpolate 0.05 seconds and save as starting point for next iteration
    a_prev = self.a_desired
    self.a_desired = float(np.interp(self.dt, CONTROL_N_T_IDX, self.a_desired_trajectory))
    self.v_desired_filter.x = self.v_desired_filter.x + self.dt * (self.a_desired + a_prev) / 2.0

    action_t =  self.CP.longitudinalActuatorDelay + DT_MDL
    output_a_target_mpc, output_should_stop_mpc = get_accel_from_plan(self.v_desired_trajectory, self.a_desired_trajectory, CONTROL_N_T_IDX,
                                                                        action_t=action_t, vEgoStopping=self.CP.vEgoStopping)
    output_a_target_e2e = sm['modelV2'].action.desiredAcceleration
    output_should_stop_e2e = sm['modelV2'].action.shouldStop

    if mode == 'acc':
      output_a_target = output_a_target_mpc
      self.output_should_stop = output_should_stop_mpc
    else:
      output_a_target = min(output_a_target_mpc, output_a_target_e2e)
      self.output_should_stop = output_should_stop_e2e or output_should_stop_mpc

    for idx in range(2):
      accel_clip[idx] = np.clip(accel_clip[idx], self.prev_accel_clip[idx] - 0.05, self.prev_accel_clip[idx] + 0.05)
    self.output_a_target = np.clip(output_a_target, accel_clip[0], accel_clip[1])
    self.prev_accel_clip = accel_clip

  def publish(self, sm, pm):
    plan_send = messaging.new_message('longitudinalPlan')

    plan_send.valid = sm.all_checks(service_list=['carState', 'controlsState', 'selfdriveState', 'radarState'])

    longitudinalPlan = plan_send.longitudinalPlan
    longitudinalPlan.modelMonoTime = sm.logMonoTime['modelV2']
    longitudinalPlan.processingDelay = (plan_send.logMonoTime / 1e9) - sm.logMonoTime['modelV2']
    longitudinalPlan.solverExecutionTime = self.mpc.solve_time

    longitudinalPlan.speeds = self.v_desired_trajectory.tolist()
    longitudinalPlan.accels = self.a_desired_trajectory.tolist()
    longitudinalPlan.jerks = self.j_desired_trajectory.tolist()

    longitudinalPlan.hasLead = sm['radarState'].leadOne.status
    longitudinalPlan.longitudinalPlanSource = self.mpc.source
    longitudinalPlan.fcw = self.fcw

    longitudinalPlan.aTarget = float(self.output_a_target)
    longitudinalPlan.shouldStop = bool(self.output_should_stop)
    longitudinalPlan.allowBrake = True
    longitudinalPlan.allowThrottle = bool(self.allow_throttle)

    pm.send('longitudinalPlan', plan_send)

    plan_ext_send = messaging.new_message('longitudinalPlanExt')
    plan_ext_send.valid = plan_send.valid
    plan_ext = plan_ext_send.longitudinalPlanExt

    plan_ext.visionTurnControllerState = self.tsc.current_state
    plan_ext.visionTurnSpeed = float(self.tsc.v_turn)
    # Map controller states are now integrated into the unified TSC
    plan_ext.mapTurnControllerState = self.tsc.current_state
    plan_ext.mapTurnSpeed = float(self.tsc.v_turn)
    # Map freshness/state passthrough
    plan_ext.mapDataStale = not getattr(self.tsc, "map_fresh", False)
    plan_ext.visionPlanIsBlended = self.mpc.mode == 'blended'

    source_key = self.mpc.source if self.mpc.source != 'cruise' else self._vision_turn_source
    plan_ext.longitudinalPlanExtSource = PLAN_EXT_SOURCE_MAP.get(
      source_key, custom.LongitudinalPlanExt.LongitudinalPlanExtSource.cruise)

    pm.send('npLongitudinalPlanExt', plan_ext_send)

    lateral_ext_send = messaging.new_message('lateralPlanExt')
    lateral_ext_send.valid = sm.all_checks(service_list=['carState', 'modelV2'])
    lateral_ext = lateral_ext_send.lateralPlanExt
    lateral_ext.dPathWLinesX = self._path_w_lines_x
    lateral_ext.dPathWLinesY = self._path_w_lines_y
    pm.send('npLateralPlanExt', lateral_ext_send)

  def _update_lateral_ext_path(self, model_msg):
    if model_msg is None or len(model_msg.position.x) == 0 or len(model_msg.position.y) == 0:
      self._path_w_lines_x = []
      self._path_w_lines_y = []
      return

    limit = min(len(model_msg.position.x), CONTROL_N)
    x_vals = [float(x) for x in model_msg.position.x[:limit]]
    y_vals = [float(y) for y in model_msg.position.y[:limit]]

    if len(x_vals) >= 4:
      self._path_w_lines_x = x_vals
      self._path_w_lines_y = y_vals
    else:
      self._path_w_lines_x = []
      self._path_w_lines_y = []

#!/usr/bin/env python3
import math
import time
import numpy as np

import cereal.messaging as messaging
from opendbc.car.interfaces import ACCEL_MIN, ACCEL_MAX
from openpilot.common.constants import CV
from openpilot.common.filter_simple import FirstOrderFilter
from openpilot.common.realtime import DT_MDL
from openpilot.selfdrive.modeld.constants import ModelConstants
from openpilot.selfdrive.controls.lib.longcontrol import LongCtrlState
from openpilot.selfdrive.controls.lib.longitudinal_mpc_lib.long_mpc import LongitudinalMpc
from openpilot.selfdrive.controls.lib.longitudinal_mpc_lib.long_mpc import T_IDXS as T_IDXS_MPC
from openpilot.selfdrive.controls.lib.drive_helpers import CONTROL_N, get_accel_from_plan
from openpilot.selfdrive.car.cruise import V_CRUISE_MAX, V_CRUISE_UNSET
from openpilot.common.swaglog import cloudlog
from nagaspilot.speed_zones import longitudinal_accel_max, longitudinal_jerk_up
from nagaspilot.controls.ngp_tja import TrafficJamAssist
from nagaspilot.controls.ngp_dlon import NGPDLON
# BRSC: Bumpy Road Speed Controller — vertical-IMU roughness policy, shared across
# EOP10/NGP10/EDP10 via nagaspilot/controls (see nagaspilot/controls/ngp_brsc.py).
# Interacts with v_cruise the same way TJA interacts with accel: it only ever
# tightens the clamp.
from nagaspilot.controls.ngp_brsc import NGPBRSC
# Lane Change Lead Handoff: pure-camera adjacent-lane lead tracking during
# laneChangeStarting. See nagaspilot/controls/ngp_lc_lead_handoff.py.
from nagaspilot.controls.ngp_lc_lead_handoff import NGPLeadHandoff
# VTSC: vision-only turn speed advisory (0-250m), comma-3-safe slice of EOP10's
# vtsc.py -- no learned-speed DB, no self-calibration. See ngp_vtsc.py.
from nagaspilot.controls.ngp_vtsc import NGPVTSC
# NSLC-equivalent: navigation-source speed-limit enforcement, matching EOP10's
# EOPNSLCEnabled (no panel toggle there either). Nav-only on this branch --
# NGP10 has no map-data source at all (see EOP10_PARITY_CANDIDATES.md's
# Tier 2.5 MTSC entry); MSLC is not portable here for the same reason.
from nagaspilot.controls.ngp_speed_policy import NGPSpeedPolicy, SpeedLimitObservation, SpeedLimitPolicy, SpeedLimitSource

LON_MPC_STEP = 0.2  # first step is 0.2s
A_CRUISE_MAX_VALS = [1.6, 1.2, 0.8, 0.6]
A_CRUISE_MAX_BP = [0., 10.0, 25., 40.]
CONTROL_N_T_IDX = ModelConstants.T_IDXS[:CONTROL_N]
ALLOW_THROTTLE_THRESHOLD = 0.4
MIN_ALLOW_THROTTLE_SPEED = 2.5

# Lookup table for turns
_A_TOTAL_MAX_V = [1.7, 3.2]
_A_TOTAL_MAX_BP = [20., 40.]


class NGPFlags:
  BRSC = 2 ** 3
  LC_LEAD_HANDOFF = 2 ** 4
  VTSC = 2 ** 5
  NSLC = 2 ** 6

# BRSC: only applies above walking speed and never cuts speed below a floor.
BRSC_MIN_V_EGO = 5.0        # m/s — below this, don't apply the speed cut
BRSC_MIN_SPEED_MS = 8.3     # m/s (~30 km/h) — never cut speed below this floor


def get_max_accel(v_ego):
  return min(np.interp(v_ego, A_CRUISE_MAX_BP, A_CRUISE_MAX_VALS), longitudinal_accel_max(v_ego))


# Adaptive acceleration -- merged from FrogPilot via EOP10's identical
# _apply_adaptive_accel_limit(). Clamps max accel at low speeds and ramps it
# off near the cruise setpoint for a more natural, less robotic feel.
# Always-on, no param, no schema change -- pure v_cruise/v_ego math, ported
# verbatim (EOP10_PARITY_CANDIDATES.md Tier 3).
ADAPTIVE_ACCEL_CITY_SPEED_LIMIT = 13.9  # m/s (~50 km/h)


def _apply_adaptive_accel_limit(raw_max_accel: float, v_cruise: float, v_ego: float) -> float:
  """Reduce max acceleration at low speeds and near cruise speed."""
  # Low-speed clamp: quarter max at standstill, half at 25 km/h, full at 50 km/h
  low_speed_limit = np.interp(v_ego, [0.0, ADAPTIVE_ACCEL_CITY_SPEED_LIMIT / 2, ADAPTIVE_ACCEL_CITY_SPEED_LIMIT],
                               [raw_max_accel / 4, raw_max_accel / 2, raw_max_accel])
  # Ramp-off near setpoint: reduce accel as we approach cruise speed
  ramp_off = np.interp(v_cruise - v_ego, [0.0, 1.0, 5.0], [0.0, 0.5, raw_max_accel])
  return min(raw_max_accel, low_speed_limit, ramp_off)


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
    self.tja = TrafficJamAssist(self.dt)
    self.tja_result = self.tja.update(init_v, None)

    self.v_desired_trajectory = np.zeros(CONTROL_N)
    self.a_desired_trajectory = np.zeros(CONTROL_N)
    self.j_desired_trajectory = np.zeros(CONTROL_N)
    self.solverExecutionTime = 0.0
    self.ngp_dlon = NGPDLON()
    self.ngp_dlon_result = {'mode': 'Disabled', 'e2e_enabled': False, 'force_stop': False}

    # BRSC: Bumpy Road Speed Controller (vertical-IMU roughness policy)
    self.brsc = NGPBRSC()
    self.brsc_result = None
    self.brsc_v_target = None

    # Lane Change Lead Handoff (pure camera)
    self.lc_handoff = NGPLeadHandoff()

    # VTSC: Vision Turn Speed Control (0-250m advisory)
    self.vtsc = NGPVTSC(enabled=False)
    self.vtsc_result = None
    self.vtsc_v_target = None

    # NSLC-equivalent: nav-source speed-limit enforcement (nav-only, see
    # ngp_speed_policy import comment above for why map isn't an option here).
    self.speed_policy = NGPSpeedPolicy(policy=SpeedLimitPolicy.NAVIGATION)
    self.speed_policy_result = None
    self.speed_policy_v_target = None

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

  def update(self, sm, ngp_flags=0):
    # BRSC: vertical-IMU roughness policy (always fed so its internal baseline/hold
    # state stays current; application is gated below). accel_max_full=1.0 makes
    # result.accel_max double as the [0-1] accel-scale fraction directly, matching
    # TJA's own accel_scale usage below.
    brsc_enabled = bool(ngp_flags & NGPFlags.BRSC)
    if sm.valid.get('accelerometer', False):
      az = sm['accelerometer'].acceleration.v[2]
      self.brsc_result = self.brsc.update(az, self.dt, accel_max_full=1.0)

    # DLON: always-on standard behavior of this branch, not user-selectable
    # (automatic ACC/E2E switching only -- no forced-mode override exists).
    self.ngp_dlon_result = self.ngp_dlon.update(sm, mpc_crash_cnt=getattr(self.mpc, 'crash_cnt', 0))
    mode = 'blended' if self.ngp_dlon_result['e2e_enabled'] else 'acc'

    if len(sm['carControl'].orientationNED) == 3:
      accel_coast = get_coast_accel(sm['carControl'].orientationNED[1])
    else:
      accel_coast = ACCEL_MAX

    v_ego = sm['carState'].vEgo
    self.tja_result = self.tja.update(v_ego, sm['radarState'].leadOne)

    v_cruise_kph = min(sm['carState'].vCruise, V_CRUISE_MAX)
    v_cruise = v_cruise_kph * CV.KPH_TO_MS
    v_cruise_initialized = sm['carState'].vCruise != V_CRUISE_UNSET

    long_control_off = sm['controlsState'].longControlState == LongCtrlState.off
    force_slow_decel = sm['controlsState'].forceDecel

    # Reset current state when not engaged, or user is controlling the speed
    reset_state = long_control_off if self.CP.openpilotLongitudinalControl else not sm['selfdriveState'].enabled
    # PCM cruise speed may be updated a few cycles later, check if initialized
    reset_state = reset_state or not v_cruise_initialized

    # No change cost when user is controlling the speed, or when standstill
    prev_accel_constraint = not (reset_state or sm['carState'].standstill)

    if mode == 'acc':
      max_accel = get_max_accel(v_ego)
      max_accel = _apply_adaptive_accel_limit(max_accel, v_cruise, v_ego)
      accel_clip = [ACCEL_MIN, max_accel]
      steer_angle_without_offset = sm['carState'].steeringAngleDeg - sm['liveParameters'].angleOffsetDeg
      accel_clip = limit_accel_in_turns(v_ego, steer_angle_without_offset, accel_clip, self.CP)
    else:
      accel_clip = [ACCEL_MIN, ACCEL_MAX]

    if reset_state:
      self.v_desired_filter.x = v_ego
      # Clip aEgo to cruise limits to prevent large accelerations when becoming active
      self.a_desired = np.clip(sm['carState'].aEgo, accel_clip[0], accel_clip[1])

    # Prevent divergence, smooth in current v_ego
    self.v_desired_filter.x = max(0.0, self.v_desired_filter.update(v_ego))
    x, v, a, j, throttle_prob = self.parse_model(sm['modelV2'])
    # Don't clip at low speeds since throttle_prob doesn't account for creep
    self.allow_throttle = throttle_prob > ALLOW_THROTTLE_THRESHOLD or v_ego <= MIN_ALLOW_THROTTLE_SPEED

    if not self.allow_throttle:
      clipped_accel_coast = max(accel_coast, accel_clip[0])
      clipped_accel_coast_interp = np.interp(v_ego, [MIN_ALLOW_THROTTLE_SPEED, MIN_ALLOW_THROTTLE_SPEED*2], [accel_clip[1], clipped_accel_coast])
      accel_clip[1] = min(accel_clip[1], clipped_accel_coast_interp)

    # BRSC: reduce cruise speed while rough-road hold is active. Gated on v_ego and
    # floored so a long rough stretch can't crawl the car below a safe minimum.
    self.brsc_v_target = None
    if (brsc_enabled and self.brsc_result is not None
        and self.brsc_result.active and v_ego > BRSC_MIN_V_EGO):
      self.brsc_v_target = max(v_cruise * self.brsc_result.speed_factor, BRSC_MIN_SPEED_MS)
      v_cruise = min(v_cruise, self.brsc_v_target)

    # VTSC: advisory vision-only turn speed, 0-250m. Only clamps v_cruise while
    # ENTERING/TURNING (see ngp_vtsc.py's state machine); target_speed is None
    # otherwise, matching TJA/BRSC's "only ever tightens" contract. No speed
    # floor here -- checked against EOP10's own application site
    # (longitudinal_planner.py's `if self.vtsc_v_target < v_cruise: v_cruise =
    # self.vtsc_v_target`), which has none either; this matches parity rather
    # than being a gap relative to it.
    vtsc_enabled = bool(ngp_flags & NGPFlags.VTSC)
    self.vtsc_v_target = None
    if sm.valid.get('modelV2', False):
      self.vtsc_result = self.vtsc.update(v_ego, sm['modelV2'], enabled=vtsc_enabled)
      self.vtsc_v_target = self.vtsc_result.target_speed
      if self.vtsc_v_target is not None:
        v_cruise = min(v_cruise, self.vtsc_v_target)

    # NSLC-equivalent: clamp v_cruise to the posted nav speed limit.
    # ngp_speed_policy.py's evaluate() never applies anything itself (it's a
    # pure resolver, like ngp_vtsc.py/ngp_mtsc.py); this is the one place
    # that acts on its suggestion. Uses min(v_cruise, target) rather than
    # evaluate()'s own suggested_cruise_mps, matching BRSC/VTSC's idiom
    # exactly -- decouples this call site from how evaluate() recomputes its
    # own copy of v_cruise internally (same result today since v_cruise is
    # always >= 0 here, but this avoids relying on that staying true).
    # Asymmetry vs. EOP10's MSLC/NSLC, noted rather than hidden: this is a
    # hard, instant, undebounced clamp on 1 Hz nav data -- no
    # driver_overriding concept, no offset, and no SpeedLimitConfirmation
    # (EOP10's nslc.py gates limit *changes* on driver confirmation; this
    # doesn't). Default off, opt-in, so this isn't a surprise until enabled.
    nslc_enabled = bool(ngp_flags & NGPFlags.NSLC)
    self.speed_policy_v_target = None
    if nslc_enabled and sm.valid.get('navInstruction', False):
      nav_limit = sm['navInstruction'].speedLimit  # m/s
      observations = (SpeedLimitObservation(source=SpeedLimitSource.NAVIGATION, limit_mps=float(nav_limit)),) if nav_limit > 0 else ()
      self.speed_policy_result = self.speed_policy.evaluate(v_ego, v_cruise, observations)
      self.speed_policy_v_target = self.speed_policy_result.resolved_limit_mps
      if self.speed_policy_v_target is not None:
        v_cruise = min(v_cruise, self.speed_policy_v_target)

    if force_slow_decel:
      v_cruise = 0.0

    self.mpc.set_weights(prev_accel_constraint, personality=sm['selfdriveState'].personality)
    self.mpc.set_cur_state(self.v_desired_filter.x, self.a_desired)

    # Lane Change Lead Handoff — if laneChangeStarting, replace radarState.leadOne
    # with the lead in the target lane so MPC tracks it instead of the old lane's lead.
    lc_lead_handoff_enabled = bool(ngp_flags & NGPFlags.LC_LEAD_HANDOFF)
    radar_state_for_mpc = sm['radarState']
    if sm.valid.get('modelV2', False):
      model_meta = sm['modelV2'].meta
      radar_state_for_mpc = self.lc_handoff.update(
        enabled=lc_lead_handoff_enabled,
        model_v2=sm['modelV2'],
        radar_state=sm['radarState'],
        lc_state=model_meta.laneChangeState,
        lc_dir=model_meta.laneChangeDirection,
        v_ego=v_ego,
        now=time.monotonic(),
      )

    self.mpc.update(radar_state_for_mpc, v_cruise, x, v, a, j, personality=sm['selfdriveState'].personality)

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

    if self.ngp_dlon_result.get('force_stop', False):
      self.output_should_stop = True

    for idx in range(2):
      accel_clip[idx] = np.clip(accel_clip[idx], self.prev_accel_clip[idx] - 0.05, self.prev_accel_clip[idx] + 0.05)
    # Limit only rising acceleration. Planner-requested braking remains
    # immediately available.
    if self.tja_result.active:
      output_a_target = min(output_a_target, longitudinal_accel_max(v_ego) * self.tja_result.accel_scale)
    # BRSC: cap positive accel while rough-road hold is active.
    if brsc_enabled and self.brsc_result is not None and self.brsc_result.active:
      accel_scale = min(max(self.brsc_result.accel_max, 0.0), 1.0)
      output_a_target = min(output_a_target, longitudinal_accel_max(v_ego) * accel_scale)
    if not reset_state:
      output_a_target = min(output_a_target,
                            self.output_a_target + longitudinal_jerk_up(v_ego) * self.tja_result.jerk_scale * self.dt)
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
    longitudinalPlan.ngpDlonMode = self.ngp_dlon_result['mode']
    longitudinalPlan.ngpDlonE2EEnabled = bool(self.ngp_dlon_result['e2e_enabled'])
    longitudinalPlan.ngpDlonForceStop = bool(self.ngp_dlon_result.get('force_stop', False))
    longitudinalPlan.ngpTjaActive = bool(self.tja_result.active)
    longitudinalPlan.ngpTjaCutIn = bool(self.tja_result.cut_in)
    longitudinalPlan.ngpTjaDesiredGap = float(self.tja_result.desired_gap)

    # BRSC debug info
    if self.brsc_result is not None:
      longitudinalPlan.ngpBrscActive = self.brsc_result.active
      longitudinalPlan.ngpBrscRoughness = float(self.brsc_result.roughness_rms)
    if self.brsc_v_target is not None:
      longitudinalPlan.ngpBrscSpeed = float(self.brsc_v_target)

    pm.send('longitudinalPlan', plan_send)

#!/usr/bin/env python3
import math
import time
import numpy as np

import cereal.messaging as messaging
from cereal import log
from openpilot.system.socketd.vehicle.car.vehicle_model import VehicleModel

# Upstream used opendbc.car.interfaces.ACCEL_MIN/ACCEL_MAX (generic per-car limits).
# EOP uses Tesla CarControllerParams directly (single-vehicle fork; opendbc removed).
from openpilot.system.socketd.vehicle.tesla.values import CarControllerParams
ACCEL_MIN = CarControllerParams.ACCEL_MIN
ACCEL_MAX = CarControllerParams.ACCEL_MAX
from openpilot.common.constants import CV
from openpilot.common.filter_simple import FirstOrderFilter
from openpilot.common.realtime import DT_MDL
from openpilot.selfdrive.modeld.constants import ModelConstants
from openpilot.selfdrive.controls.lib.longcontrol import LongCtrlState
from openpilot.selfdrive.controls.lib.longitudinal_mpc_lib.long_mpc import (
  LongitudinalMpc, T_IDXS as T_IDXS_MPC,
  get_T_FOLLOW, get_jerk_factor, STOP_DISTANCE, COMFORT_BRAKE,
)
from openpilot.selfdrive.controls.lib.drive_helpers import CONTROL_N, get_accel_from_plan
from openpilot.system.socketd.vehicle.car.cruise import V_CRUISE_MAX, V_CRUISE_UNSET
from openpilot.common.swaglog import cloudlog
from nagaspilot.speed_zones import (CRAWL_SPEED_MPS, HIGHWAY_SPEED_MPS, MAX_SPEED_MPS, URBAN_SPEED_MPS,
                                    longitudinal_accel_max, longitudinal_jerk_up)
from openpilot.selfdrive.controls.lib.tja import TrafficJamAssist

# EOP: Vision Turn Speed Control
from openpilot.selfdrive.controls.lib.vtsc import VTSC
# EOP: Dynamic Longitudinal Profile
from openpilot.selfdrive.controls.lib.dlon import DLON
# EOP: Map Turn Speed Control (includes learned speeds)
from openpilot.selfdrive.controls.lib.mtsc import MTSC
# EOP: Map Speed Limit Control (MSLC - OSM posted limits)
from openpilot.selfdrive.controls.lib.mslc import MSLC
# EOP: Navigation Speed Limit Control (NSLC - Mapbox/navd limits)
from openpilot.selfdrive.controls.lib.nslc import NSLC
# EOP: Traffic Light Speed Control
from openpilot.selfdrive.controls.lib.tlsc import TLSC
# EOP: Driver Preferences (speed offset, following distance)
from openpilot.selfdrive.controls.lib.driver_prefs import DriverPrefs
# EOP: SQSC - Surface Quality Speed Controller (road quality speed control)
from openpilot.selfdrive.controls.lib.sqsc import SQSC
# EOP: RCD - Road Condition Detection (speed limits for wet/icy/debris)
from openpilot.selfdrive.controls.lib.rcd import RCD
# EOP: Speed Limit Resolver (Topic 05 — offset + policy)
from openpilot.selfdrive.controls.lib.speed_limit_resolver import SpeedLimitResolver
# EOP: Lane Change Lead Handoff (pure camera — adjacent lead tracking during lane change)
from openpilot.selfdrive.controls.lib.lc_lead_handoff import LaneChangeLeadHandoff
# BRSC: Bumpy Road Speed Controller — vertical-IMU roughness policy, shared across
# EOP10/NGP10/EDP10 via nagaspilot/controls (see nagaspilot/controls/ngp_brsc.py).
# Interacts with v_cruise the same way VTSC/SQSC/RCD do: it only ever tightens the
# clamp already established by VTSC/MTSC's curve-speed blend earlier in update().
from nagaspilot.controls.ngp_brsc import NGPBRSC

LON_MPC_STEP = 0.2  # first step is 0.2s
# EOP: breakpoints aligned with CRAWL/URBAN/HIGHWAY/MAX (0/43/86/130 km/h).
# Upstream: [0, 10, 25, 40] m/s
A_CRUISE_MAX_BP = [CRAWL_SPEED_MPS, URBAN_SPEED_MPS, HIGHWAY_SPEED_MPS, MAX_SPEED_MPS]

# EOP: Acceleration profiles (merged from FrogPilot)
# Normal: stock conservative curve
# Eco: gentler curve for efficiency / smoother ride
# Sport: more aggressive curve for responsive feel
_A_CRUISE_PROFILES = {
  'normal': [1.6, 1.2, 0.8, 0.6],
  'eco':    [1.2, 0.9, 0.6, 0.4],
  'sport':  [2.0, 1.6, 1.2, 0.8],
}

CONTROL_N_T_IDX = ModelConstants.T_IDXS[:CONTROL_N]
ALLOW_THROTTLE_THRESHOLD = 0.4
MIN_ALLOW_THROTTLE_SPEED = 2.5

# Lookup table for turns
_A_TOTAL_MAX_V = [1.7, 3.2]
_A_TOTAL_MAX_BP = [HIGHWAY_SPEED_MPS, MAX_SPEED_MPS]

# Param caches (module-level to avoid per-frame I/O)
_accel_profile_cache = {'ts': 0.0, 'profile': 'normal'}
_adaptive_gap_cache = {'ts': 0.0, 'enabled': False}


def _load_accel_profile():
  """Read acceleration profile from params (cached, 2s TTL)."""
  global _accel_profile_cache
  now = time.monotonic()
  if now - _accel_profile_cache['ts'] < 2.0:
    return _accel_profile_cache['profile']
  from openpilot.common.params import Params
  p = Params().get("EOPAccelerationProfile")
  profile = p.decode('utf-8') if p else 'normal'
  if profile not in _A_CRUISE_PROFILES:
    profile = 'normal'
  _accel_profile_cache = {'ts': now, 'profile': profile}
  return profile


def _load_adaptive_gap_enabled():
  """Read adaptive gap toggle from params (cached, 2s TTL)."""
  global _adaptive_gap_cache
  now = time.monotonic()
  if now - _adaptive_gap_cache['ts'] < 2.0:
    return _adaptive_gap_cache['enabled']
  from openpilot.common.params import Params
  p = Params().get("EOPAdaptiveGapEnabled")
  enabled = p == b"1" if p else False
  _adaptive_gap_cache = {'ts': now, 'enabled': enabled}
  return enabled


def get_max_accel(v_ego):
  profile = _load_accel_profile()
  vals = _A_CRUISE_PROFILES[profile]
  return min(np.interp(v_ego, A_CRUISE_MAX_BP, vals), longitudinal_accel_max(v_ego))


# EOP: Adaptive acceleration — merged from FrogPilot
# Clamps max accel at low speeds and ramps off near cruise setpoint
# for more natural, less robotic acceleration feel.
CITY_SPEED_LIMIT = 13.9  # m/s (~50 km/h)

def _apply_adaptive_accel_limit(raw_max_accel: float, v_cruise: float, v_ego: float) -> float:
  """Reduce max acceleration at low speeds and near cruise speed."""
  # Low-speed clamp: quarter max at standstill, half at 25 km/h, full at 50 km/h
  low_speed_limit = np.interp(v_ego, [0.0, CITY_SPEED_LIMIT / 2, CITY_SPEED_LIMIT],
                               [raw_max_accel / 4, raw_max_accel / 2, raw_max_accel])
  # Ramp-off near setpoint: reduce accel as we approach cruise speed
  ramp_off = np.interp(v_cruise - v_ego, [0.0, 1.0, 5.0], [0.0, 0.5, raw_max_accel])
  return min(raw_max_accel, low_speed_limit, ramp_off)


# EOP: Slippery-weather risk gate — radar4d's 3-step weather severity
# (rain/snow/mud-dirt from precipitation clutter + wiper rate + windshield
# contamination) steps max acceleration down like a careful driver would as
# grip drops.
WEATHER_ACCEL_SCALE = (1.0, 0.8, 0.6, 0.4)  # by severity: clear/light/moderate/heavy


def _apply_weather_severity_limit(max_accel: float, severity: int) -> float:
  """Scale max acceleration by the radar4d weather severity level (0-3)."""
  level = min(max(int(severity), 0), len(WEATHER_ACCEL_SCALE) - 1)
  return max_accel * WEATHER_ACCEL_SCALE[level]


# BRSC: Bumpy Road Speed Controller — reduce speed/accel on rough pavement, detected
# from vertical IMU acceleration (nagaspilot.controls.ngp_brsc). Only applies above
# walking speed and never cuts speed below a floor, mirroring VTSC's MIN_VELOCITY.
BRSC_MIN_V_EGO = 5.0        # m/s — below this, don't apply the speed cut
BRSC_MIN_SPEED_MS = 8.3     # m/s (~30 km/h) — never cut speed below this floor
_brsc_enabled_cache = {'ts': 0.0, 'enabled': True}


def _load_brsc_enabled():
  """Read the BRSC toggle from Params (cached, 2s TTL)."""
  global _brsc_enabled_cache
  now = time.monotonic()
  if now - _brsc_enabled_cache['ts'] < 2.0:
    return _brsc_enabled_cache['enabled']
  from openpilot.common.params import Params
  p = Params().get("ngp_lon_brsc")
  enabled = p != b"0" if p else True
  _brsc_enabled_cache = {'ts': now, 'enabled': enabled}
  return enabled

def get_coast_accel(pitch):
  return np.sin(pitch) * -5.65 - 0.3  # fitted from data using xx/projects/allow_throttle/compute_coast_accel.py


def limit_accel_in_turns(v_ego, angle_steers, a_target, CP, VM=None):
  """
  Limit longitudinal acceleration based on total acceleration constraint in turns.

  Uses VehicleModel to properly calculate lateral acceleration from steering angle,
  accounting for tire slip and vehicle dynamics (not just kinematic bicycle model).

  Args:
    v_ego: Current speed [m/s]
    angle_steers: Steering wheel angle [deg]
    a_target: Target acceleration [m/s^2] as [min, max] tuple
    CP: CarParams
    VM: VehicleModel instance (created if None)

  Returns:
    Limited acceleration bounds [min, max]
  """
  # Use VehicleModel for accurate lateral acceleration calculation
  if VM is None:
    VM = VehicleModel(CP)

  a_total_max = np.interp(v_ego, _A_TOTAL_MAX_BP, _A_TOTAL_MAX_V)

  # Proper lateral acceleration using VehicleModel curvature
  # a_y = v^2 * curvature (accounts for tire slip, unlike simple kinematic model)
  curvature = VM.calc_curvature(angle_steers * CV.DEG_TO_RAD, v_ego, roll=0.0)
  a_y = v_ego ** 2 * abs(curvature)

  # Total acceleration constraint: a_x^2 + a_y^2 <= a_total_max^2
  a_x_allowed = math.sqrt(max(a_total_max ** 2 - a_y ** 2, 0.))

  return [a_target[0], min(a_target[1], a_x_allowed)]


class LongitudinalPlanner:
  def __init__(self, CP, init_v=0.0, init_a=0.0, dt=DT_MDL):
    self.CP = CP
    self.VM = VehicleModel(CP)  # EOP: VehicleModel for accurate lateral accel calculation
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

    self.v_desired_trajectory = np.zeros(CONTROL_N)
    self.a_desired_trajectory = np.zeros(CONTROL_N)
    self.j_desired_trajectory = np.zeros(CONTROL_N)
    self.solverExecutionTime = 0.0

    # EOP: VTSC initialization
    self.vtsc = VTSC()
    self.vtsc_v_target = None

    # EOP: DLON initialization
    self.dlon = DLON()
    self.dlon_e2e_enabled = False
    self.dlon_force_stop = False
    self.dlon_result = {'mode': 'Disabled', 'e2e_enabled': False}

    # EOP: MTSC initialization (Map-based curve speed with learned priority)
    self.mtsc = MTSC()
    self.mtsc_v_target = None

    # EOP: MSLC initialization (Map posted speed limits)
    self.mslc = MSLC()
    self.mslc_v_target = None

    # EOP: NSLC initialization (Navigation speed limits from Mapbox/navd)
    self.nslc = NSLC()
    self.nslc_v_target = None

    # EOP: Speed Limit Resolver (Topic 05) — picks source policy across MSLC/NSLC
    self.sl_resolver = SpeedLimitResolver()
    self.sl_resolved = None

    # EOP: TLSC initialization (Traffic light speed control)
    self.tlsc = TLSC()
    self.tlsc_v_target = None

    # EOP: Driver Preferences initialization
    self.driver_prefs = DriverPrefs()

    # EOP: SQSC initialization (Surface Quality Speed Controller)
    self.sqsc = SQSC()
    self.sqsc_v_target = None

    # EOP: RCD initialization (Road Condition Detection)
    self.rcd = RCD()
    self.rcd_v_target = None

    # BRSC: Bumpy Road Speed Controller (vertical-IMU roughness policy)
    self.brsc = NGPBRSC()
    self.brsc_result = None
    self.brsc_v_target = None

    # EOP: LHLC initialization (Long Horizon Lateral Controller - 500m Hybrid A* from pathd)
    self.lhlc_active = False
    self.lhlc_v_target = None

    # EOP: Adaptive Gap (dynamic t_follow modulation based on lead relative speed)
    self._adaptive_gap_enabled = _load_adaptive_gap_enabled()

    # EOP: Lane Change Lead Handoff (pure camera)
    self.lc_handoff = LaneChangeLeadHandoff()

    # EOP: Speed limit pre-announcement TTS deduplication
    self._sl_tts_announced_limit = None
    self._sl_tts_announced_distance = float('inf')

    # EOP: VTSC/MTSC handover smoothing
    # Prevents acceleration spikes during MTSC→VTSC transition at ~250m
    self.prev_vtsc_target = None
    self.prev_mtsc_target = None
    self.handover_active = False
    self.handover_distance = 0.0
    self.vtsc_using_learned = False
    self.mtsc_using_learned = False

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

  def _apply_speed_limit(self, v_cruise: float, v_limit: float | None) -> float:
    """Apply a speed limit to cruise speed if it's lower."""
    if v_limit is not None and v_limit < v_cruise:
      return v_limit
    return v_cruise

  def update(self, sm):
    # EOP: Refresh cached params
    self._adaptive_gap_enabled = _load_adaptive_gap_enabled()

    # EOP: DLON - Dynamic Longitudinal Profile
    # DLON can override experimental mode based on environmental triggers
    self.dlon_result = self.dlon.update(sm, mpc_crash_cnt=getattr(self.mpc, 'crash_cnt', 0))
    self.dlon_e2e_enabled = self.dlon_result['e2e_enabled']
    self.dlon_force_stop = self.dlon_result.get('force_stop', False)

    # DLON mode directly selects acc/blended based on profile (ACC/E2E/Dynamic)
    mode = 'blended' if self.dlon_e2e_enabled else 'acc'

    if len(sm['carControl'].orientationNED) == 3:
      accel_coast = get_coast_accel(sm['carControl'].orientationNED[1])
    else:
      accel_coast = ACCEL_MAX

    v_ego = sm['carState'].vEgo

    # BRSC: Bumpy Road Speed Controller — vertical-IMU roughness policy (always fed
    # so its internal baseline/hold state stays current; application is gated below).
    brsc_enabled = _load_brsc_enabled()
    if sm.valid.get('accelerometer', False):
      az = sm['accelerometer'].acceleration.v[2]
      # accel_max_full=1.0 makes result.accel_max double as the [0-1] accel-scale
      # fraction directly, since the real max_accel isn't computed until below.
      self.brsc_result = self.brsc.update(az, self.dt, accel_max_full=1.0)
    tja_result = self.tja.update(v_ego, sm['radarState'].leadOne)
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
      # EOP: Adaptive acceleration limit (low-speed clamp + ramp-off near cruise)
      max_accel = _apply_adaptive_accel_limit(max_accel, v_cruise, v_ego)
      # EOP: Slippery-weather risk gate — radar4d 3-step weather severity
      # (clutter + wiper + glass contamination) steps max accel down per level.
      if sm.valid.get('radar4d', False):
        max_accel = _apply_weather_severity_limit(max_accel, int(sm['radar4d'].weatherSeverity))
      # BRSC: cap positive accel while rough-road hold is active.
      if brsc_enabled and self.brsc_result is not None and self.brsc_result.active:
        accel_scale = min(max(self.brsc_result.accel_max, 0.0), 1.0)
        max_accel = max_accel * accel_scale
      accel_clip = [ACCEL_MIN, max_accel]
      steer_angle_without_offset = sm['carState'].steeringAngleDeg - sm['liveParameters'].angleOffsetDeg
      accel_clip = limit_accel_in_turns(v_ego, steer_angle_without_offset, accel_clip, self.CP, self.VM)
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

    if force_slow_decel:
      v_cruise = 0.0

    # EOP: VTSC - Vision Turn Speed Control (0-250m range, with learned speeds)
    # Get GPS coordinates for learned speed lookup
    lat, lon = 0.0, 0.0
    if sm.updated['liveLocationKalman']:
      loc = sm['liveLocationKalman']
      lat = loc.positionGeodetic.value[0]
      lon = loc.positionGeodetic.value[1]

    self.vtsc_v_target, vtsc_state, self.vtsc_using_learned = self.vtsc.update(v_ego, sm['modelV2'], lat, lon)

    # EOP: MTSC - Map Turn Speed Control (250-500m range, with learned speeds)
    # Uses learned driver speeds from database, falls back to OSM physics
    self.mtsc_v_target = None  # reset each cycle; only re-set if mapData is fresh
    if sm.updated['mapData']:
      mtsc_output = self.mtsc.update(sm['mapData'], v_ego, lat, lon)
      self.mtsc_v_target = mtsc_output['v_target']
      self.handover_distance = mtsc_output.get('distance', float('inf'))
      self.mtsc_using_learned = mtsc_output.get('using_learned', False)

    # EOP: Curve speed learning — DISABLED in real-time loop.
    # Database writes in 20 Hz control loops cause frame drops.
    # Learning moved to background thread in cslb.py (CurveDatabase._bg_writer).
    # To re-enable: call cslb.queue_curve_update(lat, lon, curvature, v_ego)
    # which enqueues for batch write every 5 seconds.
    # Placeholder — background learning handled by cslb

    # EOP: VTSC/MTSC handover smoothing (prevents acceleration spikes)
    # VTSC gets more precise as we get closer, so extend MTSC influence to 150m
    #
    # Range allocation:
    #   MTSC: 500m - 150m (long-range, less precise)
    #   Handover: 200m - 150m (smooth transition zone)
    #   VTSC: 150m - 0m (short-range, most precise)
    HANDOVER_START = 200.0  # meters - start blending MTSC→VTSC
    HANDOVER_END = 150.0    # meters - VTSC takes full control

    vtsc_active = self.vtsc_v_target is not None
    mtsc_active = self.mtsc_v_target is not None

    if vtsc_active and mtsc_active and self.handover_distance <= HANDOVER_START:
      # In transition zone (150-200m) - both controllers active
      # Blend smoothly from MTSC to VTSC as we get closer
      # VTSC gets more weight as distance decreases

      # Calculate blend factor (0.0 = all MTSC, 1.0 = all VTSC)
      # At 200m: factor = 0.0 (mostly MTSC)
      # At 150m: factor = 1.0 (mostly VTSC)
      if self.handover_distance >= HANDOVER_END:
        blend_factor = (HANDOVER_START - self.handover_distance) / (HANDOVER_START - HANDOVER_END)
        blend_factor = np.clip(blend_factor, 0.0, 1.0)
      else:
        blend_factor = 1.0  # Within 150m, use VTSC only

      # Weighted blend: closer = more VTSC influence
      # But always use the minimum (most conservative) of the two for safety
      if blend_factor < 0.5:
        # Closer to 200m - trust MTSC more, but cap at VTSC if it's lower
        blended_target = min(self.mtsc_v_target, self.vtsc_v_target * 1.1)  # 10% VTSC tolerance
      else:
        # Closer to 150m - trust VTSC more, but cap at MTSC if it's lower
        blended_target = min(self.vtsc_v_target, self.mtsc_v_target * 1.05)  # 5% MTSC tolerance

      # Rate limiting during handover to prevent acceleration spikes
      if self.prev_vtsc_target is not None and self.prev_mtsc_target is not None:
        max_change = 1.5 * DT_MDL  # Conservative: max 1.5 m/s change per step
        current_limit = v_cruise
        delta = blended_target - current_limit

        if abs(delta) > max_change:
          # Rate limit the change
          blended_target = current_limit + np.sign(delta) * max_change

      if blended_target < v_cruise:
        v_cruise = blended_target
        self.handover_active = True
      else:
        self.handover_active = False

    elif vtsc_active and self.handover_distance < HANDOVER_END:
      # Within 150m - VTSC has full control (most precise)
      self.handover_active = False
      if self.vtsc_v_target < v_cruise:
        v_cruise = self.vtsc_v_target

    elif mtsc_active and self.handover_distance >= HANDOVER_START:
      # Beyond 200m - MTSC has full control (only option available)
      self.handover_active = False
      if self.mtsc_v_target < v_cruise:
        v_cruise = self.mtsc_v_target

    else:
      # Fallback: apply whichever is active and more conservative
      self.handover_active = False
      if vtsc_active and self.vtsc_v_target < v_cruise:
        v_cruise = self.vtsc_v_target
      if mtsc_active and self.mtsc_v_target < v_cruise:
        v_cruise = self.mtsc_v_target

    # Store previous targets for next iteration
    self.prev_vtsc_target = self.vtsc_v_target
    self.prev_mtsc_target = self.mtsc_v_target

    # EOP: MSLC - Map Speed Limit Control (OSM posted limits)
    self.mslc_v_target = None  # reset each cycle; only re-set if mapData is fresh
    if sm.updated['mapData']:
      driver_overriding = sm['carState'].gasPressed
      current_time = sm.logMonoTime['carState'] * 1e-9
      self.mslc_v_target, _ = self.mslc.update(sm['mapData'], v_ego, driver_overriding, current_time)

    # EOP: Map-based speed limit pre-announcement TTS
    # When a significant speed limit change is ahead (<300m), announce it early
    self._sl_tts_text = None
    mslc = self.mslc
    if (getattr(mslc, 'upcoming_limit', None) is not None
        and mslc.distance_to_limit < 300.0
        and mslc.distance_to_limit > 10.0
        and mslc.current_limit is not None):
      diff_kmh = mslc.upcoming_limit - mslc.current_limit
      if abs(diff_kmh) >= 20:
        upcoming = mslc.upcoming_limit
        # Deduplicate: only announce if this upcoming limit is new or distance decreased significantly
        if (self._sl_tts_announced_limit != upcoming
            or mslc.distance_to_limit < self._sl_tts_announced_distance - 100.0):
          self._sl_tts_announced_limit = upcoming
          self._sl_tts_announced_distance = mslc.distance_to_limit
          direction = "drops to" if diff_kmh < 0 else "rises to"
          self._sl_tts_text = f"Speed limit {direction} {upcoming} ahead."
    else:
      self._sl_tts_announced_limit = None
      self._sl_tts_announced_distance = float('inf')

    # EOP: NSLC - Navigation Speed Limit Control (Mapbox/navd limits)
    if sm.updated['navInstruction']:
      driver_overriding = sm['carState'].gasPressed
      current_time = sm.logMonoTime['carState'] * 1e-9
      self.nslc_v_target, _ = self.nslc.update(
        nav_instruction=sm['navInstruction'] if sm.valid.get('navInstruction', False) else None,
        map_data=None,
        v_ego=v_ego,
        driver_overriding=driver_overriding,
        current_time=current_time
      )

    # EOP: Dashboard speed limit (car-reported, most reliable fallback)
    dash_limit_ms = sm['carState'].speedLimit if sm.valid.get('carState', False) else 0.0

    # EOP: Speed Limit Resolver (Topic 05)
    self.sl_resolved = self.sl_resolver.update(
      mslc_limit_mps=self.mslc_v_target,
      nslc_limit_mps=self.nslc_v_target,
      car_limit_mps=dash_limit_ms if dash_limit_ms > 0 else None,
      v_ego=v_ego,
      distance_to_change_m=getattr(self.mslc, 'distance_to_limit', 0.0) or getattr(self.nslc, 'distance_to_limit', 0.0),
    )
    v_cruise = self.sl_resolver.apply_to_v_cruise(v_cruise, self.sl_resolved)

    # EOP: TLSC - Traffic Light Speed Control
    if sm.updated['stereoObjects']:
      self.tlsc_v_target = self.tlsc.update(sm)
      v_cruise = self._apply_speed_limit(v_cruise, self.tlsc_v_target)

    # EOP: Driver Preferences
    self.driver_prefs.update(sm)

    # EOP: SQSC - Surface Quality Speed Controller
    self.sqsc_v_target = self.sqsc.update(v_ego, sm)
    v_cruise = self._apply_speed_limit(v_cruise, self.sqsc_v_target)

    # EOP: RCD - Road Condition Detection (wet/icy/debris speed limits)
    rcd_state = self.rcd.update(sm)
    self.rcd_v_target = rcd_state.speed_limit_ms if rcd_state.is_active else None
    v_cruise = self._apply_speed_limit(v_cruise, self.rcd_v_target)

    # BRSC: reduce cruise speed while rough-road hold is active, on top of whatever
    # VTSC/MTSC/SQSC/RCD/TLSC already set v_cruise to. Gated on v_ego (matches VTSC's
    # MIN_VELOCITY) and floored so a long rough stretch can't crawl the car below a
    # safe minimum.
    self.brsc_v_target = None
    if (brsc_enabled and self.brsc_result is not None
        and self.brsc_result.active and v_ego > BRSC_MIN_V_EGO):
      self.brsc_v_target = max(v_cruise * self.brsc_result.speed_factor,
                                          BRSC_MIN_SPEED_MS)
    v_cruise = self._apply_speed_limit(v_cruise, self.brsc_v_target)

    # EOP: Adaptive Gap — adjust t_follow and jerk based on lead relative speed
    # Merged from FrogPilot. Only active when enabled via param (default off for safety).
    # Disabled in traffic mode — traffic personality uses its own tight-gap tables.
    self._adaptive_gap_t_follow = None
    self._adaptive_gap_jerk_factor = None
    personality = sm['selfdriveState'].personality
    if self._adaptive_gap_enabled and personality != log.LongitudinalPersonality.traffic:
      lead_one = sm['radarState'].leadOne
      if lead_one.status:
        d_rel = lead_one.dRel
        v_lead = lead_one.vLead
        t_follow_base = get_T_FOLLOW(personality)
        jerk_base = get_jerk_factor(personality)
        if v_lead > v_ego:
          # Approaching faster lead — reduce t_follow and jerk for more natural gap
          distance_factor = max(d_rel - (v_ego * t_follow_base), 1.0)
          offset = float(np.clip(STOP_DISTANCE - v_ego, 1.0, distance_factor))
          self._adaptive_gap_t_follow = t_follow_base / offset
          self._adaptive_gap_jerk_factor = jerk_base / offset
        elif v_lead < v_ego:
          # Approaching slower lead — increase t_follow for earlier braking feel
          distance_factor = max(d_rel - (v_lead * t_follow_base), 1.0)
          braking_offset = float(np.clip(min(v_ego - v_lead, v_lead) - COMFORT_BRAKE, 1.0, distance_factor))
          if d_rel >= 100.0:
            far_lead_offset = max(d_rel - (v_ego * t_follow_base) - STOP_DISTANCE, 0.0)
            braking_offset += far_lead_offset
          self._adaptive_gap_t_follow = t_follow_base / braking_offset
          self._adaptive_gap_jerk_factor = jerk_base  # no jerk change for slower leads
        if self._adaptive_gap_t_follow is not None:
          self._adaptive_gap_t_follow = max(self._adaptive_gap_t_follow, 0.5)

    # EOP: LHLC - Long Horizon Lateral Controller (500m trajectory from pathd)
    self.lhlc_v_target = None
    self.lhlc_active = False

    if sm.valid.get('enhancedTrajectory', False):
      et = sm['enhancedTrajectory']
      if et.lhlcActive and len(et.lhlcSpeeds) > 0:
        # Find waypoint closest to lookahead distance
        LHLC_LOOKAHEAD_M = 200.0  # Plan for curves 200m ahead
        LHLC_WAYPOINT_SPACING_M = 10.0
        target_idx = int(LHLC_LOOKAHEAD_M / LHLC_WAYPOINT_SPACING_M)
        target_idx = min(target_idx, len(et.lhlcSpeeds) - 1)

        target_speed = et.lhlcSpeeds[target_idx]
        confidences = et.lhlcConfidences if len(et.lhlcConfidences) == len(et.lhlcSpeeds) else [0.5] * len(et.lhlcSpeeds)
        confidence = confidences[target_idx]

        if confidence > 0.6:
          self.lhlc_v_target = target_speed
          self.lhlc_active = True
          v_cruise = self._apply_speed_limit(v_cruise, target_speed)

    # Apply speed offset to final v_cruise (after all other limits)
    v_cruise_kph = v_cruise * CV.MS_TO_KPH
    v_cruise_kph_with_offset = self.driver_prefs.get_speed_with_offset(v_cruise_kph)
    v_cruise = v_cruise_kph_with_offset * CV.KPH_TO_MS

    # Apply adaptive-gap adjusted jerk factor to MPC weights
    jerk_override = self._adaptive_gap_jerk_factor if self._adaptive_gap_jerk_factor is not None else None
    self.mpc.set_weights(prev_accel_constraint, personality=sm['selfdriveState'].personality, jerk_factor=jerk_override)
    self.mpc.set_cur_state(self.v_desired_filter.x, self.a_desired)
    t_follow_override = self._adaptive_gap_t_follow if self._adaptive_gap_t_follow is not None else None

    # EOP: Lane Change Lead Handoff — pure camera adjacent lead tracking
    # If laneChangeStarting, replace radarState.leadOne with the lead in the target lane
    radar_state_for_mpc = sm['radarState']
    if sm.valid.get('modelV2', False):
      model_meta = sm['modelV2'].meta
      radar_state_for_mpc = self.lc_handoff.update(
        model_v2=sm['modelV2'],
        radar_state=sm['radarState'],
        lc_state=model_meta.laneChangeState,
        lc_dir=model_meta.laneChangeDirection,
        v_ego=sm['carState'].vEgo,
        now=time.monotonic(),
      )

    self.mpc.update(radar_state_for_mpc, v_cruise, x, v, a, j, personality=sm['selfdriveState'].personality, t_follow_override=t_follow_override)

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

    # EOP: DLON force stop override — forces full stop at traffic lights/signs
    if self.dlon_force_stop:
      self.output_should_stop = True

    for idx in range(2):
      accel_clip[idx] = np.clip(accel_clip[idx], self.prev_accel_clip[idx] - 0.05, self.prev_accel_clip[idx] + 0.05)
    # Limit only rising acceleration. Planner-requested braking remains
    # immediately available.
    if tja_result.active:
      output_a_target = min(output_a_target, longitudinal_accel_max(v_ego) * tja_result.accel_scale)
    if not reset_state:
      output_a_target = min(output_a_target,
                            self.output_a_target + longitudinal_jerk_up(v_ego) * tja_result.jerk_scale * self.dt)
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

    # EOP: DLON debug info
    longitudinalPlan.dlonMode = self.dlon_result['mode']
    longitudinalPlan.dlonE2EEnabled = self.dlon_e2e_enabled
    longitudinalPlan.dlonForceStop = self.dlon_force_stop

    # EOP: VTSC/MTSC/MSLC debug info
    longitudinalPlan.vtscActive = self.vtsc_v_target is not None
    longitudinalPlan.mtscActive = self.mtsc_v_target is not None
    longitudinalPlan.mslcActive = self.mslc_v_target is not None
    if self.vtsc_v_target is not None:
      longitudinalPlan.vtscSpeed = float(self.vtsc_v_target)
    longitudinalPlan.vtscUsingLearned = self.vtsc_using_learned

    if self.mtsc_v_target is not None:
      longitudinalPlan.mtscSpeed = float(self.mtsc_v_target)
    longitudinalPlan.mtscUsingLearned = self.mtsc_using_learned
    longitudinalPlan.mtscDistance = float(self.handover_distance)

    if self.mslc_v_target is not None:
      longitudinalPlan.mslcSpeed = float(self.mslc_v_target)

    # EOP: NSLC (Navigation Speed Limit Controller)
    longitudinalPlan.nslcActive = self.nslc_v_target is not None
    if self.nslc_v_target is not None:
      longitudinalPlan.nslcSpeed = float(self.nslc_v_target)

    # EOP: SQSC debug info
    sqsc_state = self.sqsc.get_state()
    longitudinalPlan.sqscActive = sqsc_state['active']
    longitudinalPlan.sqscProfile = sqsc_state['profile']
    if self.sqsc_v_target is not None:
      longitudinalPlan.sqscSpeed = float(self.sqsc_v_target)

    # EOP: RCD debug info
    longitudinalPlan.rcdActive = self.rcd_v_target is not None
    if self.rcd_v_target is not None:
      longitudinalPlan.rcdSpeed = float(self.rcd_v_target)

    # BRSC debug info
    if self.brsc_result is not None:
      longitudinalPlan.ngpBrscActive = self.brsc_result.active
      longitudinalPlan.ngpBrscRoughness = float(self.brsc_result.roughness_rms)
    if self.brsc_v_target is not None:
      longitudinalPlan.ngpBrscSpeed = float(self.brsc_v_target)

    # EOP: LHLC (Long Horizon Lateral Controller)
    longitudinalPlan.lhlcActive = self.lhlc_active
    if self.lhlc_v_target is not None:
      longitudinalPlan.lhlcTargetSpeed = float(self.lhlc_v_target)
    # Publish target curvature from enhancedTrajectory if available
    if sm.valid.get('enhancedTrajectory', False):
      et = sm['enhancedTrajectory']
      if et.lhlcActive and len(et.lhlcCurvatures) > 0:
        target_idx = int(200.0 / 10.0)
        target_idx = min(target_idx, len(et.lhlcCurvatures) - 1)
        longitudinalPlan.lhlcTargetCurvature = float(et.lhlcCurvatures[target_idx])

    pm.send('longitudinalPlan', plan_send)

    # EOP: Speed Limit State (Topic 05) — policy-only; offsets are applied inside MSLC
    if self.sl_resolved is not None:
      sl_send = messaging.new_message('speedLimitState')
      sl = sl_send.speedLimitState
      sl.source = self.sl_resolved.source
      sl.limitMps = float(self.sl_resolved.limit_mps)
      sl.distanceToChangeM = float(self.sl_resolved.distance_to_change_m)
      sl.active = self.sl_resolved.active
      pm.send('speedLimitState', sl_send)

    # EOP: Map-based speed limit pre-announcement TTS
    if self._sl_tts_text is not None:
      tts_msg = messaging.new_message('ttsRequest')
      tts_msg.ttsRequest.text = self._sl_tts_text
      tts_msg.ttsRequest.priority = 1
      tts_msg.ttsRequest.interrupt = False
      pm.send('ttsRequest', tts_msg)
      self._sl_tts_text = None

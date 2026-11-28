"""Tier C MetaDrive 3D closed-loop behavioral simulation suite for EDP10.

Runs MetaDrive (WSLg RTX 4050 / EGL) + PoCL OpenCL CameraSim + real EDP10
controllers/policies with scenario-specific behavioral assertions for every
ported and fixed feature:
  1. VTSC: slows through a real MetaDrive R=60m 90-deg curve, stays in-lane,
     recovers speed on the exit straight, and streams RTX 4050 -> PoCL NV12 -> VisionIPC.
  2. MTSC: proactively slows ahead of an upcoming OSM map curve (mapdExtendedOut),
     and cleanly expires without latching when map updates drop out (>3s).
  3. MSLC: caps real MetaDrive vehicle speed to posted OSM limit (mapdOut) and
     accelerates smoothly once the speed limit rises / expires.
  4. BRSC: rejects longitudinal-axis excitation, reduces real MetaDrive speed on
     sustained vertical (-v[0]) roughness, recovers on smooth road, and clears on IMU dropout.
  5. Lateral/safety suite in MetaDrive multi-lane driving:
     AEM (exact rc FirstOrderFilter), DesireHelper lane-width & adjacent-gap guards,
     LC adjacent lead handoff slowing the MetaDrive vehicle, BlinkerPause + SteeringResumeRamp,
     CAT bounded adaptation, and LeadDepartureNotice at standstill.
"""

from __future__ import annotations

import math
import os
import time

import numpy as np
import pytest

import cereal.messaging as messaging
from cereal import car, log
from openpilot.common.realtime import DT_MDL
from openpilot.selfdrive.controls.lib.desire_helper import DesireHelper, LaneChangeState
from openpilot.selfdrive.controls.lib.longcontrol import LongCtrlState
from openpilot.selfdrive.controls.lib.longitudinal_planner import (
  BRSC_MIN_SPEED_MS,
  DPFlags,
  LongitudinalPlanner,
)
from openpilot.selfdrive.modeld.constants import ModelConstants

from exopilot.selfdrive.controls.lib.blinker_pause import BlinkerPause
from exopilot.selfdrive.controls.lib.cat import CAT
from exopilot.selfdrive.controls.lib.lead_departure import LeadDepartureNotice
from exopilot.selfdrive.controls.lib.steering_policy import SteeringResumeRamp

metadrive = pytest.importorskip("metadrive")


class _FalseDict(dict):
  def __getitem__(self, k):
    return self.get(k, False)


class _FakeSM(dict):
  def __init__(self, d):
    super().__init__(d)
    self.valid = _FalseDict(dict.fromkeys(d, True))
    self.alive = _FalseDict(dict.fromkeys(d, True))
    self.updated = _FalseDict(dict.fromkeys(d, True))
    self.logMonoTime = _FalseDict(dict.fromkeys(d, 0))


def _make_cp():
  CP = car.CarParams.new_message()
  CP.openpilotLongitudinalControl = True
  CP.longitudinalActuatorDelay = 0.15
  CP.vEgoStopping = 0.5
  CP.wheelbase = 2.72
  CP.steerRatio = 15.0
  return CP


def _curved_model_v2(
  v_ego: float,
  curvature: float,
  lc_state=log.LaneChangeState.off,
  lc_dir=log.LaneChangeDirection.none,
  adj_lead=None,
):
  msg = messaging.new_message("modelV2").modelV2
  n = len(ModelConstants.T_IDXS)
  msg.velocity.x = [float(v_ego)] * n
  msg.orientationRate.z = [float(v_ego * curvature)] * n
  msg.position.x = [float(v_ego * t) for t in ModelConstants.T_IDXS]
  msg.position.y = [0.0] * n
  msg.position.z = [0.0] * n
  msg.acceleration.x = [0.0] * n
  msg.meta.laneChangeState = lc_state
  msg.meta.laneChangeDirection = lc_dir
  if adj_lead is not None:
    x_rel, y_right, v_abs = adj_lead
    leads = msg.init("leadsV3", 1)
    leads[0].prob = 0.92
    leads[0].x = [float(x_rel)]
    leads[0].y = [float(y_right)]
    leads[0].v = [float(v_abs)]
  return msg


def _make_sm(
  v_ego: float,
  v_cruise_kph: float,
  road_kappa: float = 0.0,
  az_vertical: float = 9.81,
  ax_longitudinal: float = 0.0,
  accel_valid: bool = True,
  mapd_ext=None,
  mapd_out=None,
  lc_state=log.LaneChangeState.off,
  lc_dir=log.LaneChangeDirection.none,
  adj_lead=None,
):
  cs = messaging.new_message("carState").carState
  cs.vEgo = float(v_ego)
  cs.aEgo = 0.0
  cs.vCruise = float(v_cruise_kph)
  cs.standstill = v_ego < 0.05
  cs.steeringAngleDeg = 0.0

  cc = messaging.new_message("carControl").carControl
  cc.longActive = True
  cc.enabled = True
  cc.orientationNED = [0.0, 0.0, 0.0]

  ctrl = messaging.new_message("controlsState").controlsState
  ctrl.longControlState = LongCtrlState.pid
  ctrl.forceDecel = False

  sd = messaging.new_message("selfdriveState").selfdriveState
  sd.active = True
  sd.enabled = True
  sd.experimentalMode = False

  lp = messaging.new_message("liveParameters").liveParameters
  lp.angleOffsetDeg = 0.0
  lp.stiffnessFactor = 1.0
  lp.steerRatio = 15.0
  lp.valid = True

  acc = messaging.new_message("accelerometer").accelerometer
  acc.acceleration.v = [float(az_vertical), 0.0, float(-ax_longitudinal)]

  d = {
    "carState": cs,
    "carControl": cc,
    "controlsState": ctrl,
    "selfdriveState": sd,
    "liveParameters": lp,
    "radarState": messaging.new_message("radarState").radarState,
    "modelV2": _curved_model_v2(max(v_ego, 1.0), road_kappa, lc_state=lc_state, lc_dir=lc_dir, adj_lead=adj_lead),
    "accelerometer": acc,
  }
  if mapd_ext is not None:
    d["mapdExtendedOut"] = mapd_ext
  if mapd_out is not None:
    d["mapdOut"] = mapd_out
  sm = _FakeSM(d)
  sm.valid["accelerometer"] = accel_valid
  return sm


def _make_mapd_ext(curve_dist_m: float, curvature: float):
  ext = messaging.new_message("mapdExtendedOut").mapdExtendedOut
  ext.position.latitude = 13.7563
  ext.position.longitude = 100.5018
  pts = ext.init("path", 24)
  for i in range(24):
    d = i * 20.0
    pts[i].latitude = 13.7563 + d / 111111.0
    pts[i].longitude = 100.5018
    pts[i].curvature = float(curvature if abs(d - curve_dist_m) <= 30.0 else 0.0)
    pts[i].targetVelocity = 0.0
  return ext


def _lane_center_steer(env) -> tuple[float, float, bool]:
  _, _, on_lane = env.vehicle.navigation._get_current_lane(env.vehicle)
  lane = env.vehicle.lane
  local_long, local_lat = lane.local_coordinates(env.vehicle.position)
  heading_err = lane.heading_theta_at(local_long) - env.vehicle.heading_theta
  heading_err = (heading_err + math.pi) % (2 * math.pi) - math.pi
  steer_cmd = float(np.clip(heading_err * 2.0 - local_lat * 0.25, -1.0, 1.0))
  dh = lane.heading_theta_at(local_long + 10.0) - lane.heading_theta_at(local_long)
  dh = (dh + math.pi) % (2 * math.pi) - math.pi
  road_kappa = abs(dh) / 10.0
  return steer_cmd, road_kappa, bool(on_lane)


def _accel_to_pedal(accel: float, a_ego: float = 0.0) -> float:
  cmd = accel + 0.35 * (accel - a_ego)
  return float(np.clip(cmd / 2.4 if cmd >= 0 else cmd / 3.8, -1.0, 1.0))


def _curve_env_config(with_camera: bool = True):
  from metadrive.component.map.pg_map import MapGenerateMethod
  from openpilot.tools.sim.bridge.metadrive.metadrive_common import RGBCameraRoad
  from openpilot.tools.sim.bridge.metadrive.metadrive_bridge import curve_block, straight_block
  from openpilot.tools.sim.lib.camerad import H, W

  cfg = dict(
    use_render=False,
    vehicle_config=dict(
      enable_reverse=False,
      render_vehicle=False,
      image_source="rgb_road" if with_camera else "main_camera",
    ),
    image_on_cuda=False,
    image_observation=with_camera,
    interface_panel=[],
    out_of_route_done=False,
    on_continuous_line_done=False,
    crash_vehicle_done=False,
    crash_object_done=False,
    traffic_density=0.0,
    map_config=dict(
      type=MapGenerateMethod.PG_MAP_FILE,
      lane_num=2,
      lane_width=4.0,
      config=[None, straight_block(100), curve_block(60, 90), straight_block(250)],
    ),
    decision_repeat=1,
    physics_world_step_size=0.05,
    preload_models=False,
    show_logo=False,
    anisotropic_filtering=False,
  )
  if with_camera:
    cfg["sensors"] = {"rgb_road": (RGBCameraRoad, W, H)}
  return cfg


def _straight_env_config():
  from metadrive.component.map.pg_map import MapGenerateMethod
  from openpilot.tools.sim.bridge.metadrive.metadrive_bridge import straight_block

  return dict(
    use_render=False,
    vehicle_config=dict(enable_reverse=False, render_vehicle=False),
    image_on_cuda=False,
    image_observation=False,
    interface_panel=[],
    out_of_route_done=False,
    on_continuous_line_done=False,
    crash_vehicle_done=False,
    crash_object_done=False,
    traffic_density=0.0,
    map_config=dict(
      type=MapGenerateMethod.PG_MAP_FILE,
      lane_num=3,
      lane_width=3.6,
      config=[None] + [straight_block(120) for _ in range(8)],
    ),
    decision_repeat=1,
    physics_world_step_size=0.05,
    preload_models=False,
    show_logo=False,
    anisotropic_filtering=False,
  )


@pytest.mark.slow
def test_edp10_metadrive_vtsc_curve_and_camera_pipeline():
  """VTSC slows the MetaDrive car through a real R=60m 90-deg curve, stays in-lane, recovers, and streams GPU->OpenCL NV12."""
  from metadrive.envs.metadrive_env import MetaDriveEnv
  from openpilot.tools.sim.bridge.metadrive.metadrive_process import C3_HPR, C3_POSITION
  from openpilot.tools.sim.lib.camerad import H, W, Camerad

  prefix = os.environ.get("OPENPILOT_PREFIX", "")
  if prefix:
    os.makedirs(f"/dev/shm/msgq_{prefix}", exist_ok=True)

  env = MetaDriveEnv(_curve_env_config(with_camera=True))
  camerad = None
  try:
    env.reset()
    camerad = Camerad(dual_camera=False)
    planner = LongitudinalPlanner(_make_cp())

    speeds = []
    vtsc_targets = []
    cam_frames = 0
    dist_m = 0.0
    prev_pos = np.array(env.vehicle.position, dtype=float)
    on_lane_steps = 0
    n_steps = 460  # 23.0s at 20 Hz: 100m entry straight -> 90-deg R=60m curve -> 250m exit straight

    t0 = time.monotonic()
    for step in range(n_steps):
      v_ego = float(env.vehicle.speed)
      speeds.append(v_ego)
      steer_cmd, road_kappa, on_lane = _lane_center_steer(env)
      if on_lane:
        on_lane_steps += 1

      sm = _make_sm(v_ego=v_ego, v_cruise_kph=54.0, road_kappa=road_kappa)  # 15 m/s cruise > ~10.4 m/s R=60m comfort
      planner.update(sm, dp_flags=DPFlags.VTSC)
      vtsc_targets.append(planner.vtsc_v_target)

      env.step([steer_cmd, _accel_to_pedal(float(planner.a_desired))])
      pos = np.array(env.vehicle.position, dtype=float)
      dist_m += float(np.linalg.norm(pos - prev_pos))
      prev_pos = pos

      if step % 20 == 0:
        cam = env.engine.sensors["rgb_road"]
        cam.get_cam().reparentTo(env.vehicle.origin)
        cam.get_cam().setPos(C3_POSITION)
        cam.get_cam().setHpr(C3_HPR)
        rgb = cam.perceive(to_float=False)
        yuv = camerad.rgb_to_yuv(rgb)
        assert len(yuv) == W * H * 3 // 2
        camerad.cam_send_yuv_road(yuv)
        cam_frames += 1

    assert time.monotonic() - t0 > 0
    assert dist_m > 180.0, f"Vehicle barely moved: {dist_m:.1f}m"
    assert on_lane_steps == n_steps, f"Left lane during curve: {on_lane_steps}/{n_steps}"
    active_steps = [i for i, t in enumerate(vtsc_targets) if t is not None]
    assert len(active_steps) >= 20, f"VTSC barely activated on R=60m curve: {len(active_steps)} ticks"
    # Verify actual physics slowdown during curve and recovery on exit straight
    first_curve = active_steps[0]
    last_curve = active_steps[-1]
    v_curve_min = min(speeds[first_curve:last_curve + 1])
    v_exit = speeds[-1]
    assert min(vtsc_targets[i] for i in active_steps) < 12.5
    assert v_curve_min < 12.5, f"MetaDrive vehicle did not slow in curve: min={v_curve_min:.2f} m/s"
    assert vtsc_targets[-1] is None, "VTSC failed to clear on exit straight"
    assert v_exit > v_curve_min + 1.5, f"Vehicle did not recover speed after curve: {v_curve_min:.2f} -> {v_exit:.2f}"
    assert cam_frames == 23
  finally:
    if camerad is not None and hasattr(camerad, "close"):
      camerad.close()
    env.close()


@pytest.mark.slow
def test_edp10_metadrive_mtsc_mslc_brsc_and_safety_behaviors():
  """Dedicated MetaDrive closed-loop behavioral suite for MTSC, MSLC, BRSC, AEM, LC guards/handoff, BlinkerPause, CAT, LeadDepart."""
  from metadrive.envs.metadrive_env import MetaDriveEnv

  env = MetaDriveEnv(_straight_env_config())
  try:
    # ------------------------------------------------------------------
    # Scenario 1: MTSC proactive slowdown ahead of 260m map curve + 3s expiry
    # ------------------------------------------------------------------
    env.reset()
    planner_mtsc = LongitudinalPlanner(_make_cp())
    v_before_mtsc = 0.0
    v_during_mtsc_min = 999.0
    prev_v = 0.0
    for step in range(520):  # 0..200 accel to cruise; 200..310 1Hz mapd curve (last @300, hold until ~360); 365..520 recovery
      v_ego = float(env.vehicle.speed)
      a_ego = (v_ego - prev_v) / DT_MDL
      prev_v = v_ego
      steer_cmd, _, on_lane = _lane_center_steer(env)
      assert on_lane
      mapd_ext = _make_mapd_ext(260.0, 1.0 / 55.0) if (200 <= step < 310 and step % 20 == 0) else None
      sm = _make_sm(v_ego=v_ego, v_cruise_kph=72.0, mapd_ext=mapd_ext)
      planner_mtsc.update(sm, dp_flags=DPFlags.MTSC | DPFlags.VTSC)
      if step == 199:
        v_before_mtsc = v_ego
      if 220 <= step < 365:
        v_during_mtsc_min = min(v_during_mtsc_min, v_ego)
      env.step([steer_cmd, _accel_to_pedal(float(planner_mtsc.a_desired), a_ego)])

    v_after_mtsc_expiry = float(env.vehicle.speed)
    assert v_before_mtsc >= 14.5, f"Did not reach cruise before MTSC: {v_before_mtsc:.2f}"
    assert v_during_mtsc_min < v_before_mtsc - 2.0, (
      f"MTSC failed to decelerate MetaDrive vehicle: {v_before_mtsc:.2f} -> {v_during_mtsc_min:.2f}"
    )
    assert planner_mtsc._mtsc_v_target is None, "MTSC stayed latched after >3s mapd silence"
    assert v_after_mtsc_expiry > v_during_mtsc_min + 1.5, (
      f"Vehicle failed to recover after MTSC expiry: {v_during_mtsc_min:.2f} -> {v_after_mtsc_expiry:.2f}"
    )

    # ------------------------------------------------------------------
    # Scenario 2: MSLC speed limit cap (36 km/h = 10 m/s) & release to 65 km/h
    # ------------------------------------------------------------------
    env.reset()
    planner_mslc = LongitudinalPlanner(_make_cp())
    v_capped_settled = []
    prev_v = 0.0
    for step in range(360):  # 0..200 cap at 36 km/h (10 m/s); 200..360 raise to 65 km/h
      v_ego = float(env.vehicle.speed)
      a_ego = (v_ego - prev_v) / DT_MDL
      prev_v = v_ego
      steer_cmd, _, on_lane = _lane_center_steer(env)
      assert on_lane
      mapd_out = None
      if step % 20 == 0:
        mo = messaging.new_message("mapdOut").mapdOut
        mo.speedLimit = (36.0 if step < 200 else 65.0) / 3.6
        mo.nextSpeedLimit = 0.0
        mo.nextSpeedLimitDistance = 0.0
        mapd_out = mo
      sm = _make_sm(v_ego=v_ego, v_cruise_kph=72.0, mapd_out=mapd_out)
      planner_mslc.update(sm, dp_flags=DPFlags.MSLC)
      if 140 <= step < 200:
        assert planner_mslc._mslc_v_target is not None and abs(planner_mslc._mslc_v_target - 10.0) < 0.2
        v_capped_settled.append(v_ego)
      env.step([steer_cmd, _accel_to_pedal(float(planner_mslc.a_desired), a_ego)])

    v_after_mslc_raise = float(env.vehicle.speed)
    v_capped_mean = float(np.mean(v_capped_settled))
    assert 8.8 <= v_capped_mean <= 11.2, f"MSLC failed to cap MetaDrive speed near 10 m/s: mean={v_capped_mean:.2f}"
    assert v_after_mslc_raise > v_capped_mean + 2.5, (
      f"MSLC failed to accelerate after limit raise: {v_capped_mean:.2f} -> {v_after_mslc_raise:.2f}"
    )

    # ------------------------------------------------------------------
    # Scenario 3: BRSC longitudinal rejection, vertical roughness slowdown, recovery & IMU dropout
    # ------------------------------------------------------------------
    env.reset()
    planner_brsc = LongitudinalPlanner(_make_cp())
    v_smooth_peak = 0.0
    v_rough_min = 999.0
    brsc_ever_active = False
    prev_v = 0.0
    for step in range(460):
      v_ego = float(env.vehicle.speed)
      a_ego = (v_ego - prev_v) / DT_MDL
      prev_v = v_ego
      steer_cmd, _, on_lane = _lane_center_steer(env)
      assert on_lane
      if step < 200:
        # Smooth road with strong longitudinal axis excitation -> BRSC must ignore
        az, ax, acc_ok = 9.81, 4.0 * math.sin(step * 0.6), True
      elif step < 320:
        # Sustained rough pavement on vertical -v[0] axis -> BRSC must slow real car
        az, ax, acc_ok = 9.81 + 4.8 * math.sin(step * 1.1), 0.0, True
      elif step == 320:
        # IMU dropout -> BRSC must immediately clear hold
        az, ax, acc_ok = 9.81, 0.0, False
      else:
        az, ax, acc_ok = 9.81, 0.0, True

      sm = _make_sm(v_ego=v_ego, v_cruise_kph=72.0, az_vertical=az, ax_longitudinal=ax, accel_valid=acc_ok)
      planner_brsc.update(sm, dp_flags=DPFlags.BRSC)
      if step < 200:
        assert planner_brsc.brsc_v_target is None, "BRSC falsely triggered on smooth road / longitudinal excitation"
        v_smooth_peak = max(v_smooth_peak, v_ego)
      elif step < 320:
        if planner_brsc.brsc_v_target is not None:
          brsc_ever_active = True
          assert planner_brsc.brsc_v_target >= BRSC_MIN_SPEED_MS
        v_rough_min = min(v_rough_min, v_ego)
      elif step == 320:
        assert planner_brsc.brsc_result is None and planner_brsc.brsc_v_target is None, "BRSC failed to clear on IMU dropout"
      env.step([steer_cmd, _accel_to_pedal(float(planner_brsc.a_desired), a_ego)])

    v_brsc_recovered = float(env.vehicle.speed)
    assert brsc_ever_active, "BRSC never activated on sustained rough road"
    assert v_rough_min < v_smooth_peak - 1.5, f"BRSC failed to slow MetaDrive vehicle: {v_smooth_peak:.2f} -> {v_rough_min:.2f}"
    assert v_brsc_recovered > v_rough_min + 1.0, f"BRSC failed to recover on smooth road: {v_rough_min:.2f} -> {v_brsc_recovered:.2f}"

    # ------------------------------------------------------------------
    # Scenario 4: LC adjacent lead handoff + DesireHelper guards + AEM + BlinkerPause + Ramp + CAT + LeadDepart
    # ------------------------------------------------------------------
    env.reset()
    CP = _make_cp()
    planner_lc = LongitudinalPlanner(CP)
    from dragonpilot.selfdrive.controls.lib.aem import AEM
    expected_alpha = DT_MDL / (AEM.EMA_TC_V_EGO + DT_MDL)
    assert abs(planner_lc.aem._v_ego_ema.alpha - expected_alpha) < 1e-9

    cat = CAT(CP)
    bp = BlinkerPause(min_speed_mps=15.0, hold_s=1.0, dt=DT_MDL)
    ramp = SteeringResumeRamp()

    v_pre_lc = 0.0
    v_lc_min = 999.0
    prev_v = 0.0
    for step in range(320):
      v_ego = float(env.vehicle.speed)
      a_ego = (v_ego - prev_v) / DT_MDL
      prev_v = v_ego
      steer_cmd, _, on_lane = _lane_center_steer(env)
      assert on_lane
      # Steps 200..319: laneChangeStarting to right lane where slower adjacent lead is 18m ahead at 6 m/s
      in_lc = step >= 200
      sm = _make_sm(
        v_ego=v_ego,
        v_cruise_kph=65.0,
        lc_state=log.LaneChangeState.laneChangeStarting if in_lc else log.LaneChangeState.off,
        lc_dir=log.LaneChangeDirection.right if in_lc else log.LaneChangeDirection.none,
        adj_lead=(18.0, 3.3, 6.0) if in_lc else None,  # modelV2 right-positive y=+3.3m (adjacent right lane)
      )
      planner_lc.update(sm, dp_flags=DPFlags.LC_LEAD_HANDOFF | DPFlags.AEM)
      cat_st = cat.update(sm)
      assert math.isfinite(cat_st.steer_ratio) and math.isfinite(cat_st.stiffness_factor)
      if step == 199:
        v_pre_lc = v_ego
      if in_lc:
        v_lc_min = min(v_lc_min, v_ego)
      env.step([steer_cmd, _accel_to_pedal(float(planner_lc.a_desired), a_ego)])

    assert planner_lc.aem._v_ego_ema.x > 5.0, f"AEM EMA did not track MetaDrive speed: {planner_lc.aem._v_ego_ema.x:.2f}"

    assert v_pre_lc >= 13.5, f"Vehicle did not reach cruise before LC handoff: {v_pre_lc:.2f}"
    assert v_lc_min < v_pre_lc - 3.0, (
      f"LC adjacent lead handoff failed to slow MetaDrive car for slower target-lane vehicle: {v_pre_lc:.2f} -> {v_lc_min:.2f}"
    )

    # DesireHelper lane-width & adjacent-gap guards against real 3.6m MetaDrive lane geometry
    lane_w = float(env.vehicle.lane.width)
    assert lane_w >= 3.0
    cs_lc = messaging.new_message("carState").carState
    cs_lc.vEgo = 18.0
    cs_lc.leftBlinker = True
    cs_lc.steeringPressed = True
    cs_lc.steeringTorque = 50.0
    xs = [float(i * 5) for i in range(33)]
    mdl_narrow = messaging.new_message("modelV2").modelV2
    lines = mdl_narrow.init("laneLines", 4)
    for ll in lines:
      ll.x = xs
      ll.y = [0.0] * 33
    lines[0].y = [-lane_w * 1.5] * 33
    lines[1].y = [-lane_w * 0.5] * 33
    lines[2].y = [lane_w * 0.5] * 33
    lines[3].y = [lane_w * 1.5] * 33
    mdl_narrow.laneLineProbs = [0.9, 0.9, 0.9, 0.9]
    edges = mdl_narrow.init("roadEdges", 2)
    edges[0].x = xs
    edges[0].y = [-(lane_w * 0.5 + 2.0)] * 33  # 2.0m < 2.5m MIN_LANE_WIDTH
    edges[1].x = xs
    edges[1].y = [lane_w * 1.5] * 33
    dh_narrow = DesireHelper(dp_lat_lca_speed=20, lca_lane_width=True)
    dh_narrow.update(cs_lc, True, 0.0, False, False, model_v2=mdl_narrow)
    dh_narrow.update(cs_lc, True, 0.0, False, False, model_v2=mdl_narrow)
    assert dh_narrow.lane_change_state == LaneChangeState.preLaneChange

    # Widen target lane to full MetaDrive lane_w (3.6m) with no obstacle -> must allow laneChangeStarting
    edges[0].y = [-(lane_w * 1.5)] * 33
    dh_open = DesireHelper(dp_lat_lca_speed=20, lca_lane_width=True, lca_gap_eval=True)
    dh_open.update(cs_lc, True, 0.0, False, False, model_v2=mdl_narrow)
    dh_open.update(cs_lc, True, 0.0, False, False, model_v2=mdl_narrow)
    assert dh_open.lane_change_state == LaneChangeState.laneChangeStarting

    # BlinkerPause + SteeringResumeRamp contract at low speed
    assert bp.update(8.0, True, False) is True
    assert bp.update(8.0, False, False) is True  # hold after blinker off
    for _ in range(25):
      bp.update(8.0, False, False)
    assert bp.update(8.0, False, False) is False  # hold expired
    t_off, _ = ramp.update(False, 0.0, 0.0, 0.0, 0.0)
    t_early, _ = ramp.update(True, 1.0, 12.0, 0.0, 0.1)
    t_full, _ = ramp.update(True, 1.0, 12.0, 0.0, 2.5)
    assert t_off == 0.0 and 0.0 <= t_early < t_full <= 1.0

    # LeadDepartureNotice at standstill
    ld = LeadDepartureNotice()
    lead = messaging.new_message("radarState").radarState.leadOne
    lead.status = True
    lead.dRel = 5.0
    lead.vLead = 0.0
    assert not ld.update(True, lead)
    lead.dRel = 6.8
    lead.vLead = 1.8
    assert ld.update(True, lead)
  finally:
    env.close()

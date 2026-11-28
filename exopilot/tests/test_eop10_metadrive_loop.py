"""Tier C MetaDrive 3D closed-loop behavioral verification suite for EOP10 / 01M / 02M.

Verifies every EOP10 driving, speed-control, perception, and vehicle-CAN feature
inside MetaDrive 3D physics (WSLg RTX 4050 EGL + PoCL OpenCL CameraSim -> v4l2d VisionIPC):
  1. Curve track + CameraSim: VTSC R=60m curve slowdown & straight exit recovery,
     DLON sharp-curve detection, DLAT laneful<->laneless arbitration, and
     RTX 4050 RGB -> PoCL OpenCL NV12 -> v4l2d VisionIPC streaming.
  2. Dedicated MetaDrive behavioral scenarios on straight highway:
     - TLSC P0 red-light stop-line MPC (full stop before 60m stop line + immediate green release)
     - MTSC + MSLC/NSLC SpeedPolicy.LOWEST cap and limit-increase recovery
     - BRSC comma/RK -v[0] vertical IMU roughness, v[2] longitudinal rejection,
       IMU-dropout reset, and unthrottled brake release from -2.5 m/s^2
     - Live EOP10 perception & Tesla CAN loop: SimulatedCar CANPacker checksums ->
       CarState.update() decoding live MetaDrive speed/blinkers + GridD leadsV3 fusion,
       trafficLight ObstacleType mapping, and ego-motion DrivableFusion shifting.
"""

from __future__ import annotations

import math
import os
import time
from types import SimpleNamespace as NS

import numpy as np
import pytest

import cereal.messaging as messaging
from cereal import car, custom, log
from openpilot.common.realtime import DT_MDL
from openpilot.selfdrive.controls.lib.aeb import AEB, AEBLevel, AEBState, get_aeb_decel_limit
from openpilot.selfdrive.controls.lib.desire_helper import DesireHelper, LaneChangeState
from openpilot.selfdrive.controls.lib.longcontrol import LongCtrlState
from openpilot.selfdrive.controls.lib.longitudinal_planner import (
  BRSC_MIN_SPEED_MS,
  LongitudinalPlanner,
)
from openpilot.selfdrive.controls.lib.radar_zones import RadarZoneMonitor, ZoneAlertLevel
from openpilot.selfdrive.controls.radar_corner_geometry import encode_corner_track_id
from openpilot.selfdrive.modeld.constants import ModelConstants
from openpilot.system.socketd.safety.safety_manager import SafetyManager
from openpilot.system.socketd.vehicle.car.carstate import CarState
from openpilot.system.socketd.vehicle.tesla.values import CANBUS
from openpilot.tools.sim.lib.simulated_car import SimulatedCar

from exopilot.selfdrive.adaptd.adaptd import AdaptiveDrivingComputer
from exopilot.selfdrive.controls.lib.alcc import ALCC, ALCCInput, ALCCState, BrakeMode
from exopilot.selfdrive.controls.lib.blinker_pause import BlinkerPause
from exopilot.selfdrive.controls.lib.cat import SteerRatioFilter
from exopilot.selfdrive.controls.lib.dlat import DLAT, DLATSuggestion
from exopilot.selfdrive.controls.lib.driver_activity import DriverActivityMonitor
from exopilot.selfdrive.controls.lib.green_light import GreenLightNotice
from exopilot.selfdrive.controls.lib.lead_departure import LeadDepartureNotice
from exopilot.selfdrive.controls.lib.map_speed import MapSpeed
from exopilot.selfdrive.controls.lib.policy_arbiter import Mode as ArbiterMode
from exopilot.selfdrive.controls.lib.red import RED, REDState, curvature_nudge
from exopilot.selfdrive.controls.lib.rule_channel import RuleChannelConsumer
from exopilot.selfdrive.controls.lib.soc import SOC
from exopilot.selfdrive.controls.lib.soc_policy import SOCInput
from exopilot.selfdrive.controls.lib.steering_policy import SteeringResumeRamp
from exopilot.selfdrive.gridd.drivable_fusion import DRIVABLE, OTHER, DrivableFusion
from exopilot.selfdrive.gridd.occupancy import GridD, obstacle_type
from exopilot.selfdrive.monod.monod import Light, fill_raw_detections
from exopilot.selfdrive.monod.traffic_light_classifier import classify as classify_tl_bgr
from exopilot.selfdrive.sided.bev_reprojector import SideCameraGeometry, reproject_side_camera

metadrive = pytest.importorskip("metadrive")


class _DictParams:
  def __init__(self, initial=None):
    self._d = dict(initial or {})

  def get_bool(self, k):
    return bool(self._d.get(k, False))

  def get(self, k):
    return self._d.get(k)


class _FalseDict(dict):
  def __getitem__(self, k):
    return self.get(k, False)


class _FakeSM(dict):
  def __init__(self, d):
    super().__init__(d)
    self.valid = _FalseDict(dict.fromkeys(d, True))
    self.alive = _FalseDict(dict.fromkeys(d, True))
    self.updated = _FalseDict(dict.fromkeys(d, True))
    self.seen = _FalseDict(dict.fromkeys(d, True))
    self.logMonoTime = _FalseDict(dict.fromkeys(d, 0))

  def __missing__(self, k):
    msg = getattr(messaging.new_message(k), k)
    self[k] = msg
    return msg


def _make_cp():
  CP = car.CarParams.new_message()
  CP.openpilotLongitudinalControl = True
  CP.longitudinalActuatorDelay = 0.15
  CP.deprecated.vEgoStopping = 0.5
  CP.wheelbase = 2.875
  CP.steerRatio = 12.0
  CP.mass = 1850.0
  CP.centerToFront = 1.3
  CP.tireStiffnessFront = 1.2e5
  CP.tireStiffnessRear = 1.2e5
  CP.rotationalInertia = 2500.0
  return CP


def _model_v2(
  v_ego: float,
  curvature: float = 0.0,
  lane_prob: float = 0.9,
  v_cruise_ms: float = 20.0,
  lc_state=log.LaneChangeState.off,
  lc_dir=log.LaneChangeDirection.none,
  adj_lead=None,
  road_edges=None,
):
  msg = messaging.new_message("modelV2").modelV2
  n = len(ModelConstants.T_IDXS)
  xs = [float(v_ego * t) for t in ModelConstants.T_IDXS]
  msg.velocity.x = [float(v_ego)] * n
  msg.orientationRate.z = [float(v_ego * curvature)] * n
  msg.position.x = xs
  msg.position.y = [float(0.5 * curvature * x * x) for x in xs]
  msg.position.z = [0.0] * n
  msg.acceleration.x = [0.0] * n
  msg.action.desiredAcceleration = float(np.clip((v_cruise_ms - v_ego) * 0.6, -2.0, 2.0))
  msg.action.shouldStop = False
  msg.meta.disengagePredictions.gasPressProbs = [1.0, 1.0]
  msg.meta.laneChangeState = lc_state
  msg.meta.laneChangeDirection = lc_dir
  lane_xs = [float(i * 5) for i in range(33)]
  lines = msg.init("laneLines", 4)
  for idx, y_val in enumerate((-5.4, -1.8, 1.8, 5.4)):
    lines[idx].x = lane_xs
    lines[idx].y = [y_val] * 33
  msg.laneLineProbs = [lane_prob, lane_prob, lane_prob, lane_prob]
  if adj_lead is not None:
    lx, ly, lv = adj_lead
    leads = msg.init("leadsV3", 1)
    leads[0].prob = 0.92
    leads[0].x = [float(lx)]
    leads[0].y = [float(ly)]
    leads[0].v = [float(lv - v_ego)]
  if road_edges is not None:
    left_y, right_y, left_std, right_std = road_edges
    edges = msg.init("roadEdges", 2)
    edges[0].x = lane_xs
    edges[0].y = [float(left_y)] * 33
    edges[1].x = lane_xs
    edges[1].y = [float(right_y)] * 33
    msg.roadEdgeStds = [float(left_std), float(right_std)]
  return msg


def _make_sm(
  v_ego: float,
  v_cruise_kph: float,
  a_ego: float = 0.0,
  road_kappa: float = 0.0,
  lane_prob: float = 0.9,
  az_vertical: float = 9.81,
  ax_longitudinal: float = 0.0,
  accel_valid: bool = True,
  mapd_ext=None,
  mapd_out=None,
  nav_limit_ms: float = 0.0,
  traffic_light=None,
  stereo_objects_msg=None,
  lead_one=None,
  lead_radar: bool = True,
  awareness: float = 1.0,
  surface_score: float | None = None,
  surface_texture: str = "wet",
  lhlc_speed: float | None = None,
  lc_state=log.LaneChangeState.off,
  lc_dir=log.LaneChangeDirection.none,
  adj_lead=None,
  steering_torque: float = 0.0,
):
  cs = messaging.new_message("carState").carState
  cs.vEgo = float(v_ego)
  cs.aEgo = float(a_ego)
  cs.vCruise = float(v_cruise_kph)
  cs.standstill = v_ego < 0.05
  cs.steeringAngleDeg = 0.0
  cs.steeringTorque = float(steering_torque)
  cs.gasPressed = False
  cs.brakePressed = False

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
  sd.personality = log.LongitudinalPersonality.standard

  lp = messaging.new_message("liveParameters").liveParameters
  lp.angleOffsetDeg = 0.0

  llk = messaging.new_message("liveLocationKalman").liveLocationKalman
  llk.gpsOK = False

  # locationd.py: meas = [-v[2], -v[1], -v[0]] -> vertical is -v[0], longitudinal is -v[2]
  acc = messaging.new_message("accelerometer").accelerometer
  acc.acceleration.v = [float(-az_vertical), 0.0, float(-ax_longitudinal)]

  nav = messaging.new_message("navInstruction").navInstruction
  nav.speedLimit = float(nav_limit_ms)
  nav.maneuverDistance = 999.0

  rs = messaging.new_message("radarState").radarState
  if lead_one is not None:
    d_rel, v_lead, track_id = lead_one
    rs.leadOne.status = True
    rs.leadOne.radar = bool(lead_radar)
    rs.leadOne.dRel = float(d_rel)
    rs.leadOne.yRel = 0.0
    rs.leadOne.vLead = float(v_lead)
    rs.leadOne.vLeadK = float(v_lead)
    rs.leadOne.vRel = float(v_lead - v_ego)
    rs.leadOne.radarTrackId = int(track_id)
    rs.leadOne.modelProb = 0.95

  dm = messaging.new_message("driverMonitoringState").driverMonitoringState
  dm.awarenessStatus = float(awareness)

  d = {
    "carState": cs,
    "carControl": cc,
    "controlsState": ctrl,
    "selfdriveState": sd,
    "liveParameters": lp,
    "liveLocationKalman": llk,
    "radarState": rs,
    "modelV2": _model_v2(
      max(v_ego, 1.0),
      road_kappa,
      lane_prob=lane_prob,
      v_cruise_ms=v_cruise_kph / 3.6,
      lc_state=lc_state,
      lc_dir=lc_dir,
      adj_lead=adj_lead,
    ),
    "accelerometer": acc,
    "driverMonitoringState": dm,
    "navInstruction": nav,
  }
  if mapd_ext is not None:
    d["mapdExtendedOut"] = mapd_ext
  if mapd_out is not None:
    d["mapdOut"] = mapd_out
  if surface_score is not None:
    ss = messaging.new_message("surfaceStatus").surfaceStatus
    ss.hasSurfaceQuality = True
    ss.hasShocks = False
    ss.surfaceQuality.score = float(surface_score)
    ss.surfaceQuality.confidence = 0.9
    ss.surfaceQuality.texture = surface_texture
    d["surfaceStatus"] = ss
  if lhlc_speed is not None:
    et = messaging.new_message("enhancedTrajectory").enhancedTrajectory
    et.lhlcActive = True
    et.lhlcSpeeds = [float(lhlc_speed)] * 30
    et.lhlcConfidences = [0.9] * 30
    d["enhancedTrajectory"] = et
  if stereo_objects_msg is not None:
    d["stereoObjects"] = stereo_objects_msg
  elif traffic_light is not None:
    state, conf, dist = traffic_light
    so = messaging.new_message("stereoObjects").stereoObjects
    objs = so.init("objects", 1)
    objs[0].obstacleType = obstacle_type("trafficLight")
    objs[0].trafficLightState = state
    objs[0].trafficLightConfidence = float(conf)
    objs[0].dRel = float(dist)
    objs[0].yRel = 0.0
    d["stereoObjects"] = so

  sm = _FakeSM(d)
  sm.valid["accelerometer"] = accel_valid
  sm.valid["navInstruction"] = nav_limit_ms > 0.0
  sm.updated["liveLocationKalman"] = False
  sm.valid["enhancedTrajectory"] = lhlc_speed is not None
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
  heading_err = lane.heading_theta_at(local_long + 3.0) - env.vehicle.heading_theta
  heading_err = (heading_err + math.pi) % (2 * math.pi) - math.pi
  # MetaDrive convention: +steer turns left and decreases local_lat -> +local_lat steers back to center.
  steer_cmd = float(np.clip(heading_err * 1.6 + local_lat * 0.25, -1.0, 1.0))
  dh = lane.heading_theta_at(local_long + 10.0) - lane.heading_theta_at(local_long)
  dh = (dh + math.pi) % (2 * math.pi) - math.pi
  road_kappa = abs(dh) / 10.0
  return steer_cmd, road_kappa, bool(on_lane)


class _PedalPI:
  """Closed-loop PI longitudinal actuator follower with anti-windup so MetaDrive tracks planner.output_a_target."""

  def __init__(self):
    self.integ = 0.0

  def step(self, a_target: float, a_ego: float) -> float:
    if a_target < 0.0 and self.integ > 0.0:
      self.integ = 0.0
    elif a_target > 0.0 and self.integ < 0.0:
      self.integ = 0.0
    err = a_target - a_ego
    self.integ = float(np.clip(self.integ + 0.05 * err, -0.45, 0.45))
    ff = a_target / 2.2 if a_target >= 0 else a_target / 3.2
    return float(np.clip(ff + self.integ + 0.18 * err, -1.0, 1.0))


def _warmup_to_speed(env, target_v: float, max_steps: int = 200) -> float:
  for _ in range(max_steps):
    v = float(env.vehicle.speed)
    if v >= target_v:
      return v
    steer_cmd, _, _ = _lane_center_steer(env)
    throttle = float(np.clip((target_v - v) * 0.6, 0.25, 1.0))
    env.step([steer_cmd, throttle])
  return float(env.vehicle.speed)


def _curve_env_config():
  from metadrive.component.map.pg_map import MapGenerateMethod
  from openpilot.tools.sim.bridge.metadrive.metadrive_bridge import curve_block, straight_block
  from openpilot.tools.sim.bridge.metadrive.metadrive_common import RGBCameraRoad
  from openpilot.tools.sim.lib.common import H, W

  return dict(
    use_render=False,
    vehicle_config=dict(enable_reverse=False, render_vehicle=False, image_source="rgb_road"),
    sensors={"rgb_road": (RGBCameraRoad, W, H)},
    image_on_cuda=False,
    image_observation=True,
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
      config=[None, straight_block(60), curve_block(60, 60), straight_block(120), straight_block(120)],
    ),
    decision_repeat=1,
    physics_world_step_size=0.05,
    preload_models=False,
    show_logo=False,
    anisotropic_filtering=False,
  )


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
      config=[None] + [straight_block(120) for _ in range(12)],
    ),
    decision_repeat=1,
    physics_world_step_size=0.05,
    preload_models=False,
    show_logo=False,
    anisotropic_filtering=False,
  )


@pytest.mark.slow
def test_eop10_metadrive_curve_vtsc_dlon_dlat_and_camera_sim():
  """EOP10 curve track: VTSC R=60m slowdown & exit recovery + DLON/DLAT + CameraSim (v4l2d OpenCL NV12 VisionIPC)."""
  pytest.importorskip("pyopencl")
  from metadrive.envs.metadrive_env import MetaDriveEnv
  from openpilot.tools.sim.bridge.metadrive.metadrive_process import C3_HPR, C3_POSITION
  from openpilot.tools.sim.lib.camera_sim import CameraSim
  from openpilot.tools.sim.lib.common import H, W

  prefix = os.environ.get("OPENPILOT_PREFIX", "")
  if prefix:
    os.makedirs(f"/dev/shm/msgq_{prefix}", exist_ok=True)

  env = MetaDriveEnv(_curve_env_config())
  cs = None
  try:
    env.reset()
    cs = CameraSim(dual_camera=False, tele_camera=False, stereo_camera=False)
    v0 = _warmup_to_speed(env, 12.0, max_steps=60)
    planner = LongitudinalPlanner(_make_cp(), init_v=v0)
    planner._vtsc_flags = (True, False)
    planner._vtsc_flags_t = 1e12  # freeze VTSC enabled without mutating persistent Params
    dlat = DLAT()

    speeds = []
    vtsc_targets = []
    dlon_saw_sharp_curve = False
    dlat_saw_laneless = False
    dlat_saw_laneful = False
    cam_frames = 0
    on_lane_steps = 0
    prev_v = v0
    pedal = _PedalPI()
    n_steps = 320

    t0 = time.monotonic()
    for step in range(n_steps):
      v_ego = float(env.vehicle.speed)
      a_ego = (v_ego - prev_v) / DT_MDL
      prev_v = v_ego
      speeds.append(v_ego)
      steer_cmd, road_kappa, on_lane = _lane_center_steer(env)
      if on_lane:
        on_lane_steps += 1

      lane_prob = 0.15 if (80 <= step < 140) else 0.92
      dlat_out = dlat.update_model(_model_v2(max(v_ego, 1.0), road_kappa, lane_prob=lane_prob), v_ego=v_ego)
      dlat_saw_laneless |= dlat_out.suggestion is DLATSuggestion.LANELESS
      dlat_saw_laneful |= dlat_out.suggestion is not DLATSuggestion.LANELESS

      sm = _make_sm(v_ego=v_ego, v_cruise_kph=54.0, a_ego=a_ego, road_kappa=road_kappa, lane_prob=lane_prob)
      planner.update(sm)
      vtsc_targets.append(planner.vtsc_v_target)
      if planner.dlon_result.get("triggers", {}).get("sharp_curve"):
        dlon_saw_sharp_curve = True

      env.step([steer_cmd, pedal.step(float(planner.output_a_target), a_ego)])

      if step % 20 == 0:
        cam = env.engine.sensors["rgb_road"]
        cam.get_cam().reparentTo(env.vehicle.origin)
        cam.get_cam().setPos(C3_POSITION)
        cam.get_cam().setHpr(C3_HPR)
        rgb = cam.perceive(to_float=False)
        yuv = cs.rgb_to_yuv(rgb)
        assert len(yuv) == W * H * 3 // 2
        cs.cam_send_yuv_road(yuv)
        cam_frames += 1

    assert time.monotonic() - t0 > 0
    assert on_lane_steps == n_steps, f"Left lane during curve: {on_lane_steps}/{n_steps}"
    active_steps = [i for i, t in enumerate(vtsc_targets) if t is not None]
    assert len(active_steps) >= 20, f"VTSC barely activated on R=60m curve: {len(active_steps)} ticks"
    v_curve_min = min(speeds[active_steps[0]:active_steps[-1] + 1])
    assert v_curve_min < 12.5, f"VTSC failed to slow MetaDrive car on curve: min={v_curve_min:.2f}"
    cleared_after_curve = [i for i in range(active_steps[0] + 15, n_steps) if vtsc_targets[i] is None]
    assert len(cleared_after_curve) >= 20, "VTSC never cleared on straight exit after curve"
    v_exit_peak = max(speeds[i] for i in cleared_after_curve)
    assert v_exit_peak > v_curve_min + 1.5, f"Vehicle did not recover after curve: {v_curve_min:.2f} -> {v_exit_peak:.2f}"
    assert dlat_saw_laneful and dlat_saw_laneless
    assert dlon_saw_sharp_curve
    assert cam_frames == 16
    print(
      f"METADRIVE_REPORT EOP10 VTSC_CAM: on_lane={on_lane_steps}/{n_steps} vtsc_ticks={len(active_steps)}",
      f"v_curve_min={v_curve_min:.2f}m/s v_exit_peak={v_exit_peak:.2f}m/s cam_frames={cam_frames}",
    )
  finally:
    if cs is not None:
      cs.close()
    env.close()


@pytest.mark.slow
def test_eop10_metadrive_tlsc_mtsc_mslc_brsc_gridd_and_tesla_can():
  """Dedicated MetaDrive behavioral tests for EOP10 TLSC, MSLC/NSLC, BRSC, GridD leadsV3/DrivableFusion, and Tesla CAN."""
  from metadrive.envs.metadrive_env import MetaDriveEnv

  env = MetaDriveEnv(_straight_env_config())
  sc = SimulatedCar()
  try:
    # ------------------------------------------------------------------
    # Scenario 1 (P0 SAFETY): TLSC red-light stop-line MPC + green release
    # ------------------------------------------------------------------
    env.reset()
    v0 = _warmup_to_speed(env, 14.0)
    now = [0.0]
    params_tlsc = _DictParams({"TLSCEnabled": True})
    planner_tlsc = LongitudinalPlanner(_make_cp(), init_v=v0)
    planner_tlsc.map_speed = MapSpeed(params_tlsc.get_bool, params_tlsc.get, clock=lambda: now[0])

    start_pos = np.array(env.vehicle.position, dtype=float)
    stop_line_x = 60.0
    prev_v = v0
    pedal = _PedalPI()
    assert v0 >= 13.0

    for _ in range(220):
      now[0] += DT_MDL
      v_ego = float(env.vehicle.speed)
      a_ego = (v_ego - prev_v) / DT_MDL
      prev_v = v_ego
      x_ego = float(np.linalg.norm(np.array(env.vehicle.position, dtype=float) - start_pos))
      dist_to_light = max(0.0, stop_line_x - x_ego)
      steer_cmd, _, on_lane = _lane_center_steer(env)
      assert on_lane
      sm = _make_sm(v_ego=v_ego, v_cruise_kph=60.0, a_ego=a_ego, traffic_light=("red", 0.92, dist_to_light))
      planner_tlsc.update(sm)
      env.step([steer_cmd, pedal.step(float(planner_tlsc.output_a_target), a_ego)])

    x_stopped = float(np.linalg.norm(np.array(env.vehicle.position, dtype=float) - start_pos))
    v_stopped = float(env.vehicle.speed)
    assert x_stopped <= stop_line_x + 1.0 and v_stopped < 0.5, (
      f"EOP10 TLSC ran red light in MetaDrive: x={x_stopped:.1f}m > {stop_line_x:.1f}m, v={v_stopped:.2f}m/s"
    )

    for step in range(200):
      now[0] += DT_MDL
      v_ego = float(env.vehicle.speed)
      a_ego = (v_ego - prev_v) / DT_MDL
      prev_v = v_ego
      x_ego = float(np.linalg.norm(np.array(env.vehicle.position, dtype=float) - start_pos))
      dist_to_light = max(0.0, stop_line_x - x_ego)
      steer_cmd, _, on_lane = _lane_center_steer(env)
      assert on_lane
      sm = _make_sm(v_ego=v_ego, v_cruise_kph=60.0, a_ego=a_ego, traffic_light=("green", 0.95, max(dist_to_light, 1.0)))
      planner_tlsc.update(sm)
      if step == 0:
        assert planner_tlsc.map_speed._light is None
      env.step([steer_cmd, pedal.step(float(planner_tlsc.output_a_target), a_ego)])
    assert float(env.vehicle.speed) > 4.5, f"EOP10 did not accelerate after green: v={float(env.vehicle.speed):.2f}"

    # ------------------------------------------------------------------
    # Scenario 2: MSLC/NSLC SpeedPolicy.LOWEST in MetaDrive
    # ------------------------------------------------------------------
    env.reset()
    v0 = _warmup_to_speed(env, 12.0)
    now[0] = 0.0
    params_map = _DictParams({"MTSCEnabled": True, "MSLCEnabled": True, "NSLCEnabled": True, "SpeedLimitPolicy": 4})
    planner_map = LongitudinalPlanner(_make_cp(), init_v=v0)
    planner_map.map_speed = MapSpeed(params_map.get_bool, params_map.get, clock=lambda: now[0])

    v_capped = []
    prev_v = v0
    pedal = _PedalPI()
    for step in range(320):
      now[0] += DT_MDL
      v_ego = float(env.vehicle.speed)
      a_ego = (v_ego - prev_v) / DT_MDL
      prev_v = v_ego
      steer_cmd, _, on_lane = _lane_center_steer(env)
      assert on_lane
      mo = None
      if step % 20 == 0:
        mo = messaging.new_message("mapdOut").mapdOut
        mo.speedLimit = (50.0 if step < 180 else 72.0) / 3.6
      nav_lim = (36.0 if step < 180 else 72.0) / 3.6
      sm = _make_sm(v_ego=v_ego, v_cruise_kph=72.0, a_ego=a_ego, mapd_out=mo, nav_limit_ms=nav_lim)
      planner_map.update(sm)
      if 120 <= step < 180:
        v_capped.append(v_ego)
      env.step([steer_cmd, pedal.step(float(planner_map.output_a_target), a_ego)])

    v_cap_mean = float(np.mean(v_capped))
    assert 8.8 <= v_cap_mean <= 11.2, f"MSLC/NSLC LOWEST failed to cap near 10 m/s: mean={v_cap_mean:.2f}"
    assert float(env.vehicle.speed) > v_cap_mean + 2.5

    # ------------------------------------------------------------------
    # Scenario 3: BRSC (-v[0] vertical axis, v[2] rejection, IMU dropout) + brake release
    # ------------------------------------------------------------------
    env.reset()
    v0 = _warmup_to_speed(env, 18.0)
    planner_brsc = LongitudinalPlanner(_make_cp(), init_v=v0)
    v_smooth_peak = v0
    v_rough_min = 999.0
    brsc_active = False
    prev_v = v0
    pedal = _PedalPI()
    for step in range(360):
      v_ego = float(env.vehicle.speed)
      a_ego = (v_ego - prev_v) / DT_MDL
      prev_v = v_ego
      steer_cmd, _, on_lane = _lane_center_steer(env)
      assert on_lane
      if step < 40:
        az = 9.81
        ax = 8.0 * math.sin(step * 1.1)
        accel_valid = True
      elif step < 260:
        az = 9.81 + 6.5 * math.sin(step * 1.3)
        ax = 0.0
        accel_valid = True
      else:
        az = 9.81
        ax = 0.0
        accel_valid = False
      sm = _make_sm(v_ego=v_ego, v_cruise_kph=72.0, a_ego=a_ego, az_vertical=az, ax_longitudinal=ax, accel_valid=accel_valid)
      planner_brsc.update(sm)
      if step < 40:
        assert planner_brsc.brsc_v_target is None, "Longitudinal v[2] falsely triggered BRSC"
        v_smooth_peak = max(v_smooth_peak, v_ego)
      elif step < 260:
        if planner_brsc.brsc_v_target is not None:
          brsc_active = True
          assert planner_brsc.brsc_v_target >= BRSC_MIN_SPEED_MS
        v_rough_min = min(v_rough_min, v_ego)
      else:
        assert planner_brsc.brsc_result is None and planner_brsc.brsc_v_target is None
      env.step([steer_cmd, pedal.step(float(planner_brsc.output_a_target), a_ego)])

    assert brsc_active and v_rough_min < v_smooth_peak - 1.5
    assert float(env.vehicle.speed) > v_rough_min + 1.5

    # Brake release responsiveness check
    planner_rel = LongitudinalPlanner(_make_cp(), init_v=10.0)
    planner_rel.output_a_target = -2.5
    planner_rel.a_desired = -2.5
    planner_rel.update(_make_sm(v_ego=10.0, v_cruise_kph=60.0))
    assert planner_rel.output_a_target > -1.0, f"Brake release throttled: {planner_rel.output_a_target:.2f}"

    # ------------------------------------------------------------------
    # Scenario 4: Live EOP10 Tesla CAN checksum round-trip + GridD leadsV3 & DrivableFusion
    # ------------------------------------------------------------------
    cp_tesla = car.CarParams.new_message()
    cp_tesla.carFingerprint = "TESLA_MODEL_3"
    cs_parser = CarState(cp_tesla)

    v_live = float(env.vehicle.speed)
    speed_kph = v_live * 3.6
    for _ in range(3):
      can_packets = [
        (0x257, sc._pack_speed(speed_kph), CANBUS.party),
        (0x118, sc._pack_system_status(gear=4, accel_pedal=0.0), CANBUS.party),
        (0x145, sc._pack_brake_status(False), CANBUS.party),
        (0x155, sc._pack_esp_b(speed_kph), CANBUS.party),
        (0x370, sc._pack_epas(4.0, 0.0), CANBUS.party),
        (0x286, sc._pack_di_state(cruise_state=2, digital_speed=speed_kph), CANBUS.party),
        (0x311, sc._pack_ui_warning(True, False, seatbelt=True), CANBUS.party),
        (0x129, sc._pack_sccm(0.0), CANBUS.autopilot_party),
        (0x39B, sc._pack_das_status(), CANBUS.autopilot_party),
      ]
      decoded = cs_parser.update(can_packets)
    assert abs(decoded.vEgoRaw - v_live) < 0.5, f"Tesla CAN decode mismatch: {decoded.vEgoRaw:.2f} vs {v_live:.2f}"
    assert decoded.leftBlinker is True and decoded.rightBlinker is False and decoded.cruiseState.enabled is True

    # GridD leadsV3 fusion + ego-motion DrivableFusion stepped with live MetaDrive speed
    class _GridHost:
      _fuse_modeld_detections = GridD._fuse_modeld_detections

    mdl_live = messaging.new_message("modelV2").modelV2
    leads = mdl_live.init("leadsV3", 1)
    leads[0].prob = 0.94
    leads[0].x = [32.0]
    leads[0].y = [-2.5]  # calibrated modelV2 frame: -2.5 Right = +2.5 Left
    leads[0].v = [-1.0]
    fused = _GridHost()._fuse_modeld_detections(mdl_live, None)
    assert len(fused) == 1 and fused[0]["yRel"] == pytest.approx(2.5, abs=0.05)

    df = DrivableFusion()
    grid_cells = np.full((df.spec.rows, df.spec.cols), OTHER, dtype=np.uint8)
    grid_cells[50:65, 20:30] = DRIVABLE
    df.step(now=1.0, v_forward=0.0)
    df.update("road", grid_cells, now=1.0)
    df.step(now=2.0, v_forward=v_live)
    shift = int(v_live / df.spec.resolution)
    assert shift >= 1 and df.logodds[55 - shift, 25] > 0.0
    print(
      f"METADRIVE_REPORT EOP10 CORE: tlsc_stop_x={x_stopped:.1f}m/{stop_line_x:.0f}m v_stop={v_stopped:.2f}m/s",
      f"mslc_nslc={v_cap_mean:.2f}m/s brsc={v_smooth_peak:.2f}->{v_rough_min:.2f}m/s",
      f"can_v={decoded.vEgoRaw:.2f}m/s drivable_shift={shift}",
    )
  finally:
    sc.close()
    env.close()


def _spawn_3d_vehicle(env, long_offset_m: float, lat_offset_m: float = 0.0):
  from metadrive.component.vehicle.vehicle_type import DefaultVehicle

  lane = env.vehicle.lane
  ego_long, _ = lane.local_coordinates(env.vehicle.position)
  s = ego_long + long_offset_m
  pos = lane.position(s, lat_offset_m)
  hdg = lane.heading_theta_at(s)
  v_cfg = env.config["vehicle_config"].copy()
  v_cfg.update(dict(spawn_lane_index=lane.index, spawn_longitude=s, spawn_lateral=lat_offset_m, enable_reverse=False))
  return env.engine.spawn_object(DefaultVehicle, vehicle_config=v_cfg, position=pos, heading=hdg)


def _clear_3d_vehicles(env, actors: list):
  if actors:
    env.engine.clear_objects([a.id for a in actors], force_destroy=True)
    actors.clear()


def _drive_3d_actor(actor, target_v: float, target_lat_offset: float | None = None, ref_lane=None):
  lane = ref_lane if ref_lane is not None else actor.lane
  local_long, local_lat = lane.local_coordinates(actor.position)
  lat_err = local_lat - (target_lat_offset if target_lat_offset is not None else 0.0)
  h_err = lane.heading_theta_at(local_long + 3.0) - actor.heading_theta
  h_err = (h_err + math.pi) % (2 * math.pi) - math.pi
  steer = float(np.clip(h_err * 1.6 + lat_err * 0.25, -1.0, 1.0))
  v = float(actor.speed)
  pedal = float(np.clip((target_v - v) * 0.45, -1.0, 1.0)) if target_v > 0.05 else (-1.0 if v > 0.1 else 0.0)
  actor.before_step([steer, pedal])


@pytest.mark.slow
def test_eop10_metadrive_3d_traffic_perception_aeb_bsd_sqsc_lhlc_rcsc_sam_alcc_pathd_adaptd():
  """Comprehensive EOP10 3D multi-vehicle & full-perception MetaDrive suite:
  MTSC->VTSC handover, SQSC, RCSC, LHLC 500m lookahead, SAM->DDSC, 3D ACC stop-and-go + GreenLight/LeadDeparture,
  3D RSS AEB (77GHz radar3d emergency braking + driver override + camera-only rejection), visual BGR->HSV->monod->GridD->TLSC
  pixel-to-brake chain, 3D TJA cut-in + PathD/DPP SUPERVISE/PRIMARY_LAT, 3D blind-spot car -> SideD BEV + Radar2D ->
  RadarZoneMonitor BSD WARNING & LCA block -> DesireHelper + SOC + ALCC + RED + CAT + AdaptD + SocketD SafetyManager."""
  from metadrive.envs.metadrive_env import MetaDriveEnv

  env = MetaDriveEnv(_straight_env_config())
  spawned: list = []
  try:
    # ------------------------------------------------------------------
    # Scenario 5: MTSC->VTSC handover + SQSC + RCSC + LHLC + SAM->DDSC
    # ------------------------------------------------------------------
    env.reset()
    v0 = _warmup_to_speed(env, 17.5)
    now = [0.0]
    params_all = _DictParams({"MTSCEnabled": True, "RCSCEnabled": True, "DDSCEnabled": True})
    planner = LongitudinalPlanner(_make_cp(), init_v=v0)
    planner._vtsc_flags = (True, False)
    planner._vtsc_flags_t = 1e12
    planner.map_speed = MapSpeed(params_all.get_bool, params_all.get, clock=lambda: now[0])
    planner.sqsc._cached_enabled = False
    planner.sqsc._last_param_update = 1e12

    prev_v = v0
    pedal = _PedalPI()
    v_mtsc_min = 999.0
    handover_seen = False
    for step in range(260):
      now[0] += DT_MDL
      v_ego = float(env.vehicle.speed)
      a_ego = (v_ego - prev_v) / DT_MDL
      prev_v = v_ego
      steer_cmd, _, on_lane = _lane_center_steer(env)
      assert on_lane
      mapd_ext = _make_mapd_ext(240.0, 1.0 / 55.0) if (step < 100 and step % 20 == 0) else None
      road_kappa = (1.0 / 55.0) if (60 <= step < 110) else 0.0
      sm = _make_sm(v_ego=v_ego, v_cruise_kph=72.0, a_ego=a_ego, road_kappa=road_kappa, mapd_ext=mapd_ext)
      planner.update(sm)
      if planner.map_speed._mtsc[0] is not None and planner.vtsc_v_target is not None:
        handover_seen = True
      if step < 160:
        v_mtsc_min = min(v_mtsc_min, v_ego)
      env.step([steer_cmd, pedal.step(float(planner.output_a_target), a_ego)])
    v_mtsc_rec = float(env.vehicle.speed)
    assert handover_seen and planner.map_speed._mtsc[0] is None
    assert v_mtsc_min < v0 - 2.0 and v_mtsc_rec > v_mtsc_min + 1.5

    # SQSC rough surface slowdown + smooth recovery
    planner.sqsc._cached_enabled = True
    planner.sqsc._cached_profile = "comfort"
    planner.sqsc.config = planner.sqsc.PROFILES["comfort"].copy()
    v_sqsc_start = float(env.vehicle.speed)
    v_sqsc_min = 999.0
    sqsc_active = False
    for step in range(200):
      now[0] += DT_MDL
      v_ego = float(env.vehicle.speed)
      a_ego = (v_ego - prev_v) / DT_MDL
      prev_v = v_ego
      steer_cmd, _, on_lane = _lane_center_steer(env)
      assert on_lane
      score = 0.85 if step < 110 else 0.05
      sm = _make_sm(v_ego=v_ego, v_cruise_kph=72.0, a_ego=a_ego, surface_score=score, surface_texture="rough" if step < 110 else "smooth")
      planner.update(sm)
      if step < 110:
        if planner.sqsc_v_target is not None:
          sqsc_active = True
        v_sqsc_min = min(v_sqsc_min, v_ego)
      env.step([steer_cmd, pedal.step(float(planner.output_a_target), a_ego)])
    planner.sqsc._cached_enabled = False
    v_sqsc_rec = float(env.vehicle.speed)
    assert sqsc_active and v_sqsc_min < v_sqsc_start - 1.5 and v_sqsc_rec > v_sqsc_min + 1.0

    # RCSC wet-road slowdown (with SQSC off) + recovery
    v_rcsc_start = _warmup_to_speed(env, 16.0, max_steps=120)
    prev_v = v_rcsc_start
    v_rcsc_min = 999.0
    for step in range(180):
      now[0] += DT_MDL
      v_ego = float(env.vehicle.speed)
      a_ego = (v_ego - prev_v) / DT_MDL
      prev_v = v_ego
      steer_cmd, _, on_lane = _lane_center_steer(env)
      assert on_lane
      sm = _make_sm(v_ego=v_ego, v_cruise_kph=72.0, a_ego=a_ego,
                    surface_score=0.55 if step < 100 else 0.05, surface_texture="wet" if step < 100 else "dry")
      planner.update(sm)
      if 30 <= step < 100:
        v_rcsc_min = min(v_rcsc_min, v_ego)
      env.step([steer_cmd, pedal.step(float(planner.output_a_target), a_ego)])
    assert v_rcsc_min < v_rcsc_start - 1.5 and float(env.vehicle.speed) > v_rcsc_min + 1.0

    # LHLC 500m enhancedTrajectory lookahead curve slowdown + clear
    v_lhlc_start = _warmup_to_speed(env, 16.0, max_steps=120)
    prev_v = v_lhlc_start
    v_lhlc_min = 999.0
    lhlc_seen = False
    for step in range(180):
      now[0] += DT_MDL
      v_ego = float(env.vehicle.speed)
      a_ego = (v_ego - prev_v) / DT_MDL
      prev_v = v_ego
      steer_cmd, _, on_lane = _lane_center_steer(env)
      assert on_lane
      sm = _make_sm(v_ego=v_ego, v_cruise_kph=72.0, a_ego=a_ego, lhlc_speed=9.5 if step < 100 else None)
      planner.update(sm)
      if step < 100:
        lhlc_seen |= planner.lhlc_active
        v_lhlc_min = min(v_lhlc_min, v_ego)
      env.step([steer_cmd, pedal.step(float(planner.output_a_target), a_ego)])
    v_lhlc_rec = float(env.vehicle.speed)
    assert lhlc_seen and v_lhlc_min < v_lhlc_start - 1.5 and v_lhlc_rec > v_lhlc_min + 1.0

    # SAM -> DDSC speed reduction & recovery on steeringPressed
    sam = DriverActivityMonitor(dt=1.5)
    v_sam_start = _warmup_to_speed(env, 18.0, max_steps=140)
    prev_v = v_sam_start
    v_sam_min = 999.0
    for step in range(200):
      now[0] += DT_MDL
      v_ego = float(env.vehicle.speed)
      a_ego = (v_ego - prev_v) / DT_MDL
      prev_v = v_ego
      steer_cmd, _, on_lane = _lane_center_steer(env)
      assert on_lane
      sam_st = sam.update(max(v_ego, 15.0), engaged=True, standstill=False, driver_engaged=(step < 10 or step >= 130))
      sm = _make_sm(v_ego=v_ego, v_cruise_kph=72.0, a_ego=a_ego, awareness=sam_st.awareness)
      planner.update(sm)
      if 30 <= step < 130:
        v_sam_min = min(v_sam_min, v_ego)
      env.step([steer_cmd, pedal.step(float(planner.output_a_target), a_ego)])
    assert v_sam_min < v_sam_start - 1.5 and float(env.vehicle.speed) > v_sam_min + 1.0
    print(
      f"METADRIVE_REPORT EOP10 SPEED_CAPS: mtsc={v0:.2f}->{v_mtsc_min:.2f}->{v_mtsc_rec:.2f}m/s",
      f"sqsc={v_sqsc_start:.2f}->{v_sqsc_min:.2f}m/s rcsc={v_rcsc_start:.2f}->{v_rcsc_min:.2f}m/s",
      f"lhlc={v_lhlc_start:.2f}->{v_lhlc_min:.2f}m/s sam_ddsc={v_sam_start:.2f}->{v_sam_min:.2f}m/s",
    )

    # ------------------------------------------------------------------
    # Scenario 6: 3D Lead ACC + RSS AEB (77GHz radar3d) + Pixel-to-Brake TLSC + 3D TJA + PathD/DPP
    # ------------------------------------------------------------------
    _clear_3d_vehicles(env, spawned)
    env.reset()
    v0 = _warmup_to_speed(env, 14.0)
    # Spawn stopped 3D MetaDrive obstacle 47 m ahead (d_rel~42m -> AEB brakes -3.48 m/s^2 cleanly into AEBState.HOLD)
    stopped_obs = _spawn_3d_vehicle(env, long_offset_m=47.0)
    spawned.append(stopped_obs)
    aeb = AEB()
    aeb.params = _DictParams({"AEBEnabled": True})
    aeb.enabled = True
    # Safety boundary check: camera-only lead (radar=False) MUST NOT trigger AEB!
    sm_cam_only = _make_sm(v_ego=14.0, v_cruise_kph=60.0, lead_one=(10.0, 0.0, 99), lead_radar=False)
    for _ in range(5):
      res_cam = aeb.update(sm_cam_only, sm_cam_only["carState"])
    assert not res_cam.is_active, "AEB illegally acted on non-radar camera-only lead!"

    prev_v = v0
    pedal = _PedalPI()
    aeb_emergency_seen = False
    min_aeb_decel = 0.0
    min_aeb_gap = 999.0
    for step in range(140):
      v_ego = float(env.vehicle.speed)
      a_ego = (v_ego - prev_v) / DT_MDL
      prev_v = v_ego
      _drive_3d_actor(stopped_obs, target_v=0.0, ref_lane=env.vehicle.lane)
      d_rel = max(0.0, float(stopped_obs.position[0] - env.vehicle.position[0]) - 4.8)
      min_aeb_gap = min(min_aeb_gap, d_rel)
      steer_cmd, _, on_lane = _lane_center_steer(env)
      assert on_lane
      sm_aeb = _make_sm(v_ego=v_ego, v_cruise_kph=60.0, a_ego=a_ego, lead_one=(d_rel, float(stopped_obs.speed), 77), lead_radar=True)
      aeb._last_update_time = time.monotonic() - DT_MDL  # deterministic DT_MDL step
      res_aeb = aeb.update(sm_aeb, sm_aeb["carState"])
      if res_aeb.level is AEBLevel.EMERGENCY:
        aeb_emergency_seen = True
      min_aeb_decel = min(min_aeb_decel, res_aeb.target_decel)
      a_cmd = get_aeb_decel_limit(res_aeb, 0.5)
      env.step([steer_cmd, pedal.step(a_cmd, a_ego)])
      if v_ego < 0.25 and step > 20:
        break
    v_aeb_stop = float(env.vehicle.speed)
    assert aeb_emergency_seen and min_aeb_decel <= -2.5, f"AEB did not reach EMERGENCY decel: {min_aeb_decel:.2f}"
    assert min_aeb_gap > 0.5 and v_aeb_stop < 0.4, f"AEB failed safe stop: gap={min_aeb_gap:.2f}m v={v_aeb_stop:.2f}"
    # Driver steering override (>3.0 Nm) immediately cancels AEB
    sm_ovr = _make_sm(v_ego=12.0, v_cruise_kph=60.0, lead_one=(10.0, 0.0, 77), lead_radar=True, steering_torque=5.0)
    res_ovr = aeb.update(sm_ovr, sm_ovr["carState"])
    assert res_ovr.state is AEBState.OVERRIDE and not res_ovr.is_active

    # Pixel-to-Brake TLSC Chain: synthetic BGR red lamp -> monod HSV classify -> fill_raw_detections -> GridD -> stereoObjects -> Planner
    img_bgr = np.zeros((240, 320, 3), dtype=np.uint8)
    img_bgr[60:110, 140:180] = (0, 0, 240)  # bright red lamp in BGR
    tl_state, tl_conf = classify_tl_bgr(img_bgr, (130, 50, 190, 160))
    assert tl_state == 1 and tl_conf >= 0.2, f"HSV traffic light classifier missed red lamp: {(tl_state, tl_conf)}"
    md_msg = messaging.new_message("monoDetections").monoDetections
    fill_raw_detections(md_msg, [], frame_id=1, timestamp_s=1.0, exec_time=0.01,
                        lights=[Light(x=30.0, y=0.0, state=tl_state, state_conf=tl_conf, conf=0.92, box=(0.5, 0.4, 0.05, 0.1))])

    class _MonoGridHost:
      _fuse_mono_detections = GridD._fuse_mono_detections

    fused_tl = _MonoGridHost()._fuse_mono_detections(md_msg, None)
    assert len(fused_tl) == 1 and fused_tl[0]["trafficLightState"] == 1
    so_tl = messaging.new_message("stereoObjects").stereoObjects
    it = so_tl.init("objects", 1)
    it[0].obstacleType = obstacle_type(fused_tl[0]["obstacleType"])
    it[0].trafficLightState = "red" if fused_tl[0]["trafficLightState"] == 1 else "unknown"
    it[0].trafficLightConfidence = float(fused_tl[0]["confidence"])
    it[0].dRel = float(fused_tl[0]["dRel"])
    it[0].yRel = float(fused_tl[0]["yRel"])
    planner_px = LongitudinalPlanner(_make_cp(), init_v=12.0)
    planner_px.map_speed = MapSpeed(lambda k: k == "TLSCEnabled", lambda k: None, clock=lambda: 1.0)
    sm_px = _make_sm(v_ego=12.0, v_cruise_kph=60.0, stereo_objects_msg=so_tl)
    for _ in range(5):
      planner_px.update(sm_px)
    assert planner_px.map_speed_result.tlsc_distance == pytest.approx(30.0, abs=0.2) and planner_px.output_a_target < -1.0

    # Real 3D TJA Cut-In + GreenLightNotice + LeadDepartureNotice + PathD/DPP SUPERVISE/PRIMARY_LAT
    _clear_3d_vehicles(env, spawned)
    env.reset()
    ego_lane_idx = env.vehicle.lane_index[2]
    adj_lat = 3.6 if ego_lane_idx == 0 else -3.6
    lead_tja = _spawn_3d_vehicle(env, long_offset_m=24.0)
    cutin_car = _spawn_3d_vehicle(env, long_offset_m=14.0, lat_offset_m=adj_lat)
    spawned.extend([lead_tja, cutin_car])
    for _ in range(70):
      steer_cmd, _, _ = _lane_center_steer(env)
      _drive_3d_actor(lead_tja, target_v=4.6, ref_lane=env.vehicle.lane)
      _drive_3d_actor(cutin_car, target_v=4.3, target_lat_offset=adj_lat, ref_lane=env.vehicle.lane)
      env.step([steer_cmd, 0.35 if float(env.vehicle.speed) < 4.6 else 0.05])
      if float(env.vehicle.speed) >= 4.0:
        break
    planner_tja = LongitudinalPlanner(_make_cp(), init_v=float(env.vehicle.speed))
    prev_v = float(env.vehicle.speed)
    pedal = _PedalPI()
    tja_cutin_clamped = False
    for step in range(100):
      v_ego = float(env.vehicle.speed)
      a_ego = (v_ego - prev_v) / DT_MDL
      prev_v = v_ego
      target_lat = adj_lat if step < 25 else 0.0
      _drive_3d_actor(lead_tja, target_v=4.6, ref_lane=env.vehicle.lane)
      _drive_3d_actor(cutin_car, target_v=3.2, target_lat_offset=target_lat, ref_lane=env.vehicle.lane)
      _, cutin_lat = env.vehicle.lane.local_coordinates(cutin_car.position)
      if abs(cutin_lat) < 1.8:
        lead_tuple = (max(1.0, float(cutin_car.position[0] - env.vehicle.position[0]) - 4.8), float(cutin_car.speed), 2)
      else:
        lead_tuple = (max(1.0, float(lead_tja.position[0] - env.vehicle.position[0]) - 4.8), float(lead_tja.speed), 1)
      steer_cmd, _, on_lane = _lane_center_steer(env)
      assert on_lane
      planner_tja.update(_make_sm(v_ego=v_ego, v_cruise_kph=50.0, a_ego=a_ego, lead_one=lead_tuple))
      if planner_tja.tja_result.cut_in and planner_tja.output_a_target <= 0.05:
        tja_cutin_clamped = True
      env.step([steer_cmd, pedal.step(float(planner_tja.output_a_target), a_ego)])
    assert tja_cutin_clamped

    gln = GreenLightNotice()
    assert not gln.update(True, True) and gln.update(False, True)
    ld = LeadDepartureNotice()
    l_stop = NS(status=True, dRel=5.0, vLead=0.0, radarTrackId=5)
    l_go = NS(status=True, dRel=7.2, vLead=2.5, radarTrackId=5)
    assert not ld.update(True, l_stop) and ld.update(True, l_go)
    rc = RuleChannelConsumer()
    assert rc.accel(int(ArbiterMode.SUPERVISE), 0.8, True, -1.6, True, False, DT_MDL)[0] < 0.8
    curvs = [rc.curvature(int(ArbiterMode.PRIMARY_LAT), 0.0, True, 0.015, True, False, DT_MDL)[0] for _ in range(25)]
    assert 0.0 < curvs[0] < curvs[-1] and curvs[-1] > 0.002
    print(
      f"METADRIVE_REPORT EOP10 AEB_TLSC_TJA: aeb_gap={min_aeb_gap:.2f}m aeb_decel={min_aeb_decel:.2f}m/s2",
      f"px_tlsc_a={planner_px.output_a_target:.2f}m/s2 tja_cutin={tja_cutin_clamped} dpp_curv={curvs[-1]:.4f}",
    )

    # ------------------------------------------------------------------
    # Scenario 7: Real 3D Blind-Spot Car -> SideD BEV + Radar2D -> RadarZoneMonitor BSD & LCA Block ->
    # DesireHelper + SOC + ALCC + RED + CAT + AdaptD + SocketD SafetyManager
    # ------------------------------------------------------------------
    _clear_3d_vehicles(env, spawned)
    env.reset()
    v_ego_bs = _warmup_to_speed(env, 16.0, max_steps=160)
    ego_lane_idx = env.vehicle.lane_index[2]
    adj_lat = 3.6 if ego_lane_idx == 0 else -3.6  # MetaDrive local_lat: +right, -left
    bs_car = _spawn_3d_vehicle(env, long_offset_m=4.0, lat_offset_m=adj_lat)
    spawned.append(bs_car)
    for _ in range(12):
      steer_cmd, _, _ = _lane_center_steer(env)
      _drive_3d_actor(bs_car, target_v=22.0, target_lat_offset=adj_lat, ref_lane=env.vehicle.lane)
      env.step([steer_cmd, 0.25 if float(env.vehicle.speed) < 16.0 else 0.05])

    ego_long, _ = env.vehicle.lane.local_coordinates(env.vehicle.position)
    bs_long, bs_lat = env.vehicle.lane.local_coordinates(bs_car.position)
    d_rel_bs = float(bs_long - ego_long)          # behind ego (negative)
    y_rel_bs = -float(bs_lat)                     # vehicle frame: +left, -right
    v_rel_bs = float(env.vehicle.speed - bs_car.speed)  # negative when faster car approaching from behind
    is_left_bs = y_rel_bs > 0.0

    # SideD ground-plane BEV reprojection (verifies R_cv sign & ground intersection)
    side_geo = SideCameraGeometry(
      fx=640.0, fy=640.0, cx=640.0, cy=360.0, img_w=1280.0, img_h=720.0,
      cam_x_m=0.5, cam_y_m=(0.95 if is_left_bs else -0.95), cam_z_m=1.0,
      yaw_rad=math.pi + (0.25 if is_left_bs else -0.25), pitch_rad=-0.17,
    )
    sx, sy, sz, _, _ = reproject_side_camera((580.0, 340.0, 700.0, 480.0), "car", (720, 1280), side_geo)
    assert sx < 0.0 and (sy > 0.0 if is_left_bs else sy < 0.0) and abs(sz) < 0.1

    # RadarZoneMonitor: associate BLE corner Radar2D track + SideD camera track -> BSD WARNING + LCA block!
    rzm = RadarZoneMonitor()
    corner_id = 2 if is_left_bs else 3  # rear_left or rear_right
    so_bs = messaging.new_message("stereoObjects").stereoObjects
    obs = so_bs.init("objects", 1)
    obs[0].trackId = encode_corner_track_id(corner_id, 1)
    obs[0].dRel = d_rel_bs
    obs[0].yRel = y_rel_bs
    obs[0].vRel = min(v_rel_bs, -9.0)  # fast approaching from behind -> WARNING
    obs[0].prob = 0.80
    obs[0].obstacleType = obstacle_type("car")
    side_msg = NS(detections=[NS(
      x=d_rel_bs + 0.5, y=y_rel_bs, vx=v_rel_bs, confidence=0.85,
      trackId=42, className="car", cameraSource="side_left" if is_left_bs else "side_right", sigmaX=0.5, sigmaY=0.5,
    )])
    cs_bs = messaging.new_message("carState").carState
    cs_bs.vEgo = float(env.vehicle.speed)
    cs_bs.leftBlinker = is_left_bs
    cs_bs.rightBlinker = not is_left_bs
    cs_bs.steeringPressed = True
    cs_bs.steeringTorque = 50.0 if is_left_bs else -50.0
    left_st, right_st, _ = rzm.update(so_bs, cs_bs, side_msg, None, t=10.0)
    active_st = left_st if is_left_bs else right_st
    assert active_st.detected and active_st.alert_level is ZoneAlertLevel.WARNING
    assert (rzm.lca_blocked_left if is_left_bs else rzm.lca_blocked_right) is True
    assert any(o.get("cameraAssociated") for o in rzm.last_fused_objects)

    # DesireHelper consumes RadarZoneMonitor blind_spot_alert and blocks LCA into the occupied blind spot
    dh = DesireHelper()
    dh._last_param_update = 1e12
    dh.lca_enabled = True
    bs_alert = messaging.new_message("blindSpotAlert").blindSpotAlert
    bs_alert.leftDetected = left_st.detected
    bs_alert.leftAlertLevel = int(left_st.alert_level.value)
    bs_alert.lcaBlockedLeft = rzm.lca_blocked_left
    bs_alert.rightDetected = right_st.detected
    bs_alert.rightAlertLevel = int(right_st.alert_level.value)
    bs_alert.lcaBlockedRight = rzm.lca_blocked_right
    dh.update(cs_bs, True, 0.0, blind_spot_alert=bs_alert)
    dh.update(cs_bs, True, 0.0, blind_spot_alert=bs_alert)
    assert dh.lane_change_state == LaneChangeState.preLaneChange, "DesireHelper failed to block LCA for 3D BSD vehicle"
    # Once blind spot clears, DesireHelper permits laneChangeStarting
    bs_clear = messaging.new_message("blindSpotAlert").blindSpotAlert
    dh.update(cs_bs, True, 0.0, blind_spot_alert=bs_clear)
    assert dh.lane_change_state == LaneChangeState.laneChangeStarting

    # SOC away from 3D blind-spot car + ALCC 3 BrakeModes + RED + CAT + BlinkerPause/Ramp
    soc = SOC(confirmation_frames=2)
    soc_in = SOCInput(
      v_ego=25.0, left_threat=is_left_bs, right_threat=not is_left_bs,
      lane_line_y=((-5.25,) * 10, (-1.75,) * 10, (1.75,) * 10, (5.25,) * 10),
      lane_line_probs=(0.9, 0.9, 0.9, 0.9), lane_line_stds=(0.1, 0.1, 0.1, 0.1),
    )
    soc.update(soc_in)
    soc_res = soc.update(soc_in)
    assert soc_res.active_suggestion and (soc_res.offset_m < 0.0 if is_left_bs else soc_res.offset_m > 0.0)

    assert ALCC(BrakeMode.MAINTAIN).update(ALCCInput(True, engage_request=True, brake_pressed=True)).state is ALCCState.ENABLED
    alcc_p = ALCC(BrakeMode.PAUSE)
    assert alcc_p.update(ALCCInput(True, engage_request=True)).state is ALCCState.ENABLED
    assert alcc_p.update(ALCCInput(True, engage_request=True, brake_pressed=True)).state is ALCCState.PAUSED
    alcc_d = ALCC(BrakeMode.DISENGAGE)
    assert alcc_d.update(ALCCInput(True, engage_request=True)).state is ALCCState.ENABLED
    assert alcc_d.update(ALCCInput(True, engage_request=True, brake_pressed=True)).state is ALCCState.DISABLED
    assert alcc_d.update(ALCCInput(True, engage_request=True, engage_button=True)).state is ALCCState.ENABLED

    red = RED(lambda k: True, clock=lambda: 1.0)
    for _ in range(3):
      red_out = red.update(_model_v2(15.0, road_edges=(-3.6, 0.25, 0.1, 0.1)), (0.0, 0.0), 15.0, [(float(x), 0.0) for x in range(0, 50, 5)], True)
    assert red_out["state"] == REDState.CRITICAL.name and red_out["path_override"] and curvature_nudge(red_out) > 0.0

    cat_f = SteerRatioFilter(base_steer_ratio=12.0)
    for i in range(35):
      cat_st = cat_f.step(i * DT_MDL, True, 13.2, 0.95, 0.3)
    assert cat_st.adaptive and cat_st.steer_ratio > 12.0
    bp = BlinkerPause(min_speed_mps=12.0, hold_s=1.0, dt=DT_MDL)
    ramp = SteeringResumeRamp()
    assert bp.update(8.0, True, False) and not bp.update(15.0, True, False)
    assert ramp.update(True, 1.0, 10.0, 0.0, 0.1)[0] < ramp.update(True, 1.0, 10.0, 0.0, 2.0)[0]

    # AdaptD: critical SOC (8%) + hot battery (58 C) derating
    adapt = AdaptiveDrivingComputer()
    vd = custom.NcpVehicleData.new_message()
    vd.valid = True
    vd.batterySoc = 8.0
    vd.rangeRemaining = 20.0
    vd.batteryTempMax = 58.0
    vd.batteryTempMin = 40.0
    vd.motorTemp = 60.0
    vd.inverterTemp = 55.0
    vd.coolantTemp = 50.0
    prof = adapt.update(vd)
    assert prof.thermal_derating and prof.accel_max <= 0.8

    # SocketD SafetyManager: block Tesla steering TX (0x488) while controls_allowed is False
    smgr = SafetyManager(use_params=False)
    assert not smgr.safety.state.controls_allowed
    assert smgr.check_tx_message(0x488, b"\x00\x00\x40\x00", 0) is False
    print(
      f"METADRIVE_REPORT EOP10 BSD_SAFETY: bsd_warn=True cam_assoc=True lca_block=True soc={soc_res.offset_m:+.3f}m",
      f"adaptd_amax={prof.accel_max:.1f} safety_tx_blocked=True v_ego_bs={v_ego_bs:.1f}m/s",
    )
  finally:
    _clear_3d_vehicles(env, spawned)
    env.close()

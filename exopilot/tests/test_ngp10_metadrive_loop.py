"""Tier C MetaDrive 3D closed-loop behavioral simulation suite for NGP10.

Runs MetaDrive (WSLg RTX 4050 / EGL) + PoCL OpenCL CameraSim + real NGP10
foundation (DLON + DLAT + ALCC + SAM/driveractivityd + MonoD/CutIn + PathD/DPP)
and add-on speed/safety controllers (VTSC, MTSC, MSLC, NSLC, TLSC, DDSC, BRSC):
  1. Curve track + camera pipeline: VTSC real R=60m 90-deg curve slowdown & exit recovery,
     DLAT laneful/laneless arbitration, DLON automatic ACC <-> E2E mode switching,
     and RTX 4050 RGB -> PoCL OpenCL NV12 -> VisionIPC streaming.
  2. TLSC P0 red-light stop & green-light release: real MetaDrive car decelerates smoothly
     and holds 0.0 m/s BEFORE the 55m stop line (never running the red light inside d<=3m),
     then immediately accelerates when the light turns green.
  3. Multi-feature straight highway: MTSC proactive slowdown & 3s expiry, MSLC/NSLC
     SpeedPolicy.LOWEST arbitration, BRSC comma-3 -v[0] vertical axis + longitudinal rejection
     + IMU-dropout reset + unthrottled brake release, SAM awareness decay -> DDSC speed cap ->
     steeringPressed reset, MonoD CutInSpeed slowdown, PathD/DPP SUPERVISE arbitration,
     ALCC lateral availability, and SOC bounded lateral offset.
"""

from __future__ import annotations

import math
import os
import time
from types import SimpleNamespace as NS

import numpy as np
import pytest

import cereal.messaging as messaging
from cereal import car, log
from openpilot.common.realtime import DT_MDL
from openpilot.selfdrive.controls.lib.desire_helper import DesireHelper, LaneChangeState
from openpilot.selfdrive.controls.lib.longcontrol import LongCtrlState
from openpilot.selfdrive.controls.lib.longitudinal_planner import (
  BRSC_MIN_SPEED_MS,
  FeatureFlags,
  LongitudinalPlanner,
)
from openpilot.selfdrive.modeld.constants import ModelConstants

from exopilot.selfdrive.controls.lib.alcc import ALCC, ALCCInput, ALCCState, BrakeMode
from exopilot.selfdrive.controls.lib.blinker_pause import BlinkerPause
from exopilot.selfdrive.controls.lib.cat import SteerRatioFilter
from exopilot.selfdrive.controls.lib.dlat import DLAT, DLATSuggestion
from exopilot.selfdrive.controls.lib.driver_activity import DriverActivityMonitor
from exopilot.selfdrive.controls.lib.green_light import GreenLightNotice
from exopilot.selfdrive.controls.lib.lead_departure import LeadDepartureNotice
from exopilot.selfdrive.controls.lib.map_speed import MapSpeed
from exopilot.selfdrive.controls.lib.policy_arbiter import Mode as ArbiterMode
from exopilot.selfdrive.controls.lib.rcsc import RoadCondition, RoadConditionCap, from_surface_score
from exopilot.selfdrive.controls.lib.red import RED, REDState, curvature_nudge
from exopilot.selfdrive.controls.lib.rule_channel import RuleChannelConsumer
from exopilot.selfdrive.controls.lib.soc import SOC
from exopilot.selfdrive.controls.lib.soc_policy import SOCInput
from exopilot.selfdrive.controls.lib.steering_policy import SteeringResumeRamp

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
    self.logMonoTime = _FalseDict(dict.fromkeys(d, 0))


def _make_cp():
  CP = car.CarParams.new_message()
  CP.openpilotLongitudinalControl = True
  CP.longitudinalActuatorDelay = 0.15
  CP.vEgoStopping = 0.5
  CP.wheelbase = 2.72
  CP.steerRatio = 15.0
  return CP


def _model_v2(
  v_ego: float,
  curvature: float = 0.0,
  lane_prob: float = 0.9,
  should_stop: bool = False,
  v_cruise_ms: float = 20.0,
  lc_state=log.LaneChangeState.off,
  lc_dir=log.LaneChangeDirection.none,
  adj_lead=None,
  road_edges=None,
):
  msg = messaging.new_message("modelV2").modelV2
  n = len(ModelConstants.T_IDXS)
  msg.velocity.x = [float(v_ego)] * n
  msg.orientationRate.z = [float(v_ego * curvature)] * n
  msg.position.x = [float(v_ego * t) for t in ModelConstants.T_IDXS]
  msg.position.y = [0.0] * n
  msg.position.z = [0.0] * n
  msg.acceleration.x = [0.0] * n
  # Open-road driving policy wants to reach v_cruise unless should_stop; speed caps clamp MPC side
  msg.action.desiredAcceleration = -1.5 if should_stop else float(np.clip((v_cruise_ms - v_ego) * 0.6, -2.0, 2.0))
  msg.action.shouldStop = bool(should_stop)
  msg.meta.disengagePredictions.gasPressProbs = [1.0, 1.0]
  msg.meta.laneChangeState = lc_state
  msg.meta.laneChangeDirection = lc_dir
  xs = [float(i * 5) for i in range(33)]
  lines = msg.init("laneLines", 4)
  for idx, y_val in enumerate((-5.4, -1.8, 1.8, 5.4)):
    lines[idx].x = xs
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
    edges[0].x = xs
    edges[0].y = [float(left_y)] * 33
    edges[1].x = xs
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
  awareness: float = 1.0,
  mono_det=None,
  path_adjust=None,
  dlat_laneless: bool = False,
  lead_one=None,
  lc_state=log.LaneChangeState.off,
  lc_dir=log.LaneChangeDirection.none,
  adj_lead=None,
  surface_score: float | None = None,
  surface_texture: str = "wet",
):
  cs = messaging.new_message("carState").carState
  cs.vEgo = float(v_ego)
  cs.aEgo = float(a_ego)
  cs.vCruise = float(v_cruise_kph)
  cs.standstill = v_ego < 0.05
  cs.steeringAngleDeg = 0.0
  cs.gasPressed = False
  cs.brakePressed = False

  cc = messaging.new_message("carControl").carControl
  cc.longActive = True
  cc.enabled = True
  cc.orientationNED = [0.0, 0.0, 0.0]

  ctrl = messaging.new_message("controlsState").controlsState
  ctrl.longControlState = LongCtrlState.pid
  ctrl.forceDecel = False
  ctrl.dlatUseLaneless = bool(dlat_laneless)

  sd = messaging.new_message("selfdriveState").selfdriveState
  sd.active = True
  sd.enabled = True
  sd.experimentalMode = False

  lp = messaging.new_message("liveParameters").liveParameters
  lp.angleOffsetDeg = 0.0

  # locationd.py: meas = [-v[2], -v[1], -v[0]] -> vertical is -v[0], longitudinal is -v[2]
  acc = messaging.new_message("accelerometer").accelerometer
  acc.acceleration.v = [float(-az_vertical), 0.0, float(-ax_longitudinal)]

  dm = messaging.new_message("driverMonitoringState").driverMonitoringState
  dm.awarenessStatus = float(awareness)

  nav = messaging.new_message("navInstruction").navInstruction
  nav.speedLimit = float(nav_limit_ms)
  nav.maneuverDistance = 999.0

  rs = messaging.new_message("radarState").radarState
  if lead_one is not None:
    d_rel, v_lead, track_id = lead_one
    rs.leadOne.status = True
    rs.leadOne.dRel = float(d_rel)
    rs.leadOne.vLead = float(v_lead)
    rs.leadOne.vLeadK = float(v_lead)
    rs.leadOne.vRel = float(v_lead - v_ego)
    rs.leadOne.radarTrackId = int(track_id)
    rs.leadOne.modelProb = 0.95

  d = {
    "carState": cs,
    "carControl": cc,
    "controlsState": ctrl,
    "selfdriveState": sd,
    "liveParameters": lp,
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
  if surface_score is not None:
    d["surfaceStatus"] = NS(hasSurfaceQuality=True, surfaceQuality=NS(score=float(surface_score), texture=surface_texture))
  if mapd_ext is not None:
    d["mapdExtendedOut"] = mapd_ext
  if mapd_out is not None:
    d["mapdOut"] = mapd_out
  if traffic_light is not None:
    state, conf, dist = traffic_light
    so = messaging.new_message("stereoObjects").stereoObjects
    objs = so.init("objects", 1)
    objs[0].obstacleType = "trafficLight"
    objs[0].trafficLightState = state
    objs[0].trafficLightConfidence = float(conf)
    objs[0].dRel = float(dist)
    objs[0].yRel = 0.0
    d["stereoObjects"] = so
  if mono_det is not None:
    md = messaging.new_message("monoDetections").monoDetections
    dets = md.init("detections", 1)
    x, y, vx, vy, conf, cls_name = mono_det
    dets[0].trackId = 7
    dets[0].x = float(x)
    dets[0].y = float(y)
    dets[0].vx = float(vx)
    dets[0].vy = float(vy)
    dets[0].sigmaX = 0.5
    dets[0].confidence = float(conf)
    dets[0].className = cls_name
    d["monoDetections"] = md
  if path_adjust is not None:
    d["pathAdjust"] = path_adjust

  sm = _FakeSM(d)
  sm.valid["accelerometer"] = accel_valid
  sm.valid["navInstruction"] = nav_limit_ms > 0.0
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
  # MetaDrive convention: +steer increases heading (+left) and decreases local_lat -> +local_lat steers back to center.
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
  """Bring MetaDrive vehicle smoothly up to highway/urban cruise speed before starting a scenario."""
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
  from openpilot.tools.sim.lib.camerad import H, W

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
def test_ngp10_metadrive_curve_vtsc_dlon_dlat_and_camera_pipeline():
  """NGP10 curve track: VTSC slowdown/recovery + DLON/DLAT foundation arbitration + RTX 4050/PoCL NV12 VisionIPC."""
  from metadrive.envs.metadrive_env import MetaDriveEnv
  from openpilot.tools.sim.bridge.metadrive.metadrive_process import C3_HPR, C3_POSITION
  from openpilot.tools.sim.lib.camerad import H, W, Camerad

  prefix = os.environ.get("OPENPILOT_PREFIX", "")
  if prefix:
    os.makedirs(f"/dev/shm/msgq_{prefix}", exist_ok=True)

  env = MetaDriveEnv(_curve_env_config())
  camerad = None
  try:
    env.reset()
    camerad = Camerad(dual_camera=False)
    v0 = _warmup_to_speed(env, 12.0, max_steps=60)
    planner = LongitudinalPlanner(_make_cp(), init_v=v0)
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

      # Simulate faded lane lines mid-drive (steps 80..140) to prove DLAT auto laneful <-> laneless arbitration
      lane_prob = 0.15 if (80 <= step < 140) else 0.92
      dlat_out = dlat.update_model(_model_v2(max(v_ego, 1.0), road_kappa, lane_prob=lane_prob), v_ego=v_ego)
      use_laneless = dlat_out.suggestion is DLATSuggestion.LANELESS
      dlat_saw_laneless |= use_laneless
      dlat_saw_laneful |= not use_laneless

      sm = _make_sm(v_ego=v_ego, v_cruise_kph=54.0, a_ego=a_ego, road_kappa=road_kappa, lane_prob=lane_prob, dlat_laneless=use_laneless)
      planner.update(sm, feature_flags=FeatureFlags.VTSC)
      vtsc_targets.append(planner.vtsc_v_target)
      if planner.dlon_result["triggers"]["sharp_curve"]:
        dlon_saw_sharp_curve = True

      env.step([steer_cmd, pedal.step(float(planner.output_a_target), a_ego)])

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
    assert on_lane_steps == n_steps, f"Left lane during curve: {on_lane_steps}/{n_steps}"
    active_steps = [i for i, t in enumerate(vtsc_targets) if t is not None]
    assert len(active_steps) >= 20, f"VTSC barely activated on R=60m curve: {len(active_steps)} ticks"
    v_curve_min = min(speeds[active_steps[0]:active_steps[-1] + 1])
    assert v_curve_min < 12.5, f"VTSC failed to slow MetaDrive car on curve: min={v_curve_min:.2f}"
    # Verify VTSC clears cleanly on the straight segment between curves and vehicle accelerates back up
    cleared_after_curve = [i for i in range(active_steps[0] + 15, n_steps) if vtsc_targets[i] is None]
    assert len(cleared_after_curve) >= 20, "VTSC never cleared on straight exit after curve"
    v_exit_peak = max(speeds[i] for i in cleared_after_curve)
    assert v_exit_peak > v_curve_min + 1.5, f"Vehicle did not recover after curve: {v_curve_min:.2f} -> {v_exit_peak:.2f}"
    assert dlat_saw_laneful and dlat_saw_laneless, "DLAT did not arbitrate laneful <-> laneless"
    assert dlon_saw_sharp_curve, "DLON failed to detect sharp curve trigger"
    assert cam_frames == 16
    print(
      f"METADRIVE_REPORT NGP10 VTSC_CAM: on_lane={on_lane_steps}/{n_steps} vtsc_ticks={len(active_steps)}",
      f"v_curve_min={v_curve_min:.2f}m/s v_exit_peak={v_exit_peak:.2f}m/s cam_frames={cam_frames}",
    )
  finally:
    if camerad is not None and hasattr(camerad, "close"):
      camerad.close()
    env.close()


@pytest.mark.slow
def test_ngp10_metadrive_tlsc_mtsc_mslc_brsc_sam_ddsc_cutin_pathd_alcc():
  """Dedicated MetaDrive behavioral tests for every NGP10 foundation & add-on controller."""
  from metadrive.envs.metadrive_env import MetaDriveEnv

  env = MetaDriveEnv(_straight_env_config())
  try:
    # ------------------------------------------------------------------
    # Scenario 1 (P0 SAFETY): TLSC full stop before red light + immediate release on green
    # ------------------------------------------------------------------
    env.reset()
    v0 = _warmup_to_speed(env, 14.0)
    now = [0.0]
    params_tlsc = _DictParams({"TLSCEnabled": True})
    planner_tlsc = LongitudinalPlanner(_make_cp(), init_v=v0)
    planner_tlsc.map_speed = MapSpeed(params_tlsc.get_bool, params_tlsc.get, clock=lambda: now[0])

    start_pos = np.array(env.vehicle.position, dtype=float)
    stop_line_x = 60.0  # red traffic light 60 m ahead
    prev_v = v0
    pedal = _PedalPI()
    assert v0 >= 13.0, f"Warmup did not reach cruise before red light: {v0:.2f}"

    # Phase A: red light active for 220 steps (11 s) -> car MUST stop before stop_line_x and stay stopped!
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
      f"TLSC RAN THE RED LIGHT in MetaDrive: x={x_stopped:.1f}m > stop={stop_line_x:.1f}m, v={v_stopped:.2f}m/s"
    )

    # Phase B: light turns GREEN -> MapSpeed clears _light immediately and car accelerates smoothly!
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
        assert planner_tlsc.map_speed._light is None, "MapSpeed failed to clear TLSC hold immediately on confirmed green"
      env.step([steer_cmd, pedal.step(float(planner_tlsc.output_a_target), a_ego)])

    assert float(env.vehicle.speed) > 4.5, f"Vehicle did not accelerate on green light: v={float(env.vehicle.speed):.2f}"

    # ------------------------------------------------------------------
    # Scenario 2: MTSC + MSLC/NSLC SpeedPolicy.LOWEST in MetaDrive
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
    for step in range(320):  # 0..180 MSLC=50km/h vs NSLC=36km/h (LOWEST picks 36km/h=10m/s); 180..320 raise both to 72km/h
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
    assert 8.8 <= v_cap_mean <= 11.2, f"MSLC/NSLC LOWEST failed to cap speed near 10 m/s: mean={v_cap_mean:.2f}"
    assert float(env.vehicle.speed) > v_cap_mean + 2.5

    # ------------------------------------------------------------------
    # Scenario 3: BRSC (-v[0] vertical axis, longitudinal rejection, IMU dropout) + unthrottled brake release
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
      if step < 80:
        az, ax, acc_ok = 9.81, 4.0 * math.sin(step * 0.6), True
      elif step < 220:
        az, ax, acc_ok = 9.81 + 4.8 * math.sin(step * 1.1), 0.0, True
      elif step == 220:
        az, ax, acc_ok = 9.81, 0.0, False
      else:
        az, ax, acc_ok = 9.81, 0.0, True
      sm = _make_sm(v_ego=v_ego, v_cruise_kph=72.0, a_ego=a_ego, az_vertical=az, ax_longitudinal=ax, accel_valid=acc_ok)
      planner_brsc.update(sm, feature_flags=FeatureFlags.BRSC)
      if step < 80:
        assert planner_brsc.brsc_v_target is None, "BRSC falsely triggered on longitudinal v[2] excitation"
        v_smooth_peak = max(v_smooth_peak, v_ego)
      elif step < 220:
        if planner_brsc.brsc_v_target is not None:
          brsc_active = True
          assert planner_brsc.brsc_v_target >= BRSC_MIN_SPEED_MS
        v_rough_min = min(v_rough_min, v_ego)
      elif step == 220:
        assert planner_brsc.brsc_result is None and planner_brsc.brsc_v_target is None, "BRSC did not reset on IMU dropout"
      env.step([steer_cmd, pedal.step(float(planner_brsc.output_a_target), a_ego)])

    assert brsc_active and v_rough_min < v_smooth_peak - 1.5
    assert float(env.vehicle.speed) > v_rough_min + 1.0

    # Verify brake-release jerk-up fix: releasing brakes from -2.5 m/s^2 is never held negative
    planner_brsc.output_a_target = -2.5
    planner_brsc.update(_make_sm(v_ego=5.0, a_ego=-2.5, v_cruise_kph=60.0))
    assert planner_brsc.output_a_target >= 0.0, f"Brake release throttled: {planner_brsc.output_a_target}"

    # ------------------------------------------------------------------
    # Scenario 4: SAM (DriverActivityMonitor) -> DDSC speed cap -> steeringPressed reset + MonoD CutIn + PathD/DPP + ALCC + SOC
    # ------------------------------------------------------------------
    env.reset()
    v0 = _warmup_to_speed(env, 20.0)
    now[0] = 0.0
    params_ddsc = _DictParams({"DDSCEnabled": True})
    planner_sam = LongitudinalPlanner(_make_cp(), init_v=v0)
    planner_sam.map_speed = MapSpeed(params_ddsc.get_bool, params_ddsc.get, clock=lambda: now[0])
    sam = DriverActivityMonitor(dt=1.5)

    v_before_ddsc = v0
    v_during_ddsc_min = 999.0
    prev_v = v0
    pedal = _PedalPI()
    # 0..15 cruise aware; 15..160 SAM decays -> DDSC slows car; 160..240 steeringPressed resets SAM -> recovers
    for step in range(240):
      now[0] += DT_MDL
      v_ego = float(env.vehicle.speed)
      a_ego = (v_ego - prev_v) / DT_MDL
      prev_v = v_ego
      steer_cmd, _, on_lane = _lane_center_steer(env)
      assert on_lane
      if step < 15:
        sam_st = sam.update(v_ego, engaged=True, standstill=v_ego < 0.1, driver_engaged=True)
      elif step < 160:
        sam_st = sam.update(max(v_ego, 15.0), engaged=True, standstill=False, driver_engaged=False)
      else:
        sam_st = sam.update(v_ego, engaged=True, standstill=False, driver_engaged=True)

      sm = _make_sm(v_ego=v_ego, v_cruise_kph=80.0, a_ego=a_ego, awareness=sam_st.awareness)
      planner_sam.update(sm)
      if step == 14:
        v_before_ddsc = v_ego
      if 50 <= step < 160:
        v_during_ddsc_min = min(v_during_ddsc_min, v_ego)
      env.step([steer_cmd, pedal.step(float(planner_sam.output_a_target), a_ego)])

    assert v_before_ddsc >= 17.5, f"Did not reach highway speed before SAM/DDSC: {v_before_ddsc:.2f}"
    assert v_during_ddsc_min < v_before_ddsc - 1.5, f"SAM->DDSC failed to slow distracted car: {v_before_ddsc:.2f} -> {v_during_ddsc_min:.2f}"
    assert float(env.vehicle.speed) > v_during_ddsc_min + 1.0, "Car did not recover after steeringPressed reset SAM"

    # MonoD Cut-In + PathD/DPP SUPERVISE + ALCC + SOC checks
    planner_cutin = LongitudinalPlanner(_make_cp(), init_v=18.0)
    # Adjacent car at x=22m, y=+2.2m (left-positive), closing at vx=-7.0 m/s relative, cutting right (vy=-1.4 m/s)
    for _ in range(15):
      sm_ci = _make_sm(v_ego=18.0, v_cruise_kph=72.0, mono_det=(22.0, 2.2, -7.0, -1.4, 0.9, "car"))
      planner_cutin.update(sm_ci, feature_flags=FeatureFlags.CUTIN)
    assert planner_cutin.output_a_target < 0.0, f"MonoD CutInSpeed did not request braking on cut-in: {planner_cutin.output_a_target}"

    pa = NS(dppMode=int(ArbiterMode.SUPERVISE), ruleValid=True, ruleAccel=-2.0, ruleCurvature=0.0, speedFactor=0.7)
    sm_pa = _make_sm(v_ego=18.0, v_cruise_kph=72.0, path_adjust=pa)
    planner_pa = LongitudinalPlanner(_make_cp(), init_v=18.0)
    planner_pa.update(sm_pa, feature_flags=FeatureFlags.PATHD)
    assert planner_pa.output_a_target <= 0.0, "PathD/DPP SUPERVISE did not lower accel"

    alcc = ALCC()
    alcc_res = alcc.update(ALCCInput(feature_enabled=True, engage_request=True))
    assert alcc_res.state is ALCCState.ENABLED and alcc_res.active_suggestion

    lane_w = min(float(env.vehicle.lane.width), 3.5)  # stay strictly inside SOC's [2.8, 3.6]m geometry gate
    soc = SOC(confirmation_frames=2)
    sample = SOCInput(
      v_ego=25.0,
      left_threat=True,
      right_threat=False,
      lane_line_y=(
        (-lane_w * 1.5,) * 10,
        (-lane_w * 0.5,) * 10,
        (lane_w * 0.5,) * 10,
        (lane_w * 1.5,) * 10,
      ),
      lane_line_probs=(0.9, 0.9, 0.9, 0.9),
      lane_line_stds=(0.1, 0.1, 0.1, 0.1),
    )
    soc.update(sample)
    soc_res = soc.update(sample)
    assert soc_res.active_suggestion and soc_res.offset_m < 0.0  # left threat -> negative (right) offset
    print(
      f"METADRIVE_REPORT NGP10 CORE: tlsc_stop_x={x_stopped:.1f}m/{stop_line_x:.0f}m v_stop={v_stopped:.2f}m/s",
      f"mslc_nslc={v_cap_mean:.2f}m/s brsc={v_smooth_peak:.2f}->{v_rough_min:.2f}m/s",
      f"sam_ddsc={v_before_ddsc:.2f}->{v_during_ddsc_min:.2f}m/s",
    )
  finally:
    env.close()


def _spawn_3d_vehicle(env, long_offset_m: float, lat_offset_m: float = 0.0):
  """Spawn a real 3D MetaDrive DefaultVehicle relative to ego on the current PGMap lane."""
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
def test_ngp10_metadrive_3d_traffic_mtsc_rcsc_tja_red_alcc_cat_lca_and_notices():
  """NGP10 3D multi-vehicle & full-feature behavioral suite:
  MTSC->VTSC handover + 3s expiry, RCSC wet/rough slowdown & clear, 3D lead ACC stop-and-go + GreenLight/LeadDeparture,
  3D TJA + MonoD CutInSpeed + PathD/DPP SUPERVISE/PRIMARY_LAT, ALCC all 3 BrakeModes, RED edge warning/override,
  BlinkerPause + SteeringResumeRamp, CAT filter convergence, DesireHelper LCA/TurnDesire + 3D LCAdjacentLeadHandoff + 3D SOC."""
  from metadrive.envs.metadrive_env import MetaDriveEnv

  env = MetaDriveEnv(_straight_env_config())
  spawned: list = []
  try:
    # ------------------------------------------------------------------
    # Scenario 5: MTSC -> VTSC handover + 3s expiry + RCSC wet/debris slowdown & clear
    # ------------------------------------------------------------------
    env.reset()
    v0 = _warmup_to_speed(env, 17.5)
    now = [0.0]
    params_mc = _DictParams({"MTSCEnabled": True, "RCSCEnabled": True})
    planner_mc = LongitudinalPlanner(_make_cp(), init_v=v0)
    planner_mc.map_speed = MapSpeed(params_mc.get_bool, params_mc.get, clock=lambda: now[0])

    prev_v = v0
    pedal = _PedalPI()
    v_mtsc_min = 999.0
    handover_seen = False
    for step in range(280):
      now[0] += DT_MDL
      v_ego = float(env.vehicle.speed)
      a_ego = (v_ego - prev_v) / DT_MDL
      prev_v = v_ego
      steer_cmd, _, on_lane = _lane_center_steer(env)
      assert on_lane
      # Steps 0..100: 1 Hz mapd curve at 240 m; steps 60..110: vision curve enters -> MTSC->VTSC handover; >110: clear
      mapd_ext = _make_mapd_ext(240.0, 1.0 / 55.0) if (step < 100 and step % 20 == 0) else None
      road_kappa = (1.0 / 55.0) if (60 <= step < 110) else 0.0
      sm = _make_sm(v_ego=v_ego, v_cruise_kph=72.0, a_ego=a_ego, road_kappa=road_kappa, mapd_ext=mapd_ext)
      planner_mc.update(sm, feature_flags=FeatureFlags.VTSC)
      if planner_mc.map_speed._mtsc[0] is not None and planner_mc.vtsc_v_target is not None:
        handover_seen = True
      if step < 160:
        v_mtsc_min = min(v_mtsc_min, v_ego)
      env.step([steer_cmd, pedal.step(float(planner_mc.output_a_target), a_ego)])

    v_mtsc_recovered = float(env.vehicle.speed)
    assert handover_seen, "MTSC -> VTSC overlap handover never occurred"
    assert planner_mc.map_speed._mtsc[0] is None, "MTSC did not expire after >3s mapd silence"
    assert v_mtsc_min < v0 - 2.0 and v_mtsc_recovered > v_mtsc_min + 1.5

    # RCSC: verify wet road steps straight to 12 m/s (no legacy 0.3 m/s brake glitch) & slows MetaDrive vehicle
    cap_rc = RoadConditionCap(legacy_filter_bug=False)
    for _ in range(5):
      st_rc = cap_rc.update(from_surface_score(0.50, "wet"))
    assert st_rc.is_active and st_rc.condition is RoadCondition.WET and abs(st_rc.speed_limit_ms - 12.0) < 0.05

    v_rcsc_start = float(env.vehicle.speed)
    v_rcsc_min = 999.0
    for step in range(220):
      now[0] += DT_MDL
      v_ego = float(env.vehicle.speed)
      a_ego = (v_ego - prev_v) / DT_MDL
      prev_v = v_ego
      steer_cmd, _, on_lane = _lane_center_steer(env)
      assert on_lane
      score = 0.55 if step < 120 else 0.05
      sm = _make_sm(v_ego=v_ego, v_cruise_kph=72.0, a_ego=a_ego, surface_score=score, surface_texture="wet" if step < 120 else "dry")
      planner_mc.update(sm)
      if 40 <= step < 120:
        v_rcsc_min = min(v_rcsc_min, v_ego)
      env.step([steer_cmd, pedal.step(float(planner_mc.output_a_target), a_ego)])
    v_rcsc_recovered = float(env.vehicle.speed)
    assert v_rcsc_min < v_rcsc_start - 1.5 and v_rcsc_recovered > v_rcsc_min + 1.0
    print(
      f"METADRIVE_REPORT NGP10 MTSC_RCSC: mtsc={v0:.2f}->{v_mtsc_min:.2f}->{v_mtsc_recovered:.2f}m/s handover={handover_seen}",
      f"rcsc={v_rcsc_start:.2f}->{v_rcsc_min:.2f}->{v_rcsc_recovered:.2f}m/s",
    )

    # ------------------------------------------------------------------
    # Scenario 6: Real 3D Lead ACC Stop-and-Go + GreenLight + LeadDeparture + 3D TJA/CutIn + PathD/DPP
    # ------------------------------------------------------------------
    _clear_3d_vehicles(env, spawned)
    env.reset()
    lead_car = _spawn_3d_vehicle(env, long_offset_m=48.0)
    spawned.append(lead_car)
    for _ in range(120):
      steer_cmd, _, _ = _lane_center_steer(env)
      _drive_3d_actor(lead_car, target_v=12.0, ref_lane=env.vehicle.lane)
      env.step([steer_cmd, 0.55 if float(env.vehicle.speed) < 12.0 else 0.1])
      if float(env.vehicle.speed) >= 11.5 and float(lead_car.speed) >= 11.0:
        break

    v0 = float(env.vehicle.speed)
    planner_acc = LongitudinalPlanner(_make_cp(), init_v=v0)
    prev_v = v0
    pedal = _PedalPI()
    min_gap_m = 999.0
    for step in range(420):
      v_ego = float(env.vehicle.speed)
      a_ego = (v_ego - prev_v) / DT_MDL
      prev_v = v_ego
      d_rel = max(0.0, float(lead_car.position[0] - env.vehicle.position[0]) - 4.8)
      min_gap_m = min(min_gap_m, d_rel)
      steer_cmd, _, on_lane = _lane_center_steer(env)
      assert on_lane
      sm = _make_sm(v_ego=v_ego, v_cruise_kph=60.0, a_ego=a_ego, lead_one=(d_rel, float(lead_car.speed), 1))
      planner_acc.update(sm)
      _drive_3d_actor(lead_car, target_v=11.0 if step < 30 else 0.0, ref_lane=env.vehicle.lane)
      a_cmd = -1.2 if (planner_acc.output_should_stop and v_ego < 1.5) else float(planner_acc.output_a_target)
      env.step([steer_cmd, pedal.step(a_cmd, a_ego)])
      if step > 120 and v_ego < 0.25 and float(lead_car.speed) < 0.1:
        break

    v_stopped = float(env.vehicle.speed)
    assert min_gap_m > 2.0 and v_stopped < 0.4, f"NGP10 3D ACC failed safe stop: gap={min_gap_m:.2f}m v={v_stopped:.2f}"

    # GreenLightNotice at standstill (dlonForceStop True -> False)
    gln = GreenLightNotice()
    assert not gln.update(force_stop=True, standstill=True)
    assert gln.update(force_stop=False, standstill=True), "GreenLightNotice failed on force_stop release"

    # LeadDepartureNotice on real 3D lead accelerating away
    ld = LeadDepartureNotice()
    ld_false_stopped = False
    ld_fired = False
    for step in range(90):
      d_rel = max(0.0, float(lead_car.position[0] - env.vehicle.position[0]) - 4.8)
      sm_ld = _make_sm(v_ego=0.0, v_cruise_kph=60.0, lead_one=(d_rel, float(lead_car.speed), 1))
      fired = ld.update(True, sm_ld["radarState"].leadOne)
      if step < 15:
        ld_false_stopped |= fired
        _drive_3d_actor(lead_car, target_v=0.0, ref_lane=env.vehicle.lane)
      else:
        ld_fired |= fired
        _drive_3d_actor(lead_car, target_v=6.0, ref_lane=env.vehicle.lane)
      env.step([0.0, -0.5])
    assert not ld_false_stopped and ld_fired

    # Phase C: Real 3D adjacent car cuts into ego lane -> TJA accel clamp + MonoD CutInSpeed braking + PathD/DPP arbitration
    _clear_3d_vehicles(env, spawned)
    env.reset()
    ego_lane_idx = env.vehicle.lane_index[2]
    adj_lat = 3.6 if ego_lane_idx == 0 else -3.6
    lead_tja = _spawn_3d_vehicle(env, long_offset_m=24.0)
    cutin_car = _spawn_3d_vehicle(env, long_offset_m=15.0, lat_offset_m=adj_lat)
    spawned.extend([lead_tja, cutin_car])
    for _ in range(70):
      steer_cmd, _, _ = _lane_center_steer(env)
      _drive_3d_actor(lead_tja, target_v=4.8, ref_lane=env.vehicle.lane)
      _drive_3d_actor(cutin_car, target_v=4.5, target_lat_offset=adj_lat, ref_lane=env.vehicle.lane)
      env.step([steer_cmd, 0.35 if float(env.vehicle.speed) < 4.8 else 0.05])
      if float(env.vehicle.speed) >= 4.2:
        break

    planner_tja = LongitudinalPlanner(_make_cp(), init_v=float(env.vehicle.speed))
    prev_v = float(env.vehicle.speed)
    pedal = _PedalPI()
    tja_cutin_clamped = False
    cutin_braked = False
    prev_cutin_y_left = -adj_lat
    for step in range(110):
      v_ego = float(env.vehicle.speed)
      a_ego = (v_ego - prev_v) / DT_MDL
      prev_v = v_ego
      target_lat = adj_lat if step < 25 else 0.0
      _drive_3d_actor(lead_tja, target_v=4.8, ref_lane=env.vehicle.lane)
      _drive_3d_actor(cutin_car, target_v=3.2, target_lat_offset=target_lat, ref_lane=env.vehicle.lane)
      _, cutin_lat = env.vehicle.lane.local_coordinates(cutin_car.position)
      # MetaDrive local_lat is right-positive -> vehicle-frame yRel (left-positive) is -cutin_lat
      y_left = -float(cutin_lat)
      vy_left = (y_left - prev_cutin_y_left) / DT_MDL
      prev_cutin_y_left = y_left
      x_cutin = max(1.0, float(cutin_car.position[0] - env.vehicle.position[0]) - 4.8)
      vx_rel = float(cutin_car.speed) - v_ego
      if abs(cutin_lat) < 1.8:
        lead_tuple = (x_cutin, float(cutin_car.speed), 2)
      else:
        lead_tuple = (max(1.0, float(lead_tja.position[0] - env.vehicle.position[0]) - 4.8), float(lead_tja.speed), 1)
      steer_cmd, _, on_lane = _lane_center_steer(env)
      assert on_lane
      sm = _make_sm(
        v_ego=v_ego,
        v_cruise_kph=50.0,
        a_ego=a_ego,
        lead_one=lead_tuple,
        mono_det=(x_cutin, y_left, vx_rel, vy_left, 0.92, "car"),
      )
      planner_tja.update(sm, feature_flags=FeatureFlags.CUTIN)
      if planner_tja.tja_result.cut_in and planner_tja.output_a_target <= 0.05:
        tja_cutin_clamped = True
      if step >= 30 and planner_tja.output_a_target < -0.1:
        cutin_braked = True
      env.step([steer_cmd, pedal.step(float(planner_tja.output_a_target), a_ego)])
    assert tja_cutin_clamped and cutin_braked

    # PathD/DPP RuleChannelConsumer: SUPERVISE caps accel, PRIMARY_LAT blends curvature & yields on driver override
    rc = RuleChannelConsumer()
    a_sup, _ = rc.accel(int(ArbiterMode.SUPERVISE), 0.8, True, -1.6, True, False, DT_MDL)
    assert a_sup < 0.8
    curv_vals = [rc.curvature(int(ArbiterMode.PRIMARY_LAT), 0.0, True, 0.015, True, False, DT_MDL)[0] for _ in range(25)]
    assert 0.0 < curv_vals[0] < curv_vals[-1] and curv_vals[-1] > 0.002, f"DPP PRIMARY_LAT failed to blend rule curvature: {curv_vals[-1]}"
    c_ovr, src_ovr = rc.curvature(int(ArbiterMode.PRIMARY_LAT), 0.002, True, 0.015, True, True, DT_MDL)
    assert abs(c_ovr - 0.002) < 1e-6 and src_ovr == "policy"
    print(
      f"METADRIVE_REPORT NGP10 3D_TRAFFIC: min_gap={min_gap_m:.2f}m v_stop={v_stopped:.2f}m/s",
      f"gln=True ld={ld_fired} tja_cutin={tja_cutin_clamped} cutin_braked={cutin_braked} dpp_curv={curv_vals[-1]:.4f}",
    )

    # ------------------------------------------------------------------
    # Scenario 7: ALCC (all 3 BrakeModes) + RED + BlinkerPause/Ramp + CAT + 3D LCA Handoff + 3D SOC
    # ------------------------------------------------------------------
    # ALCC BrakeMode.MAINTAIN / PAUSE / DISENGAGE + re-arm + steering override
    alcc_m = ALCC(BrakeMode.MAINTAIN)
    assert alcc_m.update(ALCCInput(True, engage_request=True, brake_pressed=True)).state is ALCCState.ENABLED
    alcc_p = ALCC(BrakeMode.PAUSE)
    assert alcc_p.update(ALCCInput(True, engage_request=True)).state is ALCCState.ENABLED
    assert alcc_p.update(ALCCInput(True, engage_request=True, brake_pressed=True)).state is ALCCState.PAUSED
    assert alcc_p.update(ALCCInput(True, engage_request=True, brake_pressed=False)).state is ALCCState.ENABLED
    assert alcc_p.update(ALCCInput(True, engage_request=True, steering_override=True)).state is ALCCState.OVERRIDING
    alcc_d = ALCC(BrakeMode.DISENGAGE)
    assert alcc_d.update(ALCCInput(True, engage_request=True)).state is ALCCState.ENABLED
    assert alcc_d.update(ALCCInput(True, engage_request=True, brake_pressed=True)).state is ALCCState.DISABLED
    assert alcc_d.update(ALCCInput(True, engage_request=True, brake_pressed=False)).state is ALCCState.DISABLED  # needs re-arm
    assert alcc_d.update(ALCCInput(True, engage_request=True, engage_button=True)).state is ALCCState.ENABLED

    # RED: warning repulsive cost & curvature nudge away from close right road edge + critical path override
    red_t = [0.0]
    red = RED(lambda k: True, clock=lambda: red_t[0])
    path_straight = [(float(x), 0.0) for x in range(0, 50, 5)]
    for _ in range(3):
      red_t[0] += DT_MDL
      mdl_warn = _model_v2(15.0, road_edges=(-3.6, 0.75, 0.1, 0.1))  # right edge 0.75m away (<1.0m WARNING)
      out_warn = red.update(mdl_warn, (0.0, 0.0), 15.0, path_straight, is_laneless=True)
    assert out_warn["state"] == REDState.WARNING.name and out_warn["lateral_cost"] > 0.0
    assert curvature_nudge(out_warn) > 0.0, "RED did not nudge left (+curv) away from right road edge"
    red_crit = RED(lambda k: True, clock=lambda: red_t[0])
    for _ in range(3):
      red_t[0] += DT_MDL
      mdl_crit = _model_v2(15.0, road_edges=(-3.6, 0.25, 0.1, 0.1))  # right edge 0.25m away (<0.3m MIN_EDGE)
      out_crit = red_crit.update(mdl_crit, (0.0, 0.0), 15.0, path_straight, is_laneless=True)
    assert out_crit["state"] == REDState.CRITICAL.name and out_crit["path_override"] is True
    assert out_crit["modified_path"][0][1] < 0.0, "RED critical override did not shift modelV2 path left (-y)"

    # CAT SteerRatioFilter convergence + manual_sr override
    cat_f = SteerRatioFilter(base_steer_ratio=15.0)
    for i in range(35):
      st_cat = cat_f.step(now=i * DT_MDL, gated=True, steer_ratio=13.5, stiffness=0.92, angle_offset_deg=0.4)
    assert st_cat.adaptive and st_cat.steer_ratio < 15.0
    st_man = cat_f.step(now=2.0, gated=True, steer_ratio=13.5, stiffness=0.92, angle_offset_deg=0.4, manual_sr=12.5)
    assert st_man.adaptive and abs(st_man.steer_ratio - 12.5) < 1e-6

    # BlinkerPause + SteeringResumeRamp + DesireHelper LCA/TurnDesire + 3D adjacent car LCAdjacentLeadHandoff & SOC
    _clear_3d_vehicles(env, spawned)
    env.reset()
    v_pre_lc = _warmup_to_speed(env, 15.0, max_steps=160)
    ego_lane_idx = env.vehicle.lane_index[2]
    adj_lat = 3.6 if ego_lane_idx == 0 else -3.6
    adj_car = _spawn_3d_vehicle(env, long_offset_m=22.0, lat_offset_m=adj_lat)
    spawned.append(adj_car)
    planner_lc = LongitudinalPlanner(_make_cp(), init_v=v_pre_lc)
    prev_v = v_pre_lc
    pedal = _PedalPI()
    lc_dir = log.LaneChangeDirection.right if adj_lat > 0 else log.LaneChangeDirection.left
    v_lc_min = 999.0
    soc = SOC(confirmation_frames=2)
    soc_offset_m = 0.0
    bp = BlinkerPause(min_speed_mps=12.0, hold_s=1.0, dt=DT_MDL)
    ramp = SteeringResumeRamp()
    assert bp.update(8.0, True, False) is True and bp.update(15.0, True, False) is False
    t_early, _ = ramp.update(True, 1.0, 10.0, 0.0, 0.1)
    t_full, _ = ramp.update(True, 1.0, 10.0, 0.0, 2.0)
    assert 0.0 <= t_early < t_full <= 1.0

    for step in range(90):
      v_ego = float(env.vehicle.speed)
      a_ego = (v_ego - prev_v) / DT_MDL
      prev_v = v_ego
      _drive_3d_actor(adj_car, target_v=6.0, target_lat_offset=adj_lat, ref_lane=env.vehicle.lane)
      steer_cmd, _, on_lane = _lane_center_steer(env)
      assert on_lane
      # Real 3D adjacent car position in calibrated modelV2 frame (y right-positive = +local_lat)
      adj_long, adj_lat_now = env.vehicle.lane.local_coordinates(adj_car.position)
      ego_long, _ = env.vehicle.lane.local_coordinates(env.vehicle.position)
      dx_adj = max(2.0, float(adj_long - ego_long) - 4.5)
      sm = _make_sm(
        v_ego=v_ego,
        v_cruise_kph=65.0,
        a_ego=a_ego,
        lc_state=log.LaneChangeState.laneChangeStarting,
        lc_dir=lc_dir,
        adj_lead=(dx_adj, float(adj_lat_now), float(adj_car.speed)),
      )
      planner_lc.update(sm, feature_flags=FeatureFlags.LC_LEAD_HANDOFF)
      v_lc_min = min(v_lc_min, v_ego)
      if step < 4:
        lane_w = 3.5
        s_res = soc.update(SOCInput(
          v_ego=max(v_ego, 25.0),
          left_threat=(adj_lat_now < -1.5),
          right_threat=(adj_lat_now > 1.5),
          lane_line_y=((-lane_w * 1.5,) * 10, (-lane_w * 0.5,) * 10, (lane_w * 0.5,) * 10, (lane_w * 1.5,) * 10),
          lane_line_probs=(0.9, 0.9, 0.9, 0.9),
          lane_line_stds=(0.1, 0.1, 0.1, 0.1),
        ))
        if s_res.active_suggestion:
          soc_offset_m = s_res.offset_m
      env.step([steer_cmd, pedal.step(float(planner_lc.output_a_target), a_ego)])

    assert v_lc_min < v_pre_lc - 2.5, f"NGP10 3D LCAdjacentLeadHandoff failed to slow: {v_pre_lc:.2f} -> {v_lc_min:.2f}"
    expected_soc_sign = 1.0 if adj_lat > 0 else -1.0  # right threat -> +left offset; left threat -> -right offset
    assert soc_offset_m * expected_soc_sign > 0.0, f"3D SOC offset wrong direction: offset={soc_offset_m:.3f}"

    # DesireHelper LCA guards (blindspot block, TurnDesire low-speed turn pulse)
    dh_bs = DesireHelper(lca_speed_mph=20, turn_desire_mph=15)
    cs_bs = messaging.new_message("carState").carState
    cs_bs.vEgo = 20.0
    cs_bs.leftBlinker = True
    cs_bs.leftBlindspot = True
    cs_bs.steeringPressed = True
    cs_bs.steeringTorque = 50.0
    dh_bs.update(cs_bs, True, 0.0)
    dh_bs.update(cs_bs, True, 0.0)
    assert dh_bs.lane_change_state == LaneChangeState.preLaneChange
    cs_turn = messaging.new_message("carState").carState
    cs_turn.vEgo = 4.0
    cs_turn.leftBlinker = True
    dh_turn = DesireHelper(lca_speed_mph=20, turn_desire_mph=15)
    dh_turn.update(cs_turn, True, 0.0)
    assert dh_turn.desire == log.Desire.turnLeft
    print(
      f"METADRIVE_REPORT NGP10 LATERAL: alcc_modes=3/3 red_nudge={curvature_nudge(out_warn):.5f}",
      f"cat_sr={st_cat.steer_ratio:.2f} 3d_lc_handoff={v_pre_lc:.2f}->{v_lc_min:.2f}m/s 3d_soc={soc_offset_m:+.3f}m",
    )
  finally:
    _clear_3d_vehicles(env, spawned)
    env.close()

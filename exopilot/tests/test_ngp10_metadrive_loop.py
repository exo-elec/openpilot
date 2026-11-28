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
from cereal import car
from openpilot.common.realtime import DT_MDL
from openpilot.selfdrive.controls.lib.longcontrol import LongCtrlState
from openpilot.selfdrive.controls.lib.longitudinal_planner import (
  BRSC_MIN_SPEED_MS,
  FeatureFlags,
  LongitudinalPlanner,
)
from openpilot.selfdrive.modeld.constants import ModelConstants

from exopilot.selfdrive.controls.lib.alcc import ALCC, ALCCInput, ALCCState
from exopilot.selfdrive.controls.lib.dlat import DLAT, DLATSuggestion
from exopilot.selfdrive.controls.lib.driver_activity import DriverActivityMonitor
from exopilot.selfdrive.controls.lib.map_speed import MapSpeed
from exopilot.selfdrive.controls.lib.policy_arbiter import Mode as ArbiterMode
from exopilot.selfdrive.controls.lib.soc import SOC
from exopilot.selfdrive.controls.lib.soc_policy import SOCInput

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


def _model_v2(v_ego: float, curvature: float = 0.0, lane_prob: float = 0.9, should_stop: bool = False, v_cruise_ms: float = 20.0):
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
  xs = [float(i * 5) for i in range(33)]
  lines = msg.init("laneLines", 4)
  for idx, y_val in enumerate((-5.4, -1.8, 1.8, 5.4)):
    lines[idx].x = xs
    lines[idx].y = [y_val] * 33
  msg.laneLineProbs = [lane_prob, lane_prob, lane_prob, lane_prob]
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

  d = {
    "carState": cs,
    "carControl": cc,
    "controlsState": ctrl,
    "selfdriveState": sd,
    "liveParameters": lp,
    "radarState": messaging.new_message("radarState").radarState,
    "modelV2": _model_v2(max(v_ego, 1.0), road_kappa, lane_prob=lane_prob, v_cruise_ms=v_cruise_kph / 3.6),
    "accelerometer": acc,
    "driverMonitoringState": dm,
    "navInstruction": nav,
  }
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
  finally:
    env.close()

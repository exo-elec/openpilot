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

import numpy as np
import pytest

import cereal.messaging as messaging
from cereal import car, log
from openpilot.common.realtime import DT_MDL
from openpilot.selfdrive.controls.lib.longcontrol import LongCtrlState
from openpilot.selfdrive.controls.lib.longitudinal_planner import (
  BRSC_MIN_SPEED_MS,
  LongitudinalPlanner,
)
from openpilot.selfdrive.modeld.constants import ModelConstants
from openpilot.system.socketd.vehicle.car.carstate import CarState
from openpilot.system.socketd.vehicle.tesla.values import CANBUS
from openpilot.tools.sim.lib.simulated_car import SimulatedCar

from exopilot.selfdrive.controls.lib.dlat import DLAT, DLATSuggestion
from exopilot.selfdrive.controls.lib.map_speed import MapSpeed
from exopilot.selfdrive.gridd.drivable_fusion import DRIVABLE, OTHER, DrivableFusion
from exopilot.selfdrive.gridd.occupancy import GridD, obstacle_type

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


def _model_v2(v_ego: float, curvature: float = 0.0, lane_prob: float = 0.9, v_cruise_ms: float = 20.0):
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
  lane_xs = [float(i * 5) for i in range(33)]
  lines = msg.init("laneLines", 4)
  for idx, y_val in enumerate((-5.4, -1.8, 1.8, 5.4)):
    lines[idx].x = lane_xs
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
  mapd_out=None,
  nav_limit_ms: float = 0.0,
  traffic_light=None,
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

  d = {
    "carState": cs,
    "carControl": cc,
    "controlsState": ctrl,
    "selfdriveState": sd,
    "liveParameters": lp,
    "liveLocationKalman": llk,
    "radarState": messaging.new_message("radarState").radarState,
    "modelV2": _model_v2(max(v_ego, 1.0), road_kappa, lane_prob=lane_prob, v_cruise_ms=v_cruise_kph / 3.6),
    "accelerometer": acc,
    "navInstruction": nav,
  }
  if mapd_out is not None:
    d["mapdOut"] = mapd_out
  if traffic_light is not None:
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
  sm.valid["enhancedTrajectory"] = False
  return sm


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
  finally:
    sc.close()
    env.close()

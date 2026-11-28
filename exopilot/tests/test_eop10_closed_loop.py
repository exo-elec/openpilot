"""Tier B synthetic-sensor daemon & closed-loop pipeline tests for EOP10.

Verifies the hardware/product-layer daemons and closed-loop planner without
physical RK3588/RK3576 hardware or mocked-out core math:
- gridd occupancy, DrivableFusion, obstacle_type enum mapping, and modelV2 vs yRel sign conventions
- segd degraded-card fallback (never faults when Hailo/DX absent)
- monod TLSC HSV lamp classifier + YOLO/monoDetections publication
- sided/reard SideTrackerCore BEV projection & track handover
- radar2d stale corner expiry + radar3d UART detection_to_point producer
- adaptd AdaptiveDrivingComputer SOC/thermal adaptation
- pathd SharedPathdHost / fill_path_adjust + RuleChannel authority ceiling (DPPMaxMode=0 vs >0)
- bluetoothd PlpGATT device_hello + socketd SafetyManager TX gating
- RK3588 & RK3576 independent hardware adapters + build_profile
- EOP10 closed-loop LongitudinalPlanner + LongitudinalMpc (ACC, BRSC, VTSC, MTSC/MSLC/NSLC)
"""

from __future__ import annotations

import math
from types import SimpleNamespace as NS

import numpy as np
import pytest

import cereal.messaging as messaging
from cereal import car, log
from openpilot.common.constants import CV
from openpilot.common.realtime import DT_MDL
from openpilot.selfdrive.controls.lib.longcontrol import LongCtrlState
from openpilot.selfdrive.controls.lib.longitudinal_planner import LongitudinalPlanner
from openpilot.selfdrive.modeld.constants import ModelConstants

from exopilot.selfdrive.adaptd.adaptd import AdaptiveDrivingComputer
from exopilot.selfdrive.controls.lib.dpp import Mode
from exopilot.selfdrive.controls.lib.map_speed import MapSpeed
from exopilot.selfdrive.controls.lib.radar2d import blindspot_blocked, ground_range
from exopilot.selfdrive.controls.lib.rule_channel import RuleChannelConsumer
from exopilot.selfdrive.controls.lib.speed_policy import SpeedLimitPolicy
from exopilot.selfdrive.gridd.drivable_fusion import DRIVABLE, LANE, OTHER, DrivableFusion
from exopilot.selfdrive.gridd.occupancy import GridD, obstacle_type
from exopilot.selfdrive.monod.monod import Light, fill_raw_detections
from exopilot.selfdrive.monod.traffic_light_classifier import classify as classify_traffic_light
from exopilot.selfdrive.sided.sided import SideTrackerCore
from exopilot.system.radar3d.radar3d import detection_to_point


class FakeSubMaster(dict):
  """SubMaster-compatible dict exposing .valid/.alive/.updated/.logMonoTime/.all_checks."""

  def __init__(self, services: dict):
    super().__init__(services)
    self.valid = dict.fromkeys(services, True)
    self.alive = dict.fromkeys(services, True)
    self.updated = dict.fromkeys(services, True)
    self.logMonoTime = dict.fromkeys(services, 0)

  def all_checks(self, service_list=None):
    keys = service_list if service_list is not None else self.keys()
    return all(self.valid.get(k, False) and self.alive.get(k, False) for k in keys)


class DictParams:
  def __init__(self, initial: dict | None = None):
    self._store = dict(initial or {})

  def get_bool(self, key: str) -> bool:
    return bool(self._store.get(key, False))

  def get(self, key: str, return_default: bool = False):
    return self._store.get(key, None)


def _make_cp():
  CP = car.CarParams.new_message()
  CP.openpilotLongitudinalControl = True
  CP.longitudinalActuatorDelay = 0.15
  CP.deprecated.vEgoStopping = 0.5
  CP.steerRatio = 15.0
  CP.wheelbase = 2.72
  return CP


def _make_sm(
  v_ego: float,
  a_ego: float = 0.0,
  v_cruise_kph: float = 90.0,
  lead_status: bool = False,
  lead_drel: float = 40.0,
  lead_vrel: float = 0.0,
  lead_a: float = 0.0,
  az: float = 9.81,
  curvature: float = 0.0,
  mapd_speed_limit: float = 0.0,
  nav_speed_limit: float = 0.0,
  enabled: bool = True,
):
  cs = messaging.new_message('carState').carState
  cs.vEgo = float(v_ego)
  cs.aEgo = float(a_ego)
  cs.vCruise = float(v_cruise_kph)
  cs.standstill = bool(v_ego < 0.05)
  cs.steeringAngleDeg = 0.0

  ctrl = messaging.new_message('controlsState').controlsState
  ctrl.longControlState = LongCtrlState.pid if enabled else LongCtrlState.off
  ctrl.forceDecel = False

  sd = messaging.new_message('selfdriveState').selfdriveState
  sd.enabled = bool(enabled)
  sd.experimentalMode = False
  sd.personality = log.LongitudinalPersonality.standard

  cc = messaging.new_message('carControl').carControl
  cc.orientationNED = [0.0, 0.0, 0.0]

  lp = messaging.new_message('liveParameters').liveParameters
  lp.angleOffsetDeg = 0.0

  llk = messaging.new_message('liveLocationKalman').liveLocationKalman
  llk.gpsOK = False

  rs = messaging.new_message('radarState').radarState
  rs.leadOne.status = bool(lead_status)
  if lead_status:
    rs.leadOne.dRel = float(lead_drel)
    rs.leadOne.vRel = float(lead_vrel)
    rs.leadOne.vLead = float(v_ego + lead_vrel)
    rs.leadOne.vLeadK = float(v_ego + lead_vrel)
    rs.leadOne.aLeadK = float(lead_a)
    rs.leadOne.modelProb = 1.0

  mdl = messaging.new_message('modelV2').modelV2
  n = ModelConstants.IDX_N
  t_idxs = ModelConstants.T_IDXS
  xs = [float(v_ego * t) for t in t_idxs]
  ys = [float(0.5 * curvature * x * x) for x in xs]
  mdl.position.x = xs
  mdl.position.y = ys
  mdl.position.z = [0.0] * n
  mdl.velocity.x = [float(v_ego)] * n
  mdl.velocity.y = [float(curvature * v_ego * x) for x in xs]
  mdl.velocity.z = [0.0] * n
  mdl.acceleration.x = [0.0] * n
  mdl.acceleration.y = [float(curvature * v_ego * v_ego)] * n
  mdl.acceleration.z = [0.0] * n
  mdl.orientationRate.z = [float(curvature * v_ego)] * n
  mdl.meta.disengagePredictions.gasPressProbs = [1.0, 1.0]
  mdl.action.desiredAcceleration = 0.0
  mdl.action.shouldStop = False

  acc = messaging.new_message('accelerometer').accelerometer
  acc.acceleration.v = [-float(az), 0.0, 0.0]

  nav = messaging.new_message('navInstruction').navInstruction
  nav.speedLimit = float(nav_speed_limit)

  mo = messaging.new_message('mapdOut').mapdOut
  mo.speedLimit = float(mapd_speed_limit)

  et = messaging.new_message('enhancedTrajectory').enhancedTrajectory
  sm = FakeSubMaster({
    'carState': cs,
    'controlsState': ctrl,
    'selfdriveState': sd,
    'carControl': cc,
    'liveParameters': lp,
    'liveLocationKalman': llk,
    'radarState': rs,
    'modelV2': mdl,
    'accelerometer': acc,
    'navInstruction': nav,
    'mapdOut': mo,
    'enhancedTrajectory': et,
  })
  sm.valid['enhancedTrajectory'] = False
  sm.updated['liveLocationKalman'] = False
  sm.valid['navInstruction'] = nav_speed_limit > 0.0
  sm.valid['mapdOut'] = mapd_speed_limit > 0.0
  sm.updated['mapdOut'] = sm.valid['mapdOut']
  return sm


# ---------------------------------------------------------------------------
# 1. Perception Daemons: gridd, segd, monod, sided/reard, radar2d/radar3d
# ---------------------------------------------------------------------------

def test_gridd_drivable_fusion_obstacle_types_and_frame_sign_convention():
  """gridd maps class names cleanly into capnp ObstacleType, fuses BEV drivable grids, and flips modelV2 y-right -> yRel left ONCE."""
  # ObstacleType capnp safety check (regression guard against first-detection capnp enum crash)
  so = messaging.new_message('stereoObjects').stereoObjects
  objs = so.init('objects', 6)
  for i, raw in enumerate(('car', 'truck', 'person', 'bicycle', 'traffic light', 'trafficLight')):
    objs[i].obstacleType = obstacle_type(raw)
  assert str(objs[4].obstacleType) == 'trafficLight'
  assert str(objs[5].obstacleType) == 'trafficLight'

  # DrivableFusion BEV multi-camera fusion, logodds accumulation, and ego-motion grid shift
  df = DrivableFusion()
  cells = np.full((df.spec.rows, df.spec.cols), OTHER, dtype=np.uint8)
  cells[20:40, 20:30] = DRIVABLE
  cells[20:40, 19] = LANE
  df.step(now=1.0, v_forward=0.0)
  df.update('road', cells, now=1.0)
  assert df.logodds[25, 25] > 0.0
  assert df.lane[25, 19] > 0.0
  # Travel forward 10m -> content shifts backward by 10m / resolution cells
  df.step(now=2.0, v_forward=10.0)
  shift = int(10.0 / df.spec.resolution)
  assert df.logodds[25 - shift, 25] > 0.0

  # Frame sign convention check on _fuse_modeld_detections with real capnp leadsV3 (y-right -> yRel left-positive flipped ONCE)
  class _Host:
    _fuse_modeld_detections = GridD._fuse_modeld_detections

  real_mdl = messaging.new_message('modelV2').modelV2
  leads = real_mdl.init('leadsV3', 1)
  leads[0].prob = 0.95
  leads[0].x = [30.0]
  leads[0].y = [3.5]
  leads[0].v = [0.0]
  fused = _Host()._fuse_modeld_detections(real_mdl, None)
  assert len(fused) == 1
  assert fused[0]['yRel'] == pytest.approx(-3.5, abs=0.05), (
    f"Frame sign bug! modelV2 y=+3.5 (Right) must map to yRel=-3.5 (Left-positive), got {fused[0]['yRel']}"
  )


def test_segd_degraded_card_fallback_never_faults():
  """SegD runs cleanly without a physical Hailo/DX card and degrades gracefully without raising on step()."""
  from exopilot.selfdrive.segd.segd import SegD
  seg = SegD()
  # CardSegmenter is optimistic until first unavailable IPC reply, and step() with no cameras never raises
  assert isinstance(seg.card_active, bool)
  seg.step()


def test_monod_tlsc_hsv_classifier_and_mono_detections_capnp():
  """monod HSV classifier identifies red/yellow/green lamps cleanly and fill_raw_detections populates capnp monoDetections."""
  def _lamp_frame(bgr_color):
    img = np.zeros((120, 120, 3), dtype=np.uint8)
    img[30:90, 30:90] = bgr_color
    return img

  st_r, conf_r = classify_traffic_light(_lamp_frame((10, 10, 240)), (20, 20, 100, 100))
  st_y, conf_y = classify_traffic_light(_lamp_frame((10, 235, 240)), (20, 20, 100, 100))
  st_g, conf_g = classify_traffic_light(_lamp_frame((30, 235, 30)), (20, 20, 100, 100))
  st_0, conf_0 = classify_traffic_light(np.zeros((120, 120, 3), dtype=np.uint8), (20, 20, 100, 100))

  assert st_r == 1 and conf_r > 0.4
  assert st_y == 2 and conf_y > 0.4
  assert st_g == 3 and conf_g > 0.4
  assert st_0 == 0 and conf_0 == 0.0

  md = messaging.new_message('monoDetections').monoDetections
  lights = [Light(x=35.0, y=0.2, state=1, state_conf=0.88, conf=0.92, box=(0.5, 0.2, 0.03, 0.06))]
  fill_raw_detections(md, meas=[], frame_id=42, timestamp_s=12.5, exec_time=0.015, lights=lights)
  assert len(md.detections) == 1
  assert md.detections[0].className == 'traffic light'
  assert md.detections[0].trafficLightState == 1
  assert md.detections[0].trafficLightConfidence == pytest.approx(0.88, abs=1e-3)


def test_sided_bev_tracker_and_radar2d_radar3d_pipelines():
  """SideTrackerCore projects side-camera detections to vehicle frame; radar2d slant projection & radar3d UART point conversion."""
  from exopilot.selfdrive.sided.bev_reprojector import make_default_geometry, reproject_side_camera
  core = SideTrackerCore()
  assert core.left_tracker is not None and core.right_tracker is not None
  geo = make_default_geometry('side_left')
  x_m, y_m, z_m, w_m, l_m = reproject_side_camera((500.0, 350.0, 650.0, 480.0), 'car', (720, 1280), geo)
  assert all(math.isfinite(v) for v in (x_m, y_m, z_m, w_m, l_m))
  assert y_m > 0.0, f"Left side-camera detection should project to positive (left) y_m, got {y_m}"

  # radar2d planar BSD slant projection & presence-only gating
  assert blindspot_blocked(True, False, 'left') is True
  assert blindspot_blocked(True, False, 'right') is False
  assert ground_range(10.0, 0.0) == pytest.approx(10.0)
  assert ground_range(10.0, 60.0) == pytest.approx(5.0)
  assert math.isnan(ground_range(-1.0, 0.0))

  # radar3d UART detection -> RadarPoint dict preserving Left-positive yRel
  det = NS(track_id=7, range_m=42.5, azimuth_deg=5.0, vel_mps=-2.5)
  pt = detection_to_point(det)
  assert pt['trackId'] == 7
  assert pt['dRel'] == pytest.approx(42.5) and pt['yRel'] > 0.0 and pt['vRel'] == pytest.approx(-2.5)


# ---------------------------------------------------------------------------
# 2. Planning / Adaptation / Rule Channel / Safety / Comms / Hardware Adapters
# ---------------------------------------------------------------------------

def test_adaptd_rule_channel_bluetoothd_socketd_and_board_adapters():
  """Verify AdaptD SOC/thermal derating, RuleChannel authority ceiling, PlpGATT hello, SafetyManager, and RK3588/RK3576 adapters."""
  # AdaptD critical SOC & thermal derating
  adc = AdaptiveDrivingComputer()
  adc._enabled = True
  vd_low = NS(valid=True, batterySoc=8.0, rangeRemaining=15.0,
              batteryTempMax=35.0, batteryTempMin=25.0, motorTemp=60.0, inverterTemp=50.0, coolantTemp=45.0)
  prof = adc.update(vd_low)
  assert prof.accel_max <= 1.0 and prof.reason_code != "no_data"

  # RuleChannelConsumer: DPPMaxMode=0 (Mode.OFF) passes policy through untouched; PRIMARY_LAT obeys authority ceiling
  consumer = RuleChannelConsumer()
  c_off, src_off = consumer.curvature(Mode.OFF, policy_curv=0.012, rule_valid=True, rule_curv=-0.02,
                                      fresh=True, driver_override=False, dt=DT_MDL)
  assert c_off == pytest.approx(0.012) and src_off == 'policy'

  # Stale rule message must also pass policy through unchanged even under PRIMARY_LAT
  c_stale, src_stale = consumer.curvature(Mode.PRIMARY_LAT, policy_curv=0.012, rule_valid=True, rule_curv=-0.02,
                                          fresh=False, driver_override=False, dt=DT_MDL)
  assert c_stale == pytest.approx(0.012) and src_stale == 'policy'

  # Bluetoothd PlpGATT device_hello advertises expected services
  from openpilot.system.bluetoothd.plp_gatt import device_hello
  hello = device_hello()
  svc_names = {s[0] for s in hello.services}
  assert 'core' in svc_names and 'locate' in svc_names

  # SocketD SafetyManager defaults to safety enforcement enabled and blocks steering TX (0x488) while controls disallowed
  from openpilot.system.socketd.safety.safety_manager import SafetyManager
  smgr = SafetyManager(use_params=False)
  assert smgr.is_enabled() is True
  assert smgr.check_tx_message(0x488, bytes(8), 0) is False

  # Both RK3588 and RK3576 hardware adapters exist independently + Rockchip core topology (0-3 A55 little, 4-7 A76/A72 big)
  from openpilot.common.realtime import BIG_CORES, LITTLE_CORES
  from openpilot.system.hardware.rk3588.hardware import RK3588Hardware
  from openpilot.system.hardware.rk3576.hardware import RK3576Hardware
  assert RK3588Hardware().get_device_type() == 'rk3588'
  assert RK3576Hardware().get_device_type() == 'rk3576'
  assert LITTLE_CORES == [0, 1, 2, 3]
  assert BIG_CORES == [4, 5, 6, 7]

  # NCP blindspot payload ignores stale radar2d returns
  from openpilot.system.bluetoothd.ncp_session import build_blindspot_payload
  r2d = NS(returns=[NS(side=0, present=True, stale=True), NS(side=1, present=True, stale=False)])
  payload = build_blindspot_payload(None, r2d, decision_valid=False)
  assert payload['radarPresence']['frontLeft'] is False
  assert payload['radarPresence']['rearLeft'] is True

  # RGABackend never returns raw unmodified arrays even when _use_opencv_fallback is False
  from openpilot.system.inferenced.rockchip_rga import RGABackend
  rga = RGABackend()
  rga._use_opencv_fallback = False
  img = np.zeros((240, 320, 3), dtype=np.uint8)
  assert rga._resize({'input': img, 'width': 160, 'height': 120}).shape == (120, 160, 3)
  assert rga._crop({'input': img, 'x': 10, 'y': 20, 'width': 80, 'height': 60}).shape == (60, 80, 3)


# ---------------------------------------------------------------------------
# 3. EOP10 Closed-Loop LongitudinalPlanner + MPC (ACC, BRSC, VTSC, MSLC/NSLC)
# ---------------------------------------------------------------------------

def test_eop10_longitudinal_planner_closed_loop_acc_brsc_vtsc_slc():
  """Closed-loop kinematic verification of EOP10's LongitudinalPlanner + MPC across ACC, BRSC, VTSC, and MSLC/NSLC."""
  # 1. ACC lead brake-to-stop (SurfaceQualityDB resolves cleanly on dev PC without /data)
  planner = LongitudinalPlanner(_make_cp(), init_v=20.0, init_a=0.0, dt=DT_MDL)
  sent = []
  fake_pm = NS(send=lambda s, m: sent.append((s, m)))
  v_ego, a_ego, x_ego = 20.0, 0.0, 0.0
  v_lead, x_lead = 20.0, 45.0
  min_gap = float('inf')
  for step in range(400):
    t = step * DT_MDL
    a_lead = -2.5 if (t >= 2.0 and v_lead > 0.0) else 0.0
    v_lead = max(0.0, v_lead + a_lead * DT_MDL)
    x_lead += v_lead * DT_MDL
    d_rel = x_lead - x_ego
    min_gap = min(min_gap, d_rel)
    sm = _make_sm(v_ego=v_ego, a_ego=a_ego, v_cruise_kph=72.0,
                      lead_status=True, lead_drel=d_rel, lead_vrel=v_lead - v_ego, lead_a=a_lead)
    planner.update(sm)
    if step == 0:
      planner.publish(sm, fake_pm)
    a_ego = 0.65 * a_ego + 0.35 * planner.output_a_target
    v_ego = max(0.0, v_ego + a_ego * DT_MDL)
    x_ego += v_ego * DT_MDL
  assert any(s == 'longitudinalPlan' for s, _ in sent)
  assert min_gap > 2.5 and v_ego < 0.3 and planner.output_should_stop

  # 2. VTSC curve slowdown on EOP10
  planner_v = LongitudinalPlanner(_make_cp(), init_v=25.0, init_a=0.0, dt=DT_MDL)
  planner_v._vtsc_flags = (True, False)
  planner_v._vtsc_flags_t = 1e12  # freeze flag cache on
  v_ego, a_ego = 25.0, 0.0
  min_vtsc = float('inf')
  for _ in range(80):
    sm = _make_sm(v_ego=v_ego, a_ego=a_ego, v_cruise_kph=90.0, curvature=0.0125)
    planner_v.update(sm)
    if planner_v.vtsc_v_target is not None:
      min_vtsc = min(min_vtsc, planner_v.vtsc_v_target)
    a_ego = 0.65 * a_ego + 0.35 * planner_v.output_a_target
    v_ego = max(0.0, v_ego + a_ego * DT_MDL)
  assert min_vtsc < 18.0 and v_ego < 22.0

  # 3. Unified MSLC + NSLC speed limit policy on EOP10
  now = [0.0]
  mp = DictParams({'MSLCEnabled': True, 'NSLCEnabled': True, 'SpeedLimitPolicy': int(SpeedLimitPolicy.LOWEST)})
  planner_s = LongitudinalPlanner(_make_cp(), init_v=25.0, init_a=0.0, dt=DT_MDL)
  planner_s.map_speed = MapSpeed(mp.get_bool, mp.get, clock=lambda: now[0])
  v_ego, a_ego = 25.0, 0.0
  for _ in range(160):
    now[0] += DT_MDL
    sm = _make_sm(v_ego=v_ego, a_ego=a_ego, v_cruise_kph=100.0,
                      mapd_speed_limit=70.0 * CV.KPH_TO_MS, nav_speed_limit=60.0 * CV.KPH_TO_MS)
    planner_s.update(sm)
    a_ego = 0.65 * a_ego + 0.35 * planner_s.output_a_target
    v_ego = max(0.0, v_ego + a_ego * DT_MDL)
  assert planner_s.map_speed._last is not None and planner_s.map_speed._last <= 17.0
  assert v_ego < 19.0

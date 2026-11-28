"""Tier B closed-loop scenario & loopback tests for the NGP10 portable ExoPilot layer.

Exercises real LongitudinalPlanner + LongitudinalMpc (no mocked MPC), MapSpeed
(MTSC/VTSC handover, MSLC/NSLC unified SpeedLimitPolicy, TLSC, DDSC, RCSC),
DLON, BRSC, TJA, DriveModeApplier, lateral policies (ALCC, DLAT+DLP, CAT, RED,
SOC, Arbiter), BigModel TCP loopback + one-way fallback, and PLP v5 TCP/BLE/Locate
loopback. Designed on NGP10 so it flows cleanly to EOP10, 01M, and 02M.
"""

from __future__ import annotations

import json
import logging
import pathlib
import socket
import threading
import time
from types import SimpleNamespace as NS

import numpy as np
import pytest

import cereal.messaging as messaging
from cereal import car, log
from openpilot.common.constants import CV
from openpilot.common.realtime import DT_MDL
from openpilot.selfdrive.controls.lib.longcontrol import LongCtrlState
from openpilot.selfdrive.controls.lib.longitudinal_planner import (
  BRSC_MIN_SPEED_MS,
  FeatureFlags,
  LongitudinalPlanner,
)
from openpilot.selfdrive.modeld.constants import ModelConstants

from exopilot.common.speed_zones import HIGHWAY_SPEED_MPS
from exopilot.selfdrive.controls.lib.alcc import ALCC, ALCCInput, ALCCState, BrakeMode
from exopilot.selfdrive.controls.lib.arbiter import MAX_OFFSET_M, Proposal, arbitrate
from exopilot.selfdrive.controls.lib.cat import SteerRatioFilter
from exopilot.selfdrive.controls.lib.curve_speed import blend_mtsc_vtsc
from exopilot.selfdrive.controls.lib.dlat import DLAT, DLATSuggestion
from exopilot.selfdrive.controls.lib.dlon import DLON
from exopilot.selfdrive.controls.lib.drive_mode import AGGRESSIVE, RELAXED, STANDARD, DriveModeApplier
from exopilot.selfdrive.controls.lib.map_speed import MapSpeed
from exopilot.selfdrive.controls.lib.red import RED, REDState, curvature_nudge
from exopilot.selfdrive.controls.lib.soc import SOC, SOCInput, curvature_bias
from exopilot.selfdrive.controls.lib.speed_policy import SpeedLimitPolicy
from exopilot.selfdrive.modeld.bigmodel import protocol as BM_P
from exopilot.selfdrive.modeld.bigmodel import server_ref as BM_S
from exopilot.selfdrive.modeld.bigmodel.client import Client as BigModelClient
from exopilot.selfdrive.modeld.bigmodel.joining import Joining
from exopilot.selfdrive.modeld.bigmodel.listener import Listener
from exopilot.selfdrive.modeld.bigmodel.remote_state import RemoteModelState
from exopilot.system.plp import ble_stream as PLP_BLE
from exopilot.system.plp import frame as PLP_F
from exopilot.system.plp import registry as PLP_R
from exopilot.system.plp.cable import CablePLP
from exopilot.system.plp.channel import Message as PlpMessage
from exopilot.system.plp.hello import Hello as PlpHello
from exopilot.system.plp.locate import LocateSink
from exopilot.system.plp.session import PlpSession


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
  """In-memory Params double for isolated closed-loop planner scenarios."""

  def __init__(self, initial: dict | None = None):
    self._store = dict(initial or {})

  def get_bool(self, key: str) -> bool:
    return bool(self._store.get(key, False))

  def get(self, key: str, return_default: bool = False):
    return self._store.get(key, None)

  def put(self, key: str, value) -> None:
    self._store[key] = value

  def put_bool(self, key: str, value: bool) -> None:
    self._store[key] = bool(value)


def _make_cp():
  CP = car.CarParams.new_message()
  CP.openpilotLongitudinalControl = True
  CP.longitudinalActuatorDelay = 0.15
  CP.vEgoStopping = 0.5
  CP.steerRatio = 15.0
  CP.wheelbase = 2.72
  return CP


def _make_curved_model(v_ego: float, curvature: float = 0.0, should_stop: bool = False,
                       desired_accel: float = 0.0, gas_prob: float = 1.0,
                       lane_probs=(0.95, 0.95, 0.95, 0.95),
                       road_edges_y=(-4.0, 4.0), road_edge_stds=(0.2, 0.2),
                       yaw_rate_override: float | None = None):
  """Build a physically consistent capnp modelV2 message (y is right-positive)."""
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
  mdl.acceleration.x = [float(desired_accel)] * n
  mdl.acceleration.y = [float(curvature * v_ego * v_ego)] * n
  mdl.acceleration.z = [0.0] * n
  yr = float(curvature * v_ego) if yaw_rate_override is None else float(yaw_rate_override)
  mdl.orientationRate.z = [yr] * n
  mdl.meta.disengagePredictions.gasPressProbs = [float(gas_prob), float(gas_prob)]
  mdl.action.desiredAcceleration = float(desired_accel)
  mdl.action.shouldStop = bool(should_stop)
  mdl.action.desiredCurvature = float(curvature)

  mdl.laneLineProbs = [float(p) for p in lane_probs]
  mdl.laneLineStds = [0.1, 0.1, 0.1, 0.1]
  ll = mdl.init('laneLines', 4)
  for idx, y0 in enumerate((-5.4, -1.8, 1.8, 5.4)):
    ll[idx].x = xs
    ll[idx].y = [float(y0) for _ in xs]
    ll[idx].z = [0.0] * n

  re = mdl.init('roadEdges', 2)
  for idx, y0 in enumerate(road_edges_y):
    re[idx].x = xs
    re[idx].y = [float(y0) for _ in xs]
    re[idx].z = [0.0] * n
  mdl.roadEdgeStds = [float(s) for s in road_edge_stds]
  return mdl


def _make_sm(
  v_ego: float,
  a_ego: float = 0.0,
  v_cruise_kph: float = 90.0,
  lead_status: bool = False,
  lead_drel: float = 40.0,
  lead_vrel: float = 0.0,
  lead_a: float = 0.0,
  lead_track_id: int = 1,
  az: float = 9.81,
  curvature: float = 0.0,
  should_stop: bool = False,
  desired_accel: float = 0.0,
  gas_pressed: bool = False,
  dlat_laneless: bool = False,
  dlat_lane_conf: float = 1.0,
  awareness: float = 1.0,
  nav_speed_limit: float = 0.0,
  nav_maneuver_dist: float = 5000.0,
  mapd_speed_limit: float = 0.0,
  mapd_next_limit: float = 0.0,
  mapd_next_dist: float = 0.0,
  mapd_path: list[tuple[float, float, float]] | None = None,
  traffic_light: tuple[str, float, float] | None = None,
  surface_score: float | None = None,
  enabled: bool = True,
):
  cs = messaging.new_message('carState').carState
  cs.vEgo = float(v_ego)
  cs.aEgo = float(a_ego)
  cs.vCruise = float(v_cruise_kph)
  cs.standstill = bool(v_ego < 0.05)
  cs.gasPressed = bool(gas_pressed)
  cs.steeringAngleDeg = 0.0

  ctrl = messaging.new_message('controlsState').controlsState
  ctrl.longControlState = LongCtrlState.pid if enabled else LongCtrlState.off
  ctrl.forceDecel = False
  ctrl.dlatUseLaneless = bool(dlat_laneless)
  ctrl.dlatLaneConfidence = float(dlat_lane_conf)

  sd = messaging.new_message('selfdriveState').selfdriveState
  sd.enabled = bool(enabled)
  sd.experimentalMode = False
  sd.personality = log.LongitudinalPersonality.standard

  cc = messaging.new_message('carControl').carControl
  cc.orientationNED = [0.0, 0.0, 0.0]

  lp = messaging.new_message('liveParameters').liveParameters
  lp.angleOffsetDeg = 0.0

  rs = messaging.new_message('radarState').radarState
  rs.leadOne.status = bool(lead_status)
  if lead_status:
    rs.leadOne.dRel = float(lead_drel)
    rs.leadOne.vRel = float(lead_vrel)
    rs.leadOne.vLead = float(v_ego + lead_vrel)
    rs.leadOne.vLeadK = float(v_ego + lead_vrel)
    rs.leadOne.aLeadK = float(lead_a)
    rs.leadOne.radarTrackId = int(lead_track_id)
    rs.leadOne.modelProb = 1.0

  mdl = _make_curved_model(v_ego, curvature=curvature, should_stop=should_stop, desired_accel=desired_accel)

  acc = messaging.new_message('accelerometer').accelerometer
  # locationd.py maps raw IMU [v0, v1, v2] -> meas = [-v2, -v1, -v0] (vertical is -v[0]).
  acc.acceleration.v = [float(-az), 0.0, 0.0]

  nav = messaging.new_message('navInstruction').navInstruction
  nav.speedLimit = float(nav_speed_limit)
  nav.maneuverDistance = float(nav_maneuver_dist)

  mo = messaging.new_message('mapdOut').mapdOut
  mo.speedLimit = float(mapd_speed_limit)
  mo.nextSpeedLimit = float(mapd_next_limit)
  mo.nextSpeedLimitDistance = float(mapd_next_dist)

  mext = messaging.new_message('mapdExtendedOut').mapdExtendedOut
  mext.position.latitude = 13.7500
  mext.position.longitude = 100.5000
  if mapd_path:
    pts = mext.init('path', len(mapd_path))
    for i, (lat, lon, curv) in enumerate(mapd_path):
      pts[i].latitude = float(lat)
      pts[i].longitude = float(lon)
      pts[i].curvature = float(curv)

  so = messaging.new_message('stereoObjects').stereoObjects
  if traffic_light is not None:
    tl_state, tl_conf, tl_dist = traffic_light
    objs = so.init('objects', 1)
    objs[0].obstacleType = 'trafficLight'
    objs[0].dRel = float(tl_dist)
    objs[0].yRel = 0.0
    objs[0].prob = float(tl_conf)
    objs[0].trafficLightState = tl_state
    objs[0].trafficLightConfidence = float(tl_conf)

  dm = messaging.new_message('driverMonitoringState').driverMonitoringState
  dm.awarenessStatus = float(awareness)

  services = {
    'carState': cs,
    'controlsState': ctrl,
    'selfdriveState': sd,
    'carControl': cc,
    'liveParameters': lp,
    'radarState': rs,
    'modelV2': mdl,
    'accelerometer': acc,
    'navInstruction': nav,
    'mapdOut': mo,
    'mapdExtendedOut': mext,
    'stereoObjects': so,
    'driverMonitoringState': dm,
  }
  if surface_score is not None:
    services['surfaceStatus'] = NS(
      hasSurfaceQuality=True,
      surfaceQuality=NS(score=float(surface_score), texture='wet_asphalt'),
    )

  sm = FakeSubMaster(services)
  sm.valid['navInstruction'] = nav_speed_limit > 0.0 or nav_maneuver_dist < 4000.0
  sm.valid['mapdOut'] = mapd_speed_limit > 0.0 or mapd_next_limit > 0.0
  sm.updated['mapdOut'] = sm.valid['mapdOut']
  sm.valid['mapdExtendedOut'] = bool(mapd_path)
  sm.updated['mapdExtendedOut'] = bool(mapd_path)
  sm.valid['stereoObjects'] = traffic_light is not None
  return sm


# ---------------------------------------------------------------------------
# 1. Longitudinal Closed-Loop Scenarios (Real LongitudinalPlanner + MPC)
# ---------------------------------------------------------------------------

def test_acc_closed_loop_lead_follow_and_brake_to_stop():
  """Closed-loop kinematic plant + real NGP10 LongitudinalPlanner/MPC braking behind stopping lead."""
  planner = LongitudinalPlanner(_make_cp(), init_v=20.0, init_a=0.0, dt=DT_MDL)
  v_ego, a_ego, x_ego = 20.0, 0.0, 0.0
  v_lead, x_lead = 20.0, 45.0
  min_gap = float('inf')

  for step in range(400):
    t = step * DT_MDL
    if t >= 2.0 and v_lead > 0.0:
      a_lead = -2.5
      v_lead = max(0.0, v_lead + a_lead * DT_MDL)
    else:
      a_lead = 0.0
    x_lead += v_lead * DT_MDL
    d_rel = x_lead - x_ego
    min_gap = min(min_gap, d_rel)

    sm = _make_sm(v_ego=v_ego, a_ego=a_ego, v_cruise_kph=72.0,
                  lead_status=True, lead_drel=d_rel, lead_vrel=v_lead - v_ego, lead_a=a_lead)
    planner.update(sm)
    a_ego = 0.65 * a_ego + 0.35 * planner.output_a_target
    v_ego = max(0.0, v_ego + a_ego * DT_MDL)
    x_ego += v_ego * DT_MDL

  assert min_gap > 2.5, f"Unsafe min_gap={min_gap:.2f}m"
  assert v_ego < 0.3, f"Did not come to full stop: v_ego={v_ego:.2f}m/s"
  assert planner.output_should_stop


def test_dlon_highway_urban_dynamic_and_force_stop(monkeypatch):
  """DLON supports Highway (0), Urban (1), and Dynamic AUTO (2) modes, stop-prediction force_stop, and gas override."""
  import exopilot.selfdrive.controls.lib.dlon as dlon_mod
  sim_t = [100.0]
  monkeypatch.setattr(dlon_mod.time, 'monotonic', lambda: sim_t[0])

  dp = DictParams({
    'DlonMode': 2,  # Dynamic AUTO (INT param)
    'DLONCurvesEnabled': True,
    'DLONLaneConfidenceEnabled': True,
    'DLONSlowLeadEnabled': True,
    'DLONLowSpeedEnabled': True,
    'DLONStopPredictionEnabled': True,
    'DLONNavigationEnabled': True,
    'DLONSignalEnabled': True,
    'DLONSpeedLimitEnabled': True,
    'DLONForceStopsEnabled': True,
  })
  planner = LongitudinalPlanner(_make_cp(), init_v=25.0, init_a=0.0, dt=DT_MDL)
  planner.dlon = DLON(get_bool=dp.get_bool, get=dp.get)

  # 1. Dynamic AUTO at highway speed with confident lanes -> ACC mode
  for _ in range(20):
    sim_t[0] += DT_MDL
    sm = _make_sm(v_ego=25.0, v_cruise_kph=90.0, dlat_laneless=False, dlat_lane_conf=0.95)
    planner.update(sm)
  assert planner.dlon_result['e2e_enabled'] is False

  # 2. Dynamic AUTO urban low-speed + model shouldStop -> E2E (>0.5s enter debounce) + force_stop (>1.0s)
  force_stopped = False
  for _ in range(60):  # 3.0s simulated time
    sim_t[0] += DT_MDL
    sm = _make_sm(v_ego=8.0, v_cruise_kph=50.0, should_stop=True, desired_accel=-1.5, dlat_laneless=True)
    planner.update(sm)
    if planner.dlon_result.get('force_stop', False):
      force_stopped = True
  assert planner.dlon_result['e2e_enabled'] is True
  assert force_stopped, f"Expected DLON force_stop on urban stop prediction: {planner.dlon_result}"

  # 3. Gas override clears force_stop
  sim_t[0] += DT_MDL
  sm_gas = _make_sm(v_ego=8.0, v_cruise_kph=50.0, should_stop=True, desired_accel=-1.5, gas_pressed=True)
  planner.update(sm_gas)
  assert planner.dlon_result.get('force_stop', False) is False

  # 4. Highway mode (0) forces rule-based ACC even in urban stop prediction
  dp.put('DlonMode', 0)
  for _ in range(80):  # allow param poll (1s) + exit debounce + mode manager transition
    sim_t[0] += DT_MDL
    sm_hwy = _make_sm(v_ego=8.0, v_cruise_kph=50.0, should_stop=True, desired_accel=-1.5, dlat_laneless=True)
    planner.update(sm_hwy)
  assert planner.dlon_result['e2e_enabled'] is False

  # 5. Urban mode (1) forces E2E even on clear highway
  dp.put('DlonMode', 1)
  for _ in range(80):
    sim_t[0] += DT_MDL
    sm_urb = _make_sm(v_ego=25.0, v_cruise_kph=90.0, dlat_laneless=False, dlat_lane_conf=0.98)
    planner.update(sm_urb)
  assert planner.dlon_result['e2e_enabled'] is True


def test_vtsc_closed_loop_curve_slowdown_and_straight_recovery():
  """VTSC slows ego into a tight vision curve and releases once road straightens."""
  planner = LongitudinalPlanner(_make_cp(), init_v=25.0, init_a=0.0, dt=DT_MDL)
  v_ego, a_ego = 25.0, 0.0

  for _ in range(15):
    sm = _make_sm(v_ego=v_ego, a_ego=a_ego, v_cruise_kph=90.0, curvature=0.0)
    planner.update(sm, feature_flags=FeatureFlags.VTSC)
  assert planner.vtsc_v_target is None

  min_vtsc = float('inf')
  for _ in range(80):
    sm = _make_sm(v_ego=v_ego, a_ego=a_ego, v_cruise_kph=90.0, curvature=0.0125)
    planner.update(sm, feature_flags=FeatureFlags.VTSC)
    if planner.vtsc_v_target is not None:
      min_vtsc = min(min_vtsc, planner.vtsc_v_target)
    a_ego = 0.65 * a_ego + 0.35 * planner.output_a_target
    v_ego = max(0.0, v_ego + a_ego * DT_MDL)

  assert min_vtsc < 18.0, f"VTSC failed to target curve speed: min_vtsc={min_vtsc}"
  assert v_ego < 22.0, f"Ego did not slow for vision curve: v_ego={v_ego:.2f}"

  for _ in range(60):
    sm = _make_sm(v_ego=v_ego, a_ego=a_ego, v_cruise_kph=90.0, curvature=0.0)
    planner.update(sm, feature_flags=FeatureFlags.VTSC)
  assert planner.vtsc_v_target is None


def test_mtsc_and_mtsc_to_vtsc_handover():
  """MapSpeed MTSC slows for OSM curve in its 150-500m lookahead window and blends into VTSC."""
  now = [0.0]
  params = DictParams({'MTSCEnabled': True})
  planner = LongitudinalPlanner(_make_cp(), init_v=25.0, init_a=0.0, dt=DT_MDL)
  planner.map_speed = MapSpeed(params.get_bool, params.get, clock=lambda: now[0])

  # Build an OSM path spanning 0..350m ahead (1 deg lat ~ 111,111m -> 0.0002 deg ~ 22.2m),
  # with a tight curve (k=0.015 1/m, v_safe ~ 11 m/s) at 200-310m (inside MTSC 150-500m range).
  path = [(13.7500 + i * 0.0002, 100.5000, 0.015 if i >= 9 else 0.0) for i in range(16)]
  v_ego, a_ego = 25.0, 0.0
  caps = []
  for _ in range(100):
    now[0] += DT_MDL
    sm = _make_sm(v_ego=v_ego, a_ego=a_ego, v_cruise_kph=90.0, mapd_path=path)
    planner.update(sm)
    caps.append(planner.map_speed._last)
    a_ego = 0.65 * a_ego + 0.35 * planner.output_a_target
    v_ego = max(0.0, v_ego + a_ego * DT_MDL)

  valid_caps = [c for c in caps if c is not None]
  assert valid_caps and min(valid_caps) < 16.0, f"MTSC did not cap curve speed: {valid_caps[:5]}"
  assert v_ego < 21.5, f"Ego did not slow for MTSC map curve: v_ego={v_ego:.2f}"

  # Explicit handover verification: MTSC (14 m/s) -> VTSC (16 m/s) smoothly rate-limited across transition zone
  state = None
  v_cur = 14.0
  blended = []
  for dist in (210.0, 190.0, 175.0, 160.0, 140.0):
    c, state = blend_mtsc_vtsc(14.0, 16.0, dist, v_cur, state, DT_MDL)
    assert c is not None
    assert abs(c - v_cur) <= 2.0, f"Step jump during MTSC->VTSC handover: {v_cur} -> {c}"
    v_cur = c
    blended.append(c)
  assert all(13.9 <= b <= 16.1 for b in blended)


def test_mslc_and_nslc_unified_speed_limit_policy():
  """MSLC (mapdOut) and NSLC (navInstruction) both route through the one SpeedLimitPolicy (LOWEST=4)."""
  now = [0.0]
  params = DictParams({
    'MSLCEnabled': True,
    'NSLCEnabled': True,
    'SpeedLimitPolicy': int(SpeedLimitPolicy.LOWEST),
  })
  planner = LongitudinalPlanner(_make_cp(), init_v=25.0, init_a=0.0, dt=DT_MDL)
  planner.map_speed = MapSpeed(params.get_bool, params.get, clock=lambda: now[0])

  v_ego, a_ego = 25.0, 0.0
  # Map says 70 km/h (19.44 m/s), nav says 60 km/h (16.67 m/s) -> LOWEST selects ~16.67 m/s.
  for _ in range(160):
    now[0] += DT_MDL
    sm = _make_sm(v_ego=v_ego, a_ego=a_ego, v_cruise_kph=100.0,
                  mapd_speed_limit=70.0 * CV.KPH_TO_MS,
                  nav_speed_limit=60.0 * CV.KPH_TO_MS)
    planner.update(sm)
    a_ego = 0.65 * a_ego + 0.35 * planner.output_a_target
    v_ego = max(0.0, v_ego + a_ego * DT_MDL)

  assert planner.map_speed._last is not None
  assert planner.map_speed._last <= 17.0, f"Expected LOWEST speed limit ~16.67 m/s, got {planner.map_speed._last}"
  assert v_ego < 19.0, f"Ego did not decelerate to unified speed limit: v_ego={v_ego:.2f}"


def test_tlsc_red_approach_slowdown_and_green_release():
  """TLSC decelerates ego during red-light approach (d in 10..60m) and releases after green."""
  now = [0.0]
  params = DictParams({'TLSCEnabled': True})
  planner = LongitudinalPlanner(_make_cp(), init_v=18.0, init_a=0.0, dt=DT_MDL)
  planner.map_speed = MapSpeed(params.get_bool, params.get, clock=lambda: now[0])

  v_ego, a_ego = 18.0, 0.0
  # Approach red light from 40m down to 10m (well above TLSC_MIN_DIST=3.0m)
  for step in range(140):
    now[0] += DT_MDL
    dist = max(10.0, 40.0 - step * 0.25)
    sm = _make_sm(v_ego=v_ego, a_ego=a_ego, v_cruise_kph=72.0, traffic_light=('red', 0.92, dist))
    planner.update(sm)
    a_ego = 0.65 * a_ego + 0.35 * planner.output_a_target
    v_ego = max(0.0, v_ego + a_ego * DT_MDL)

  assert v_ego < 11.5, f"TLSC failed to decelerate approaching red light: v_ego={v_ego:.2f}m/s"
  v_slowed = v_ego

  # Light turns green -> after LIGHT_HOLD_S (0.5s), TLSC cap clears and ego accelerates
  for _ in range(180):
    now[0] += DT_MDL
    sm = _make_sm(v_ego=v_ego, a_ego=a_ego, v_cruise_kph=72.0, desired_accel=1.2, traffic_light=('green', 0.95, 12.0))
    planner.update(sm)
    a_ego = 0.65 * a_ego + 0.35 * planner.output_a_target
    v_ego = max(0.0, v_ego + a_ego * DT_MDL)

  assert planner.map_speed._last is None
  assert v_ego > v_slowed + 1.5, f"TLSC failed to resume on green: {v_slowed:.2f} -> {v_ego:.2f}"


def test_ddsc_and_rcsc_speed_caps():
  """DDSC slows vehicle on sustained critical awareness drain; RCSC caps speed on wet/debris surface."""
  now = [0.0]
  params_ddsc = DictParams({'DDSCEnabled': True})
  ms_ddsc = MapSpeed(params_ddsc.get_bool, params_ddsc.get, clock=lambda: now[0])
  ddsc_cap = None
  for _ in range(100):
    now[0] += DT_MDL
    sm = _make_sm(v_ego=22.0, v_cruise_kph=80.0, awareness=0.0)
    res = ms_ddsc.update(sm, 22.0, 22.2, None, False, DT_MDL)
    if 'ddsc' in res.sources:
      ddsc_cap = res.sources['ddsc']
  assert ddsc_cap is not None and ddsc_cap < 22.0, f"DDSC failed to cap speed on 0 awareness: {ddsc_cap}"

  now2 = [0.0]
  params_rcsc = DictParams({'RCSCEnabled': True})
  ms_rcsc = MapSpeed(params_rcsc.get_bool, params_rcsc.get, clock=lambda: now2[0])
  rcsc_cap = None
  for _ in range(30):
    now2[0] += DT_MDL
    sm = _make_sm(v_ego=25.0, v_cruise_kph=90.0, surface_score=0.85)
    res = ms_rcsc.update(sm, 25.0, 25.0, None, False, DT_MDL)
    if 'rcsc' in res.sources:
      rcsc_cap = res.sources['rcsc']
  assert rcsc_cap is not None and rcsc_cap < 25.0, f"RCSC failed to cap speed on rough/wet surface: {rcsc_cap}"


def test_brsc_tja_and_drive_mode_on_ngp10():
  """BRSC rough road slowdown, TJA cut-in accel suppression, and DriveModeApplier presets."""
  planner = LongitudinalPlanner(_make_cp(), init_v=20.0, init_a=0.0, dt=DT_MDL)
  v_ego, a_ego = 20.0, 0.0
  for i in range(50):
    az = 9.81 + (3.2 if i % 2 == 0 else -3.2)
    sm = _make_sm(v_ego=v_ego, a_ego=a_ego, v_cruise_kph=72.0, az=az)
    planner.update(sm, feature_flags=FeatureFlags.BRSC)
    a_ego = 0.65 * a_ego + 0.35 * planner.output_a_target
    v_ego = max(0.0, v_ego + a_ego * DT_MDL)
  assert planner.brsc_result.active
  assert planner.brsc_v_target is not None and BRSC_MIN_SPEED_MS <= planner.brsc_v_target < 20.0

  planner_tja = LongitudinalPlanner(_make_cp(), init_v=3.0, init_a=0.0, dt=DT_MDL)
  for _ in range(25):
    sm = _make_sm(v_ego=3.0, v_cruise_kph=50.0, lead_status=True, lead_drel=18.0, lead_vrel=0.5, lead_track_id=1)
    planner_tja.update(sm)
  sm_cutin = _make_sm(v_ego=3.0, v_cruise_kph=50.0, lead_status=True, lead_drel=9.0, lead_vrel=0.0, lead_track_id=2)
  planner_tja.update(sm_cutin)
  assert planner_tja.tja_result.active and planner_tja.tja_result.cut_in
  assert planner_tja.output_a_target <= 1e-6

  p = DictParams({'DriveMode': 'eco'})
  applier = DriveModeApplier(p)
  assert applier.update(now=2.0) is True
  assert p.get('AccelerationProfile') == 'eco' and p.get('LongitudinalPersonality') == RELAXED
  assert applier.current() == 'eco'
  p.put('DriveMode', 'sport')
  assert applier.update(now=4.0) is True
  assert p.get('AccelerationProfile') == 'sport' and p.get('LongitudinalPersonality') == AGGRESSIVE
  p.put('LongitudinalPersonality', STANDARD)
  assert applier.current() == 'custom'


# ---------------------------------------------------------------------------
# 2. Lateral & Safety Policies (ALCC, DLAT, CAT, RED, SOC, Arbiter)
# ---------------------------------------------------------------------------

def test_alcc_brake_modes_and_never_grants_authority():
  """ALCC honors MAINTAIN / PAUSE / DISENGAGE brake modes and NEVER grants control_authority."""
  for mode, expected_on_brake in (
    (BrakeMode.MAINTAIN, ALCCState.ENABLED),
    (BrakeMode.PAUSE, ALCCState.PAUSED),
    (BrakeMode.DISENGAGE, ALCCState.DISABLED),
  ):
    alcc = ALCC(mode)
    res_engage = alcc.update(ALCCInput(feature_enabled=True, engage_request=True))
    assert res_engage.state is ALCCState.ENABLED and res_engage.control_authority is False

    res_brake = alcc.update(ALCCInput(feature_enabled=True, engage_request=True, brake_pressed=True))
    assert res_brake.state is expected_on_brake and res_brake.control_authority is False

    res_release = alcc.update(ALCCInput(feature_enabled=True, engage_request=True, brake_pressed=False))
    if mode is BrakeMode.DISENGAGE:
      assert res_release.state is ALCCState.DISABLED
      res_rearm = alcc.update(ALCCInput(feature_enabled=True, engage_request=True, engage_button=True))
      assert res_rearm.state is ALCCState.ENABLED
    else:
      assert res_release.state is ALCCState.ENABLED


def test_dlat_hysteresis_and_dlp_curve_assist():
  """DLAT debounces laneful<->laneless transitions, while DLP tight-curve assist forces laneless immediately."""
  dlat = DLAT(enter_frames=20)
  mdl_good = _make_curved_model(20.0, curvature=0.0, lane_probs=(0.95, 0.95, 0.95, 0.95))
  for _ in range(15):
    res = dlat.update_model(mdl_good, v_ego=20.0, curve_assist_enabled=True)
  assert res.suggestion is DLATSuggestion.LANEFUL

  # Single tight-curve frame (yaw_rate=1.2 rad/s) bypasses hysteresis immediately
  mdl_curve = _make_curved_model(20.0, curvature=0.06, lane_probs=(0.95, 0.95, 0.95, 0.95), yaw_rate_override=1.2)
  res_curve = dlat.update_model(mdl_curve, v_ego=20.0, curve_assist_enabled=True)
  assert res_curve.curve_detected and res_curve.suggestion is DLATSuggestion.LANELESS

  # Sustained low lane probs switch via hysteresis
  dlat2 = DLAT()
  mdl_poor = _make_curved_model(20.0, curvature=0.0, lane_probs=(0.05, 0.05, 0.05, 0.05))
  for _ in range(30):
    res2 = dlat2.update_model(mdl_poor, v_ego=20.0, curve_assist_enabled=False)
  assert res2.suggestion is DLATSuggestion.LANELESS


def test_cat_steer_ratio_filter_adaptation_and_override():
  """CAT SteerRatioFilter adapts smoothly within bounds when gated, reverts ungated, honors manual SR."""
  flt = SteerRatioFilter(base_steer_ratio=15.0)
  for i in range(60):
    st = flt.step(now=float(i) * 0.5, gated=True, steer_ratio=16.5, stiffness=1.1, angle_offset_deg=0.4)
  assert st.adaptive is True
  assert 15.2 < st.steer_ratio < 16.6

  st_man = flt.step(now=31.0, gated=False, steer_ratio=16.5, stiffness=1.1, angle_offset_deg=0.4, manual_sr=50.0)
  assert st_man.adaptive is True and st_man.steer_ratio == pytest.approx(30.0)


def test_red_soc_and_arbiter_lateral_safety_conventions():
  """RED right/left sign convention + laneless gate; SOC 1s confirmation + ramp; Arbiter conflict-to-zero."""
  red = RED(get_bool=lambda _: True, clock=lambda: 10.0)
  mdl_right_edge = _make_curved_model(20.0, road_edges_y=(-4.5, 0.7), road_edge_stds=(0.1, 0.1))
  out_laneful = red.update(mdl_right_edge, (0.0, 0.0), 20.0, [], is_laneless=False)
  assert out_laneful['state'] == REDState.INACTIVE.name and curvature_nudge(out_laneful) == 0.0

  for _ in range(5):
    out_r = red.update(mdl_right_edge, (0.0, 0.0), 20.0, [], is_laneless=True)
  assert out_r['state'] in (REDState.WARNING.name, REDState.CRITICAL.name)
  assert curvature_nudge(out_r) > 0.0, f"Right road edge must nudge LEFT (+), got {out_r}"

  mdl_left_edge = _make_curved_model(20.0, road_edges_y=(-0.7, 4.5), road_edge_stds=(0.1, 0.1))
  red_l = RED(get_bool=lambda _: True, clock=lambda: 10.0)
  for _ in range(5):
    out_l = red_l.update(mdl_left_edge, (0.0, 0.0), 20.0, [], is_laneless=True)
  assert curvature_nudge(out_l) < 0.0, f"Left road edge must nudge RIGHT (-), got {out_l}"

  # SOC: 4 lane line y arrays (exact 3.5m lane widths inside [2.8, 3.6]m)
  soc = SOC()
  lane_y = tuple([y] * 33 for y in (-5.25, -1.75, 1.75, 5.25))
  lane_p = (0.95, 0.95, 0.95, 0.95)
  lane_s = (0.1, 0.1, 0.1, 0.1)
  res = None
  for _ in range(60):
    res = soc.update(SOCInput(HIGHWAY_SPEED_MPS + 2.0, True, False, lane_y, lane_p, lane_s))
  assert res is not None and res.active_suggestion
  assert res.offset_m < -0.05 and curvature_bias(res) < 0.0

  same = arbitrate([Proposal('soc', 0.2, 22.0), Proposal('pathd', 0.35, 18.0)], room_left_m=0.3, room_right_m=0.5)
  assert same.offset_m == pytest.approx(0.3) and same.speed_cap == pytest.approx(18.0) and not same.conflict

  conflict = arbitrate([Proposal('soc', 0.2, 22.0), Proposal('pathd', -0.25, 16.0)], room_left_m=0.5, room_right_m=0.5)
  assert conflict.offset_m == 0.0 and conflict.conflict is True and conflict.speed_cap == pytest.approx(16.0)
  assert MAX_OFFSET_M == pytest.approx(0.6)


# ---------------------------------------------------------------------------
# 3. Communications Loopback (BigModel TCP + PLP v5 TCP / BLE / Locate)
# ---------------------------------------------------------------------------

def test_bigmodel_tcp_loopback_and_one_way_fallback():
  """Real TCP loopback between BigModel Listener/RemoteModelState/Joining and ref phone server + one-way fallback."""
  vision_n, policy_n = 1024, 600

  class DummyWarp:
    def __init__(self):
      self.output = memoryview(bytearray(BM_P.FRAME_BYTES))
      self.n = 0

    def start_buffers(self, bufs, transforms):
      self.n += 1
      self.output[:] = bytes([self.n % 251]) * BM_P.FRAME_BYTES

    def wait(self):
      pass

  class DummyParser:
    def parse_vision_outputs(self, d):
      return {'hidden_state': d['hidden'].copy()}

    def parse_policy_outputs(self, d):
      return {'plan': d['plan'].copy()}

  class SmallModel:
    desire_key = 'desire'
    numpy_inputs = {}
    lat_delay = 0.0

    def run(self, *a, **k):
      return 'small'

  listener = Listener('127.0.0.1', 0).start()
  deadline = time.monotonic() + 5.0
  while not listener.bound and time.monotonic() < deadline:
    time.sleep(0.01)
  assert listener.bound
  stop = threading.Event()
  threading.Thread(
    target=BM_S.dial,
    args=(BM_S.StubBackend(vision_n, policy_n), '127.0.0.1', listener.port),
    kwargs=dict(retry=0.05, stop=stop),
    daemon=True,
  ).start()

  def connect():
    sock = listener.take(0.5)
    if sock is None:
      raise OSError('no phone')
    c = BigModelClient(sock=sock)
    return c, c.connect()

  def make_remote(c, info):
    return RemoteModelState(
      c, info, DummyWarp(), parser=DummyParser(),
      vision_slices={'hidden': slice(0, 512)}, policy_slices={'plan': slice(0, policy_n)},
      get_action_from_model=lambda *a: 'a', log=logging.getLogger('sim_bm'),
    )

  inputs = {'desire': np.zeros(8, np.float32), 'traffic_convention': np.array([[1, 0]], np.float32)}
  joining = Joining(SmallModel(), connect, make_remote, None, log=logging.getLogger('sim_bm'), in_control=lambda: False)
  try:
    deadline = time.monotonic() + 5.0
    while joining.big_model_state != 'ready' and time.monotonic() < deadline:
      time.sleep(0.01)
    assert joining.big_model_state == 'ready'
    outs = [joining.run({}, {}, inputs) for _ in range(8)]
    assert outs[:3] == ['small'] * 3
    assert joining.big_model_state == 'running' and joining.is_big
    assert 'hidden_state' in outs[-1]

    # Disconnect phone -> immediate hand-back to local small model
    stop.set()
    joining._big.client.close()
    assert joining.run({}, {}, inputs) == 'small'
    assert not joining.is_big
  finally:
    joining.close()
    listener.close()
    stop.set()


def test_plp_v5_tcp_loopback_ble_mtu_and_locate_sink():
  """PLP v5 over real TCP loopback (CablePLP) + BLE MTU chunking (ble_stream.split) + LocateSink horizon delivery."""
  fixture = pathlib.Path(__file__).parent / 'fixtures' / 'map_horizon_navpilot.json'
  services = (('core', 1, 1), ('locate', 1, 1))
  sink = LocateSink(clock=lambda: 100.0)
  horizon_desc = PLP_R.BY_NAME['locate.map_horizon']

  cable = CablePLP(
    PlpHello(services=services, max_payload=256, link='usb'),
    setup=sink.attach,
    bind_addr='127.0.0.1',
    port=0,
    allowed_prefix='127.',
  )
  cable.start()
  try:
    deadline = time.monotonic() + 3.0
    while cable.bound_port is None and time.monotonic() < deadline:
      time.sleep(0.01)
    assert cable.bound_port is not None

    sock = socket.create_connection(('127.0.0.1', cable.bound_port), timeout=3.0)
    sock.settimeout(2.0)
    phone = PlpSession(PlpHello(services=services, max_payload=256, link='usb'),
                       lambda b: sock.sendall(b), role='phone')
    phone.hello_request()

    deadline = time.monotonic() + 3.0
    while phone.agreed is None and time.monotonic() < deadline:
      chunk = sock.recv(4096)
      if not chunk:
        break
      phone.feed(chunk)
    assert phone.agreed is not None

    # Send full ~2.7 KB map horizon across fragmented 256-byte frames over real TCP loopback
    phone.send(PlpMessage(PLP_R.KIND_NOTIFY, horizon_desc.service, horizon_desc.message, fixture.read_bytes(), 1))
    deadline = time.monotonic() + 3.0
    while sink.horizons_taken < 1 and time.monotonic() < deadline:
      time.sleep(0.01)
    assert sink.horizons_taken == 1
    assert sink.tracker.current.route_id == json.loads(fixture.read_text())['route_id']
    sock.close()
  finally:
    cable.stop()

  # BLE MTU chunking (23-byte ATT MTU -> 20-byte payload chunks) preserves framed PLP + CRC-32C integrity
  raw_frame = PLP_F.Frame(
    kind=PLP_R.KIND_NOTIFY, service=horizon_desc.service, message=horizon_desc.message,
    payload=b'plp-ble-loopback-payload-1234567890',
  ).encode()
  chunks = PLP_BLE.split(raw_frame, att_mtu=23)
  assert len(chunks) >= 2 and all(len(c) <= 20 for c in chunks)
  dec = PLP_F.FrameDecoder()
  frames = []
  for ch in chunks:
    frames.extend(dec.feed(ch))
  assert len(frames) == 1 and dec.bad_crc == 0 and frames[0].payload == b'plp-ble-loopback-payload-1234567890'


def test_tlsc_virtual_lead_preserves_lead_two_and_yields_to_gas_at_standstill():
  from openpilot.selfdrive.controls.lib.longitudinal_planner import _with_virtual_stop_lead
  rs = messaging.new_message('radarState').radarState
  rs.leadTwo.status = True
  rs.leadTwo.dRel = 42.0
  wrapped = _with_virtual_stop_lead(rs, 25.0, 10.0)
  assert wrapped.leadOne.status is True and wrapped.leadOne.modelProb == 0.0 and wrapped.leadOne.aLeadTau == 1.5
  assert wrapped.leadTwo.status is True and wrapped.leadTwo.dRel == 42.0

  planner = LongitudinalPlanner(_make_cp(), init_v=0.2, init_a=-1.0, dt=DT_MDL)
  planner.map_speed = MapSpeed(lambda k: k == 'TLSCEnabled', lambda _k: None)
  sm_hold = _make_sm(v_ego=0.2, a_ego=-1.0, v_cruise_kph=50.0, traffic_light=('red', 0.9, 4.0), gas_pressed=False)
  planner.update(sm_hold, feature_flags=0)
  assert planner.output_should_stop is True and planner.output_a_target <= -1.0

  sm_gas = _make_sm(v_ego=0.2, a_ego=0.0, v_cruise_kph=50.0, traffic_light=('red', 0.9, 4.0), gas_pressed=True)
  planner.update(sm_gas, feature_flags=0)
  assert planner.output_a_target > -0.5, f"TLSC standstill hold fought driver gasPressed: {planner.output_a_target}"

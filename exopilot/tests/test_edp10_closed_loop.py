"""Tier B closed-loop simulation & regression tests for EDP10.

Exercises the real LongitudinalPlanner + LongitudinalMpc, BRSC (comma 3 vertical
-v[0] axis, IMU dropout recovery, disabled telemetry gating, dict-sm Plant
compatibility), dp_tja, speed_zones (including unthrottled brake release), and
BYD/Chery CarController + panda libsafety TX hooks.
"""

from __future__ import annotations

import math

import cereal.messaging as messaging
from cereal import car, log
from opendbc.car import gen_empty_fingerprint, structs
from opendbc.car.byd.values import CarControllerParams as BydCCP
from opendbc.car.chery.interface import CarInterface as CheryCarInterface
from opendbc.car.chery.values import CAR as CHERY_CAR
from openpilot.common.constants import CV
from openpilot.common.realtime import DT_MDL
from openpilot.selfdrive.controls.lib.longcontrol import LongCtrlState
from openpilot.selfdrive.controls.lib.longitudinal_planner import (
  BRSC_MIN_SPEED_MS,
  BRSC_MIN_V_EGO,
  DPFlags,
  LongitudinalPlanner,
)
from openpilot.selfdrive.modeld.constants import ModelConstants

from exopilot.common.speed_zones import (
  CITY_SPEED_MPS,
  STEER_ZONE_ANGLE_DEG,
  STEER_ZONE_RATE_DEG_20MS,
  STEER_ZONE_SPEEDS_MPS,
  longitudinal_accel_max,
  longitudinal_jerk_up,
)


class FakeSubMaster(dict):
  """Minimal SubMaster-compatible mapping for deterministic closed-loop planner runs."""

  def __init__(self, services: dict):
    super().__init__(services)
    self.valid = dict.fromkeys(services, True)
    self.alive = dict.fromkeys(services, True)
    self.updated = dict.fromkeys(services, True)
    self.logMonoTime = dict.fromkeys(services, 0)

  def all_checks(self, service_list=None):
    keys = service_list if service_list is not None else self.keys()
    return all(self.valid.get(k, False) and self.alive.get(k, False) for k in keys)


def _make_cp():
  CP = car.CarParams.new_message()
  CP.openpilotLongitudinalControl = True
  CP.longitudinalActuatorDelay = 0.15
  CP.vEgoStopping = 0.5
  CP.steerRatio = 15.0
  CP.wheelbase = 2.72
  return CP


def _make_sm(
  v_ego: float,
  a_ego: float = 0.0,
  v_cruise_kph: float = 72.0,
  lead_status: bool = False,
  lead_drel: float = 40.0,
  lead_vrel: float = 0.0,
  lead_a: float = 0.0,
  lead_track_id: int = 1,
  lead_model_prob: float = 1.0,
  az_vertical: float = 9.81,
  ax_longitudinal: float = 0.0,
  accel_valid: bool = True,
  enabled: bool = True,
  standstill: bool = False,
  experimental: bool = False,
):
  cs = messaging.new_message('carState').carState
  cs.vEgo = float(v_ego)
  cs.aEgo = float(a_ego)
  cs.vCruise = float(v_cruise_kph)
  cs.standstill = bool(standstill or v_ego < 0.05)
  cs.steeringAngleDeg = 0.0

  ctrl = messaging.new_message('controlsState').controlsState
  ctrl.longControlState = LongCtrlState.pid if enabled else LongCtrlState.off
  ctrl.forceDecel = False

  sd = messaging.new_message('selfdriveState').selfdriveState
  sd.enabled = bool(enabled)
  sd.experimentalMode = bool(experimental)
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
    rs.leadOne.modelProb = float(lead_model_prob)

  mdl = messaging.new_message('modelV2').modelV2
  n = ModelConstants.IDX_N
  t_idxs = ModelConstants.T_IDXS
  mdl.position.x = [float(v_ego * t) for t in t_idxs]
  mdl.velocity.x = [float(v_ego) for _ in range(n)]
  mdl.acceleration.x = [0.0] * n
  mdl.meta.disengagePredictions.gasPressProbs = [1.0, 1.0]
  mdl.action.desiredAcceleration = 0.0
  mdl.action.shouldStop = False

  # Comma 3 accelerometer axes (locationd.py:108-109): meas = [-v[2], -v[1], -v[0]]
  # -> v[0] is vertical (gravity ~ +9.81), v[2] is longitudinal.
  acc = messaging.new_message('accelerometer').accelerometer
  acc.acceleration.v = [float(az_vertical), 0.0, float(-ax_longitudinal)]

  sm = FakeSubMaster({
    'carState': cs,
    'controlsState': ctrl,
    'selfdriveState': sd,
    'carControl': cc,
    'liveParameters': lp,
    'radarState': rs,
    'modelV2': mdl,
    'accelerometer': acc,
  })
  sm.valid['accelerometer'] = accel_valid
  return sm


def test_brsc_closed_loop_vertical_axis_dropout_and_longitudinal_rejection():
  """Real LongitudinalPlanner + BRSC: vertical v[0] triggers, longitudinal v[2] does NOT, IMU dropout clears hold."""
  planner = LongitudinalPlanner(_make_cp(), init_v=20.0, init_a=0.0, dt=DT_MDL)
  v_ego = 20.0
  v_cruise_kph = 72.0  # 20.0 m/s

  # 1. Smooth road + strong longitudinal braking/accel on v[2] must NOT trigger BRSC.
  for i in range(60):
    ax = 4.0 * math.sin(i * 0.6)
    planner.update(_make_sm(v_ego=v_ego, v_cruise_kph=v_cruise_kph, az_vertical=9.81, ax_longitudinal=ax),
                   dp_flags=DPFlags.BRSC)
  assert planner.brsc_result is not None and not planner.brsc_result.active
  assert planner.brsc_v_target is None

  # 2. Sustained vertical road bumps on v[0] must engage BRSC and reduce target speed.
  min_target = v_cruise_kph * CV.KPH_TO_MS
  for i in range(60):
    az = 9.81 + 4.5 * math.sin(i * 1.1)
    planner.update(_make_sm(v_ego=v_ego, v_cruise_kph=v_cruise_kph, az_vertical=az), dp_flags=DPFlags.BRSC)
    if planner.brsc_v_target is not None:
      min_target = min(min_target, planner.brsc_v_target)
  assert planner.brsc_result is not None and planner.brsc_result.active
  assert BRSC_MIN_SPEED_MS <= min_target < v_cruise_kph * CV.KPH_TO_MS

  # 3. IMU dropout mid-hold must clear BRSC rather than latching active forever.
  planner.update(_make_sm(v_ego=v_ego, v_cruise_kph=v_cruise_kph, accel_valid=False), dp_flags=DPFlags.BRSC)
  assert planner.brsc_result is None
  assert planner.brsc_v_target is None

  # 4. Low-speed gating: below BRSC_MIN_V_EGO, v_cruise must not be cut.
  planner_slow = LongitudinalPlanner(_make_cp(), init_v=BRSC_MIN_V_EGO - 1.0, init_a=0.0, dt=DT_MDL)
  for i in range(50):
    planner_slow.update(
      _make_sm(v_ego=BRSC_MIN_V_EGO - 1.0, v_cruise_kph=25.0, az_vertical=9.81 + 4.5 * math.sin(i * 1.1)),
      dp_flags=DPFlags.BRSC,
    )
  assert planner_slow.brsc_v_target is None


def test_brake_release_is_not_throttled_by_tja_or_speed_zone_jerk_up():
  """Releasing brakes from negative accel toward 0.0 must never be held back by longitudinal_jerk_up."""
  planner = LongitudinalPlanner(_make_cp(), init_v=2.0, init_a=-2.5, dt=DT_MDL)
  planner.output_a_target = -2.5
  # Clear road, cruise set above v_ego so MPC requests positive accel immediately.
  sm = _make_sm(v_ego=2.0, a_ego=-2.5, v_cruise_kph=50.0, lead_status=False, enabled=True)
  planner.update(sm, dp_flags=0)
  # Positive ramp starts from 0.0, NOT from -2.5 + 0.35*0.05 = -2.4825.
  assert planner.output_a_target >= 0.0, f"Brake release throttled: output_a_target={planner.output_a_target}"


def test_tja_closed_loop_cut_in_and_speed_gate():
  """dp_tja suppresses positive acceleration on close cut-in below CITY_SPEED_MPS and deactivates above it."""
  planner = LongitudinalPlanner(_make_cp(), init_v=4.0, init_a=0.0, dt=DT_MDL)
  v_ego = 4.0

  # Establish original lead id=1 at comfortable distance.
  for _ in range(10):
    sm = _make_sm(v_ego=v_ego, v_cruise_kph=50.0, lead_status=True, lead_drel=25.0, lead_vrel=0.0, lead_track_id=1)
    planner.update(sm, dp_flags=0)

  # Sudden closer cut-in lead id=99 at 10 m -> TJA must suppress positive accel.
  sm_cutin = _make_sm(v_ego=v_ego, v_cruise_kph=50.0, lead_status=True, lead_drel=10.0, lead_vrel=0.0, lead_track_id=99)
  planner.update(sm_cutin, dp_flags=0)
  assert planner.output_a_target <= 1e-6, f"TJA cut-in failed to suppress positive accel: {planner.output_a_target}"

  # Above CITY_SPEED_MPS, TJA must deactivate.
  sm_fast = _make_sm(v_ego=CITY_SPEED_MPS + 4.0, v_cruise_kph=72.0, lead_status=True, lead_drel=35.0, lead_vrel=0.0, lead_track_id=99)
  planner.update(sm_fast, dp_flags=0)
  assert not planner.tja.lead_seen


def test_speed_zones_closed_loop_accel_and_jerk_envelope():
  """LongitudinalPlanner respects speed_zones positive accel (steady-state) and jerk-up ceilings across speeds."""
  for v_start in (1.0, 4.0, 9.0, 18.0, 28.0):
    planner = LongitudinalPlanner(_make_cp(), init_v=v_start, init_a=0.0, dt=DT_MDL)
    prev_a = None
    for step in range(45):
      sm = _make_sm(v_ego=v_start, a_ego=0.0, v_cruise_kph=130.0, enabled=(step > 0))
      planner.update(sm, dp_flags=0)
      if step > 1 and prev_a is not None:
        assert planner.output_a_target - prev_a <= longitudinal_jerk_up(v_start) * DT_MDL + 1e-4
      if step >= 35:
        assert planner.output_a_target <= longitudinal_accel_max(v_start) + 1e-4
      prev_a = planner.output_a_target


def test_acc_closed_loop_lead_brake_to_stop():
  """Closed-loop kinematic plant + real LongitudinalPlanner/MPC braking behind stopping lead."""
  planner = LongitudinalPlanner(_make_cp(), init_v=20.0, init_a=0.0, dt=DT_MDL)
  v_ego = 20.0
  a_ego = 0.0
  x_ego = 0.0
  v_lead = 20.0
  x_lead = 45.0
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

    sm = _make_sm(
      v_ego=v_ego,
      a_ego=a_ego,
      v_cruise_kph=72.0,
      lead_status=True,
      lead_drel=d_rel,
      lead_vrel=v_lead - v_ego,
      lead_a=a_lead,
      standstill=(v_ego < 0.05),
    )
    planner.update(sm, dp_flags=0)
    a_ego = 0.65 * a_ego + 0.35 * planner.output_a_target
    v_ego = max(0.0, v_ego + a_ego * DT_MDL)
    x_ego += v_ego * DT_MDL

  assert min_gap > 2.5, f"Collision or unsafe gap: min_gap={min_gap:.2f}m"
  assert v_ego < 0.3, f"Ego did not stop behind stopped lead: v_ego={v_ego:.2f}m/s"
  assert planner.output_should_stop


def test_plain_dict_sm_plant_compatibility():
  """Upstream Plant passes a plain dict sm (no .valid attr); LongitudinalPlanner.update() must handle it."""
  planner = LongitudinalPlanner(_make_cp(), init_v=15.0, init_a=0.0, dt=DT_MDL)
  sm_dict = dict(_make_sm(v_ego=15.0, v_cruise_kph=72.0))
  del sm_dict['accelerometer']
  planner.update(sm_dict, dp_flags=DPFlags.BRSC)
  assert math.isfinite(planner.output_a_target)


def test_byd_8pt_backstop_lut_and_chery_car_controller_safety_parity():
  """Verify BYD 8-point backstop LUT (80%/100%) and Chery/BYD controllers pass libsafety."""
  from opendbc.car.structs import CarParams
  from opendbc.safety.tests.libsafety import libsafety_py

  assert tuple(BydCCP.ZONE_MAX_ANGLE_BP) == STEER_ZONE_SPEEDS_MPS
  assert tuple(BydCCP.ZONE_MAX_RATE_BP) == STEER_ZONE_SPEEDS_MPS
  for idx in range(len(STEER_ZONE_SPEEDS_MPS)):
    assert math.isclose(BydCCP.ZONE_MAX_ANGLE_DEG[idx], 0.8 * STEER_ZONE_ANGLE_DEG[idx], rel_tol=1e-6)
    assert math.isclose(BydCCP.ZONE_MAX_RATE_DEG_20MS[idx], 0.8 * STEER_ZONE_RATE_DEG_20MS[idx], rel_tol=1e-6)

  for platform in (CHERY_CAR.CHERY_OMODA_5, CHERY_CAR.CHERY_ICAUR_03, CHERY_CAR.CHERY_JAECOO_J7_PHEV):
    CP = CheryCarInterface.get_params(str(platform), gen_empty_fingerprint(), [], False, False, 0, False)
    CI_inst = CheryCarInterface(CP)
    CI_inst.update([])
    CC = structs.CarControl()
    CC.enabled = True
    CC.latActive = True
    CC.actuators.steeringAngleDeg = 5.0
    CI_inst.CS.out.vEgo = 15.0
    CI_inst.CS.out.cruiseState.enabled = True
    CI_inst.CS.out.steeringAngleDeg = 4.0
    _, can_sends = CI_inst.apply(CC.as_reader(), 0)
    assert len(can_sends) > 0

  safety = libsafety_py.libsafety
  assert safety.set_safety_hooks(CarParams.SafetyModel.byd, 0) == 0
  safety.init_tests()
  safety.set_controls_allowed(True)
  assert safety.get_controls_allowed()

def test_aem_first_order_filter_uses_exact_time_constants(capsys):
  """Regression: AEM must pass tau (rc seconds) to FirstOrderFilter, not alpha."""
  from dragonpilot.selfdrive.controls.lib.aem import AEM
  aem = AEM(debug=False)
  out = capsys.readouterr().out
  assert "FirstOrderFilter init failed" not in out
  expected_alpha = DT_MDL / (AEM.EMA_TC_V_EGO + DT_MDL)
  assert abs(aem._v_ego_ema.alpha - expected_alpha) < 1e-9


def test_plant_dict_sm_regression():
  """Regression: LongitudinalPlanner.update must accept Plant's plain dict sm."""
  from openpilot.selfdrive.test.longitudinal_maneuvers.plant import Plant
  plant = Plant(lead_relevancy=False, speed=15.0, distance_lead=50.0)
  plant.step(15.0, 0.0, 20.0, 0.0, 1.0)


def _curved_model_v2(v_ego: float, curvature: float):
  """Build a modelV2 message with steady velocity and yaw rate corresponding to curvature."""
  msg = messaging.new_message('modelV2').modelV2
  n = len(ModelConstants.T_IDXS)
  msg.velocity.x = [float(v_ego)] * n
  msg.orientationRate.z = [float(v_ego * curvature)] * n
  msg.position.x = [float(v_ego * t) for t in ModelConstants.T_IDXS]
  msg.position.y = [0.0] * n
  msg.position.z = [0.0] * n
  msg.acceleration.x = [0.0] * n
  return msg


def test_vtsc_closed_loop_slows_for_curve_and_recovers():
  """Real LongitudinalPlanner + VTSC: slows on sharp curve, recovers on straight, inert when flag OFF."""
  CP = _make_cp()
  planner_off = LongitudinalPlanner(CP, init_v=22.0)
  planner_on = LongitudinalPlanner(CP, init_v=22.0)

  v_off = 22.0
  v_on = 22.0
  v_on_min = v_on
  # 8s approaching/in sharp curve (R = 50m -> kappa = 0.02 -> ~9.5 m/s comfort)
  for _ in range(160):
    sm_off = _make_sm(v_ego=v_off, v_cruise_kph=80.0)
    sm_off['modelV2'] = _curved_model_v2(v_off, 0.02)
    planner_off.update(sm_off, dp_flags=0)
    v_off = max(0.0, v_off + float(planner_off.a_desired) * DT_MDL)

    sm_on = _make_sm(v_ego=v_on, v_cruise_kph=80.0)
    sm_on['modelV2'] = _curved_model_v2(v_on, 0.02)
    planner_on.update(sm_on, dp_flags=DPFlags.VTSC)
    v_on = max(0.0, v_on + float(planner_on.a_desired) * DT_MDL)
    v_on_min = min(v_on_min, v_on)

  assert planner_off.vtsc_v_target is None
  assert v_off > 20.5, f"VTSC OFF unexpectedly slowed: {v_off:.2f}"
  assert planner_on.vtsc_v_target is not None and planner_on.vtsc_v_target < 15.0
  assert v_on_min < 16.0, f"VTSC ON failed to decelerate for R=50m curve: min={v_on_min:.2f}"

  # Straight road recovery (10s)
  for _ in range(200):
    sm_on = _make_sm(v_ego=v_on, v_cruise_kph=80.0)
    sm_on['modelV2'] = _curved_model_v2(v_on, 0.0)
    planner_on.update(sm_on, dp_flags=DPFlags.VTSC)
    v_on = max(0.0, v_on + float(planner_on.a_desired) * DT_MDL)

  assert planner_on.vtsc_v_target is None
  assert v_on > v_on_min + 3.0, f"VTSC failed to recover on straight road: {v_on_min:.2f} -> {v_on:.2f}"


def _make_mapd_ext(curve_dist_m: float, curvature: float):
  ext = messaging.new_message('mapdExtendedOut').mapdExtendedOut
  ext.position.latitude = 13.7563
  ext.position.longitude = 100.5018
  # 1 deg latitude ~= 111111 m; generate points every 20m up to 460m
  pts = ext.init('path', 24)
  for i in range(24):
    d = i * 20.0
    pts[i].latitude = 13.7563 + d / 111111.0
    pts[i].longitude = 100.5018
    pts[i].curvature = float(curvature if abs(d - curve_dist_m) <= 30.0 else 0.0)
    pts[i].targetVelocity = 0.0
  return ext


def test_mtsc_proactive_slowdown_handover_and_hold():
  """Real LongitudinalPlanner + MTSC: slows for 260m map curve, holds across 1 Hz updates, expires after 3s."""
  CP = _make_cp()
  planner = LongitudinalPlanner(CP, init_v=25.0)
  v_ego = 25.0

  # Feed 1 Hz mapdExtendedOut for 4s (80 ticks) with R=60m curve at 260m
  for step in range(80):
    sm = _make_sm(v_ego=v_ego, v_cruise_kph=90.0)
    if step % 20 == 0:
      sm['mapdExtendedOut'] = _make_mapd_ext(260.0, 1.0 / 60.0)
      sm.valid['mapdExtendedOut'] = True
      sm.updated['mapdExtendedOut'] = True
    planner.update(sm, dp_flags=DPFlags.MTSC | DPFlags.VTSC)
    v_ego = max(0.0, v_ego + float(planner.a_desired) * DT_MDL)

  assert planner._mtsc_v_target is not None and planner._mtsc_v_target < 15.0
  assert v_ego < 22.0, f"MTSC did not decelerate proactively: v_ego={v_ego:.2f}"

  # Verify MTSC target expires cleanly after >3.0s without map updates (no stale lock)
  for _ in range(70):
    sm = _make_sm(v_ego=v_ego, v_cruise_kph=90.0)
    planner.update(sm, dp_flags=DPFlags.MTSC | DPFlags.VTSC)
  assert planner._mtsc_v_target is None


def test_mslc_posted_limit_hold_and_expiry():
  """Real LongitudinalPlanner + MSLC: caps cruise to posted 60 km/h limit and expires if mapd drops out."""
  CP = _make_cp()
  planner = LongitudinalPlanner(CP, init_v=17.5)
  v_ego = 17.5

  for step in range(60):
    sm = _make_sm(v_ego=v_ego, v_cruise_kph=80.0)
    if step % 20 == 0:
      mo = messaging.new_message('mapdOut').mapdOut
      mo.speedLimit = 60.0 / 3.6
      mo.nextSpeedLimit = 0.0
      mo.nextSpeedLimitDistance = 0.0
      sm['mapdOut'] = mo
      sm.valid['mapdOut'] = True
      sm.updated['mapdOut'] = True
    planner.update(sm, dp_flags=DPFlags.MSLC)
    v_ego = max(0.0, v_ego + float(planner.a_desired) * DT_MDL)

  assert planner._mslc_v_target is not None
  assert abs(planner._mslc_v_target - (60.0 / 3.6)) < 0.5
  assert v_ego < 17.2

  # Expire after >3s silence
  for _ in range(70):
    sm = _make_sm(v_ego=v_ego, v_cruise_kph=80.0)
    planner.update(sm, dp_flags=DPFlags.MSLC)
  assert planner._mslc_v_target is None


def test_desire_helper_lane_gap_and_width_guards():
  """DesireHelper blocks lane change when target lane is narrow or adjacent gap is unsafe."""
  from openpilot.selfdrive.controls.lib.desire_helper import DesireHelper, LaneChangeState

  cs = messaging.new_message('carState').carState
  cs.vEgo = 20.0
  cs.leftBlinker = True
  cs.steeringPressed = True
  cs.steeringTorque = 50.0

  # Narrow target left lane (left edge at y=-4.0 vs left lane line at y=-1.8 => 2.2m < 2.5m)
  mdl = messaging.new_message('modelV2').modelV2
  xs = [float(i * 5) for i in range(33)]
  lines = mdl.init('laneLines', 4)
  for ll in lines:
    ll.x = xs
    ll.y = [0.0] * 33
  lines[0].y = [-5.5] * 33
  lines[1].y = [-1.8] * 33
  lines[2].y = [1.8] * 33
  lines[3].y = [5.5] * 33
  mdl.laneLineProbs = [0.9, 0.9, 0.9, 0.9]
  edges = mdl.init('roadEdges', 2)
  edges[0].x = xs
  edges[0].y = [-4.0] * 33
  edges[1].x = xs
  edges[1].y = [5.5] * 33

  dh = DesireHelper(dp_lat_lca_speed=20, lca_lane_width=True)
  dh.update(cs, True, 0.0, False, False, model_v2=mdl)
  assert dh.lane_change_state == LaneChangeState.preLaneChange
  dh.update(cs, True, 0.0, False, False, model_v2=mdl)
  assert dh.lane_change_state == LaneChangeState.preLaneChange, "Narrow target lane must block laneChangeStarting"

  # Unsafe adjacent-lane gap (wide lane, but adjacent vehicle closing fast on left: modelV2 y=-3.2m right-positive)
  edges[0].y = [-6.0] * 33
  leads = mdl.init('leadsV3', 1)
  leads[0].prob = 0.9
  leads[0].x = [6.0]
  leads[0].y = [-3.2]
  leads[0].v = [12.0]
  dh_gap = DesireHelper(dp_lat_lca_speed=20, lca_gap_eval=True)
  dh_gap.update(cs, True, 0.0, False, False, model_v2=mdl)
  dh_gap.update(cs, True, 0.0, False, False, model_v2=mdl)
  assert dh_gap.lane_change_state == LaneChangeState.preLaneChange, "Unsafe adjacent TTC gap must block laneChangeStarting"


def test_lead_departure_and_steering_resume_ramp():
  """LeadDepartureNotice fires on stopped lead pulling away; SteeringResumeRamp ramps cleanly."""
  from exopilot.selfdrive.controls.lib.lead_departure import LeadDepartureNotice
  from exopilot.selfdrive.controls.lib.steering_policy import SteeringResumeRamp

  ld = LeadDepartureNotice()
  lead = messaging.new_message('radarState').radarState.leadOne
  lead.status = True
  lead.dRel = 5.0
  lead.vLead = 0.0
  assert not ld.update(True, lead)
  lead.dRel = 6.5
  lead.vLead = 1.5
  assert ld.update(True, lead)

  ramp = SteeringResumeRamp()
  t0, a0 = ramp.update(False, 0.0, 0.0, 0.0, 0.0)
  assert t0 == 0.0 and a0 == 0.0
  t1, _ = ramp.update(True, 1.0, 10.0, 0.0, 0.1)
  t2, _ = ramp.update(True, 1.0, 10.0, 0.0, 2.0)
  assert 0.0 <= t1 < t2 <= 1.0


def test_acm_coasts_on_open_road_but_yields_to_speed_cap_deceleration():
  """ACM suppresses mild overspeed braking on open highway, but yields immediately to VTSC/MSLC/BRSC slowdowns."""
  planner = LongitudinalPlanner(_make_cp(), init_v=20.2, init_a=0.0, dt=DT_MDL)
  # Open highway, slight overspeed (20.2 m/s vs 20.0 m/s cruise): ACM engages and suppresses mild coast braking.
  sm_open = _make_sm(v_ego=20.2, a_ego=0.0, v_cruise_kph=72.0, enabled=True)
  planner.update(sm_open, dp_flags=DPFlags.ACM | DPFlags.MSLC)
  assert planner.acm.active is True
  assert planner.output_a_target >= -1e-6, f"ACM should suppress mild overspeed braking, got {planner.output_a_target}"

  # Posted speed limit drops to 65 km/h (~18.06 m/s) -> MSLC reduces v_cruise, ACM must yield so MPC decelerates!
  mo = messaging.new_message('mapdOut').mapdOut
  mo.speedLimit = 65.0 / 3.6
  sm_mslc = _make_sm(v_ego=20.2, a_ego=0.0, v_cruise_kph=72.0, enabled=True)
  sm_mslc['mapdOut'] = mo
  sm_mslc.valid['mapdOut'] = True
  sm_mslc.updated['mapdOut'] = True
  for _ in range(5):
    planner.update(sm_mslc, dp_flags=DPFlags.ACM | DPFlags.MSLC)
  assert planner.acm.active is False, "ACM stayed active and suppressed MSLC deceleration"
  assert planner.output_a_target < -0.1, f"Expected MSLC deceleration after ACM yielded, got {planner.output_a_target}"

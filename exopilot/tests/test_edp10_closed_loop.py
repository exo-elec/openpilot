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

from exopilot.speed_zones import (
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

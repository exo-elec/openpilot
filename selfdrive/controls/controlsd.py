#!/usr/bin/env python3
import math
import time
from numbers import Number

from cereal import car, log
import cereal.messaging as messaging
from openpilot.common.constants import CV
from openpilot.common.params import Params
from openpilot.common.realtime import config_realtime_process, Priority, Ratekeeper, DT_CTRL
from openpilot.common.swaglog import cloudlog

from opendbc.car.car_helpers import interfaces
from nagaspilot.controls.ngp_vehicle_model import VehicleModel
from openpilot.selfdrive.controls.lib.drive_helpers import clip_curvature
from nagaspilot.controls.ngp_dlat import NGPDLAT, DLATSuggestion
from nagaspilot.controls.ngp_cat import NGPCAT, live_params_gated
from nagaspilot.controls.ngp_red import NGPRED, curvature_nudge
from nagaspilot.controls.ngp_blinker_pause import NGPBlinkerPause
from nagaspilot.controls.ngp_driver_activity import monitoring_force_decel
from nagaspilot.controls.ngp_arbiter import Proposal, arbitrate
from nagaspilot.controls.ngp_pathd_consumer import BIAS_PER_METER, PathAdjustFollower
from nagaspilot.runtime.rule_channel import RuleChannelConsumer, apply_curvature
from nagaspilot.controls.ngp_soc import NGPSOC, SOCInput, threats_from
from nagaspilot.runtime.path_adapter import lane_room
from nagaspilot.controls.ngp_alcc import ALCCInput, NGPALCC
from nagaspilot.runtime.lateral_authorization import lateral_authorized
from nagaspilot.controls.steering_policy import SteeringResumeRamp
from openpilot.selfdrive.controls.lib.latcontrol import LatControl
from openpilot.selfdrive.controls.lib.latcontrol_pid import LatControlPID
from openpilot.selfdrive.controls.lib.latcontrol_angle import LatControlAngle, STEER_ANGLE_SATURATION_THRESHOLD
from openpilot.selfdrive.controls.lib.latcontrol_torque import LatControlTorque
from openpilot.selfdrive.controls.lib.longcontrol import LongControl
from openpilot.selfdrive.locationd.helpers import PoseCalibrator, Pose

State = log.SelfdriveState.OpenpilotState
LaneChangeState = log.LaneChangeState
LaneChangeDirection = log.LaneChangeDirection

ACTUATOR_FIELDS = tuple(car.CarControl.Actuators.schema.fields.keys())


class Controls:
  def __init__(self) -> None:
    self.params = Params()
    cloudlog.info("controlsd is waiting for CarParams")
    self.CP = messaging.log_from_bytes(self.params.get("CarParams", block=True), car.CarParams)
    cloudlog.info("controlsd got CarParams")

    self.CI = interfaces[self.CP.carFingerprint](self.CP)

    self.sm = messaging.SubMaster(['liveParameters', 'liveTorqueParameters', 'modelV2', 'selfdriveState',
                                   'liveCalibration', 'livePose', 'longitudinalPlan', 'carState', 'carOutput',
                                   'driverMonitoringState', 'onroadEvents', 'driverAssistance', 'pathAdjust', 'pandaStates'], poll='selfdriveState')
    self.pm = messaging.PubMaster(['carControl', 'controlsState'])

    self.steer_limited_by_safety = False
    self.curvature = 0.0
    self.desired_curvature = 0.0
    self.steering_resume_ramp = SteeringResumeRamp()

    self.pose_calibrator = PoseCalibrator()
    self.calibrated_pose: Pose | None = None

    self.LoC = LongControl(self.CP)
    self.VM = VehicleModel(self.CP)
    self.LaC: LatControl
    if self.CP.steerControlType == car.CarParams.SteerControlType.angle:
      self.LaC = LatControlAngle(self.CP, self.CI)
    elif self.CP.lateralTuning.which() == 'pid':
      self.LaC = LatControlPID(self.CP, self.CI)
    elif self.CP.lateralTuning.which() == 'torque':
      self.LaC = LatControlTorque(self.CP, self.CI)

    self.alcc_enabled = self.params.get_bool("ngp_lat_alcc")
    # Blinker pause (ngp_lat_blinker_pause_mph, 0 = off): no lateral assistance with a signal on below this speed
    pause_mph = self.params.get("ngp_lat_blinker_pause_mph", return_default=True)
    self.blinker_pause = NGPBlinkerPause(float(pause_mph) * CV.MPH_TO_MS) if pause_mph else None
    # SOC (ngp_lat_soc, default off): small slow offset away from a vehicle beside you on the highway
    self.soc = NGPSOC() if self.params.get_bool("ngp_lat_soc") else None
    # pathd add-on offset (ngp_lat_pathd, default off): bounded lateral offset from `pathAdjust`; replaces SOC's own offset when on
    self.pathd_lat = PathAdjustFollower() if self.params.get_bool("ngp_lat_pathd") else None
    # Parallel rule channel (pathd + DPP): applies pathd's mode to the policy curvature; no pathd or mode < 4 = policy unchanged
    self.rule_consumer = RuleChannelConsumer()
    # RED (ngp_lat_edge_guard, default off): push away from a close road edge in laneless mode, vision only
    self.red = NGPRED() if self.params.get_bool("ngp_lat_edge_guard") else None
    # CAT: smoothed/validated steer ratio and stiffness (opt-in, default off)
    self.cat = NGPCAT(self.CP.steerRatio) if self.params.get_bool("ngp_lat_cat") else None
    self.alcc_active = False
    self.alcc = NGPALCC()

    # DLAT: advisory Laneful/Laneless confidence arbitration (non-controlling
    # in the curvature/steering sense). Always automatic -- a default behavior
    # of this branch, no user-selectable mode and no panel control for the
    # mode itself. DLP curve assist (ngp_lat_dlp_curves) is a separate,
    # panel-exposed pre-emptive override on top of that arbitration.
    self.dlat = NGPDLAT()
    self.dlat_use_laneless = False
    self.dlat_lane_confidence = 1.0
    self.dlp_curves_enabled = self.params.get_bool("ngp_lat_dlp_curves")

  def update(self):
    self.sm.update(15)
    if self.sm.updated["liveCalibration"]:
      self.pose_calibrator.feed_live_calib(self.sm['liveCalibration'])
    if self.sm.updated["livePose"]:
      device_pose = Pose.from_live_pose(self.sm['livePose'])
      self.calibrated_pose = self.pose_calibrator.build_calibrated_pose(device_pose)

  def state_control(self):
    CS = self.sm['carState']

    # Update VehicleModel
    lp = self.sm['liveParameters']
    x = max(lp.stiffnessFactor, 0.1)
    sr = max(lp.steerRatio, 0.1)
    if self.cat is not None:
      if self.sm.updated['liveParameters']:
        self.cat.step(time.monotonic(), live_params_gated(lp, CS), lp.steerRatio, lp.stiffnessFactor, lp.angleOffsetDeg)
      if self.cat.status.adaptive:  # held between liveParameters updates so the model does not flicker
        x, sr = max(self.cat.status.stiffness_factor, 0.1), max(self.cat.status.steer_ratio, 0.1)
    self.VM.update_params(x, sr)

    steer_angle_without_offset = math.radians(CS.steeringAngleDeg - lp.angleOffsetDeg)
    self.curvature = -self.VM.calc_curvature(steer_angle_without_offset, CS.vEgo, lp.roll)

    # Update Torque Params
    if self.CP.lateralTuning.which() == 'torque':
      torque_params = self.sm['liveTorqueParameters']
      if self.sm.all_checks(['liveTorqueParameters']) and torque_params.useParams:
        self.LaC.update_live_torque_params(torque_params.latAccelFactorFiltered, torque_params.latAccelOffsetFiltered,
                                           torque_params.frictionCoefficientFiltered)

    long_plan = self.sm['longitudinalPlan']
    model_v2 = self.sm['modelV2']

    CC = car.CarControl.new_message()
    CC.enabled = self.sm['selfdriveState'].enabled

    # Check which actuators can be enabled
    standstill = abs(CS.vEgo) <= max(self.CP.minSteerSpeed, 0.3) or CS.standstill
    calibrated = self.sm['liveCalibration'].calStatus == log.LiveCalibrationData.Status.calibrated
    gear_ok = CS.gearShifter not in (car.CarState.GearShifter.park,
                                     car.CarState.GearShifter.neutral,
                                     car.CarState.GearShifter.reverse)
    safety_ok = not (CS.steerFaultTemporary or CS.steerFaultPermanent or
                     CS.seatbeltUnlatched or CS.doorOpen)
    alcc_status = self.alcc.update(ALCCInput(
      feature_enabled=self.alcc_enabled,
      engage_request=CS.cruiseState.available,
      user_disable=not CS.cruiseState.available,
      immediate_disable=not safety_ok,
      soft_disable=self.sm['selfdriveState'].state == State.softDisabling,
      pause_condition=standstill and not self.CP.steerAtStandstill,
      steering_override=abs(CS.steeringTorque) > 1.0,
      calibrated=calibrated,
      gear_ok=gear_ok,
      safety_ok=safety_ok,
    ))
    authorized = lateral_authorized(self.sm['pandaStates'], self.CP.safetyConfigs, self.CP.alternativeExperience,
                                    self.sm.valid['pandaStates'] and self.sm.alive['pandaStates']
                                    and time.monotonic() - self.sm.recv_time['pandaStates'] < 0.5)
    self.alcc_active = alcc_status.active_suggestion and alcc_status.available and authorized
    lat_active = self.sm['selfdriveState'].active or self.alcc_active
    CC.latActive = lat_active and not CS.steerFaultTemporary and not CS.steerFaultPermanent and \
                   (not standstill or self.CP.steerAtStandstill)
    if self.blinker_pause is not None and self.blinker_pause.update(CS.vEgo, CS.leftBlinker, CS.rightBlinker):
      CC.latActive = False
    CC.longActive = CC.enabled and not any(e.overrideLongitudinal for e in self.sm['onroadEvents']) and self.CP.openpilotLongitudinalControl

    actuators = CC.actuators
    actuators.longControlState = self.LoC.long_control_state

    # Enable blinkers while lane changing
    if model_v2.meta.laneChangeState != LaneChangeState.off:
      CC.leftBlinker = model_v2.meta.laneChangeDirection == LaneChangeDirection.left
      CC.rightBlinker = model_v2.meta.laneChangeDirection == LaneChangeDirection.right

    if not CC.latActive:
      self.LaC.reset()
    if not CC.longActive:
      self.LoC.reset()

    # accel PID loop
    pid_accel_limits = self.CI.get_pid_accel_limits(self.CP, CS.vEgo, CS.vCruise * CV.KPH_TO_MS)
    actuators.accel = float(self.LoC.update(CC.longActive, CS, long_plan.aTarget, long_plan.shouldStop, pid_accel_limits))

    # DLAT: automatic Laneful/Laneless confidence arbitration, a default
    # always-on behavior of this branch -- no user-selectable mode. DLP curve
    # assist (ngp_lat_dlp_curves) pre-emptively forces laneless on a
    # predicted tight curve, ahead of the ordinary confidence hysteresis.
    dlat_result = self.dlat.update_model(model_v2, v_ego=CS.vEgo, curve_assist_enabled=self.dlp_curves_enabled)
    self.dlat_lane_confidence = dlat_result.lane_confidence
    self.dlat_use_laneless = dlat_result.suggestion is DLATSuggestion.LANELESS

    # Steering PID loop and lateral MPC
    # Reset desired curvature to current to avoid violating the limits on engage
    new_desired_curvature = model_v2.action.desiredCurvature if CC.latActive else self.curvature
    if self.red is not None and CC.latActive:
      new_desired_curvature += curvature_nudge(self.red.update(model_v2, (0.0, 0.0), CS.vEgo, [], self.dlat_use_laneless))
    # Lateral add-ons (SOC, pathd) are proposers merged by the tighten-only arbiter: same-side offsets do not add,
    # opposite sides cancel, and the result stays inside the lane room (nagaspilot/controls/ngp_arbiter.py).
    proposals = []
    if self.soc is not None and CC.latActive:
      left, right = threats_from(CS, model_v2)
      lines = tuple(tuple(line.y) for line in model_v2.laneLines)
      soc_res = self.soc.update(SOCInput(CS.vEgo, left, right, lines, tuple(model_v2.laneLineProbs), tuple(model_v2.laneLineStds)))
      if soc_res.active_suggestion:
        proposals.append(Proposal('soc', soc_res.offset_m))
    if self.pathd_lat is not None:
      fresh = bool(self.sm.alive['pathAdjust'] and self.sm.valid['pathAdjust'])
      allowed = CC.latActive and not (CS.steeringPressed or CS.leftBlinker or CS.rightBlinker or CC.leftBlinker or CC.rightBlinker)
      proposals.append(Proposal('pathd', self.pathd_lat.update(self.sm['pathAdjust'].offsetM, fresh, allowed, DT_CTRL)))
    if proposals:
      new_desired_curvature += arbitrate(proposals, *lane_room(model_v2)).offset_m * BIAS_PER_METER
    new_desired_curvature = apply_curvature(self.rule_consumer, self.sm, new_desired_curvature, CC.latActive,
                                            CS.steeringPressed or CS.brakePressed or CS.gasPressed, DT_CTRL)
    self.desired_curvature, curvature_limited = clip_curvature(CS.vEgo, self.desired_curvature, new_desired_curvature, lp.roll)

    actuators.curvature = self.desired_curvature
    steer, steeringAngleDeg, lac_log = self.LaC.update(CC.latActive, CS, self.VM, lp,
                                                       self.steer_limited_by_safety, self.desired_curvature,
                                                       curvature_limited)  # TODO what if not available
    actuators.torque = float(steer)
    actuators.steeringAngleDeg = float(steeringAngleDeg)
    actuators.torque, actuators.steeringAngleDeg = self.steering_resume_ramp.update(
      CC.latActive, actuators.torque, actuators.steeringAngleDeg, CS.steeringAngleDeg, time.monotonic())
    # Ensure no NaNs/Infs
    for p in ACTUATOR_FIELDS:
      attr = getattr(actuators, p)
      if not isinstance(attr, Number):
        continue

      if not math.isfinite(attr):
        cloudlog.error(f"actuators.{p} not finite {actuators.to_dict()}")
        setattr(actuators, p, 0.0)

    return CC, lac_log

  def publish(self, CC, lac_log):
    CS = self.sm['carState']

    # Orientation and angle rates can be useful for carcontroller
    # Only calibrated (car) frame is relevant for the carcontroller
    CC.currentCurvature = self.curvature
    if self.calibrated_pose is not None:
      CC.orientationNED = self.calibrated_pose.orientation.xyz.tolist()
      CC.angularVelocity = self.calibrated_pose.angular_velocity.xyz.tolist()

    CC.cruiseControl.override = CC.enabled and not CC.longActive and self.CP.openpilotLongitudinalControl
    CC.cruiseControl.cancel = CS.cruiseState.enabled and (not CC.enabled or not self.CP.pcmCruise)
    CC.cruiseControl.resume = CC.enabled and CS.cruiseState.standstill and not self.sm['longitudinalPlan'].shouldStop

    hudControl = CC.hudControl
    hudControl.setSpeed = float(CS.vCruiseCluster * CV.KPH_TO_MS)
    hudControl.speedVisible = CC.enabled
    hudControl.lanesVisible = CC.enabled
    hudControl.leadVisible = self.sm['longitudinalPlan'].hasLead
    hudControl.leadDistanceBars = self.sm['selfdriveState'].personality.raw + 1
    hudControl.visualAlert = self.sm['selfdriveState'].alertHudVisual

    hudControl.rightLaneVisible = True
    hudControl.leftLaneVisible = True
    if self.sm.valid['driverAssistance']:
      hudControl.leftLaneDepart = self.sm['driverAssistance'].leftLaneDeparture
      hudControl.rightLaneDepart = self.sm['driverAssistance'].rightLaneDeparture

    if self.sm['selfdriveState'].active:
      CO = self.sm['carOutput']
      if self.CP.steerControlType == car.CarParams.SteerControlType.angle:
        self.steer_limited_by_safety = abs(CC.actuators.steeringAngleDeg - CO.actuatorsOutput.steeringAngleDeg) > \
                                              STEER_ANGLE_SATURATION_THRESHOLD
      else:
        self.steer_limited_by_safety = abs(CC.actuators.torque - CO.actuatorsOutput.torque) > 1e-2

    # TODO: both controlsState and carControl valids should be set by
    #       sm.all_checks(), but this creates a circular dependency

    # controlsState
    dat = messaging.new_message('controlsState')
    dat.valid = CS.canValid
    cs = dat.controlsState

    cs.curvature = self.curvature
    cs.longitudinalPlanMonoTime = self.sm.logMonoTime['longitudinalPlan']
    cs.lateralPlanMonoTime = self.sm.logMonoTime['modelV2']
    cs.desiredCurvature = self.desired_curvature
    cs.longControlState = self.LoC.long_control_state
    cs.upAccelCmd = float(self.LoC.pid.p)
    cs.uiAccelCmd = float(self.LoC.pid.i)
    cs.ufAccelCmd = float(self.LoC.pid.f)
    cs.forceDecel = monitoring_force_decel(self.sm['driverMonitoringState'].awarenessStatus,
                                         self.sm['selfdriveState'].state == State.softDisabling)
    cs.ngpAlccActive = bool(self.alcc_active)
    cs.ngpDlatUseLaneless = bool(self.dlat_use_laneless)
    cs.ngpDlatLaneConfidence = float(self.dlat_lane_confidence)

    lat_tuning = self.CP.lateralTuning.which()
    if self.CP.steerControlType == car.CarParams.SteerControlType.angle:
      cs.lateralControlState.angleState = lac_log
    elif lat_tuning == 'pid':
      cs.lateralControlState.pidState = lac_log
    elif lat_tuning == 'torque':
      cs.lateralControlState.torqueState = lac_log

    self.pm.send('controlsState', dat)

    # carControl
    cc_send = messaging.new_message('carControl')
    cc_send.valid = CS.canValid
    cc_send.carControl = CC
    self.pm.send('carControl', cc_send)

  def run(self):
    rk = Ratekeeper(100, print_delay_threshold=None)
    while True:
      self.update()
      CC, lac_log = self.state_control()
      self.publish(CC, lac_log)
      rk.monitor_time()


def main():
  config_realtime_process(4, Priority.CTRL_HIGH)
  controls = Controls()
  controls.run()


if __name__ == "__main__":
  main()

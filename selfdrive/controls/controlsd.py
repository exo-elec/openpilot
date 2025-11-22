#!/usr/bin/env python3
import math
from numbers import Number
import time
import json

from cereal import car, log, custom
import cereal.messaging as messaging
from openpilot.common.constants import CV
from openpilot.common.params import Params
from openpilot.common.realtime import config_realtime_process, Priority, Ratekeeper
from openpilot.common.swaglog import cloudlog

from opendbc.car.car_helpers import interfaces
from opendbc.car.vehicle_model import VehicleModel
from openpilot.selfdrive.controls.lib.drive_helpers import clip_curvature
from openpilot.selfdrive.controls.lib.latcontrol import LatControl
from openpilot.selfdrive.controls.lib.latcontrol_pid import LatControlPID
from openpilot.selfdrive.controls.lib.latcontrol_angle import LatControlAngle, STEER_ANGLE_SATURATION_THRESHOLD
from openpilot.selfdrive.controls.lib.latcontrol_torque import LatControlTorque
from openpilot.selfdrive.controls.lib.longcontrol import LongControl
from openpilot.selfdrive.locationd.helpers import PoseCalibrator, Pose
from nagaspilot.selfdrive.controls.lib.np_dlp_controller import NpDlpController
from nagaspilot.selfdrive.controls.lib.np_cat_controller import NpCatController

State = log.SelfdriveState.OpenpilotState
LaneChangeState = log.LaneChangeState
LaneChangeDirection = log.LaneChangeDirection

ACTUATOR_FIELDS = tuple(car.CarControl.Actuators.schema.fields.keys())

_DLP_REASON_CODES: dict[str, int] = {}


def _encode_dlp_reason(reason: str | None) -> int:
  if not reason:
    return 0
  if reason not in _DLP_REASON_CODES:
    _DLP_REASON_CODES[reason] = (len(_DLP_REASON_CODES) + 1) & 0xFFFF
  return _DLP_REASON_CODES[reason]


class Controls:
  def __init__(self) -> None:
    self.params = Params()
    cloudlog.info("controlsd is waiting for CarParams")
    self.CP = messaging.log_from_bytes(self.params.get("CarParams", block=True), car.CarParams)
    cloudlog.info("controlsd got CarParams")

    self.CI = interfaces[self.CP.carFingerprint](self.CP)

    self.sm = messaging.SubMaster(['liveParameters', 'liveTorqueParameters', 'modelV2', 'selfdriveState',
                                   'liveCalibration', 'livePose', 'longitudinalPlan', 'npLongitudinalPlanExt', 'carState', 'carOutput',
                                   'driverMonitoringState', 'onroadEvents', 'driverAssistance'], poll='selfdriveState')
    self.pm = messaging.PubMaster(['carControl', 'controlsState', 'npControlsState'])

    self.steer_limited_by_controls = False
    self.curvature = 0.0
    self.desired_curvature = 0.0

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

    self.dlp_controller = NpDlpController(self.CP, self.params)
    self.dlp_status = None
    self.alcc_active = False
    self.cat_controller = NpCatController(self.CP, self.params)
    self.cat_status = None
    self.cat_enabled_prev = False
    self.cat_enabled = False
    self.last_cat_status_log = 0.0

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
    cat_enabled = self.params.get_bool("np_cat_enable")
    self.cat_enabled = cat_enabled
    self.cat_status = None
    if not cat_enabled and self.cat_enabled_prev:
      self.cat_controller.reset()
    stiffness = lp.stiffnessFactor
    sr = lp.steerRatio
    angle_offset_deg = lp.angleOffsetDeg
    if cat_enabled:
      self.cat_status = self.cat_controller.update(self.sm)
      if self.cat_status.adaptive:
        stiffness = self.cat_status.stiffness_factor
        sr = self.cat_status.steer_ratio
        angle_offset_deg = self.cat_status.angle_offset_deg
    x = max(stiffness, 0.1)
    sr = max(sr, 0.1)
    self.VM.update_params(x, sr)

    steer_angle_without_offset = math.radians(CS.steeringAngleDeg - angle_offset_deg)
    self.curvature = -self.VM.calc_curvature(steer_angle_without_offset, CS.vEgo, lp.roll)
    self.cat_enabled_prev = cat_enabled

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
    base_lat_allowed = self.sm['selfdriveState'].active
    self.dlp_status = None
    alcc_enabled = self.params.get_bool("np_alcc_enable")
    if self.dlp_controller.enabled:
      try:
        self.dlp_status = self.dlp_controller.update(self.sm, base_lat_allowed, CS.vEgo, base_lat_allowed, standstill)
      except Exception as e:
        cloudlog.exception(f"DLP controller update failed: {e}")
        self.dlp_status = None
    dlp_available = self.dlp_status is not None and self.dlp_status.available
    self.alcc_active = alcc_enabled and dlp_available

    lat_active = base_lat_allowed or self.alcc_active
    CC.latActive = lat_active and not CS.steerFaultTemporary and not CS.steerFaultPermanent and \
                   (not standstill or self.CP.steerAtStandstill)
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

    # Steering PID loop and lateral MPC
    # Reset desired curvature to current to avoid violating the limits on engage
    new_desired_curvature = model_v2.action.desiredCurvature if CC.latActive else self.curvature
    self.desired_curvature, curvature_limited = clip_curvature(CS.vEgo, self.desired_curvature, new_desired_curvature, lp.roll)

    actuators.curvature = self.desired_curvature
    steer, steeringAngleDeg, lac_log = self.LaC.update(CC.latActive, CS, self.VM, lp,
                                                       self.steer_limited_by_controls, self.desired_curvature,
                                                       curvature_limited)  # TODO what if not available
    actuators.torque = float(steer)
    actuators.steeringAngleDeg = float(steeringAngleDeg)
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
        self.steer_limited_by_controls = abs(CC.actuators.steeringAngleDeg - CO.actuatorsOutput.steeringAngleDeg) > \
                                              STEER_ANGLE_SATURATION_THRESHOLD
      else:
        self.steer_limited_by_controls = abs(CC.actuators.torque - CO.actuatorsOutput.torque) > 1e-2

    # TODO: both controlsState and carControl valids should be set by
    #       sm.all_checks(), but this creates a circular dependency

    # npControlsState
    dat = messaging.new_message('npControlsState')
    dat.valid = True
    ncs = dat.npControlsState
    ncs.alccActive = self.alcc_active
    if self.dlp_status is not None:
      ncs.dlpMode = int(self.dlp_status.mode.value)
      ncs.dlpLcaMode = int(self.dlp_status.lca_mode.value)
      ncs.dlpActive = self.dlp_status.active
      ncs.dlpAvailable = self.dlp_status.available
      ncs.dlpConfidence = float(self.dlp_status.confidence)
      ncs.dlpDesire = int(self.dlp_status.desire)
      ncs.dlpReasonCode = _encode_dlp_reason(self.dlp_status.reason)
    else:
      ncs.dlpMode = 0
      ncs.dlpLcaMode = 0
      ncs.dlpActive = False
      ncs.dlpAvailable = False
      ncs.dlpConfidence = 0.0
      ncs.dlpDesire = 0
      ncs.dlpReasonCode = 0
    ncs.dlpActive = ncs.dlpActive or self.alcc_active
    ncs.dlpAvailable = ncs.dlpAvailable or self.alcc_active
    if self.cat_status is not None:
      ncs.catAdaptive = self.cat_status.adaptive
      ncs.catConfidence = float(self.cat_status.confidence)
      ncs.catSteerRatio = float(self.cat_status.steer_ratio)
      ncs.catStiffnessFactor = float(self.cat_status.stiffness_factor)
      ncs.catSamples = int(self.cat_status.samples)
      ncs.catManualOverride = self.cat_status.note == "manual_sr"
    else:
      ncs.catAdaptive = False
      ncs.catConfidence = 0.0
      ncs.catSteerRatio = float(self.CP.steerRatio)
      ncs.catStiffnessFactor = 1.0
      ncs.catSamples = 0
      ncs.catManualOverride = False
    # TSC telemetry from npLongitudinalPlanExt
    if self.sm.valid['npLongitudinalPlanExt']:
      lpext = self.sm['npLongitudinalPlanExt'].longitudinalPlanExt
      ncs.tscActive = lpext.visionTurnControllerState != custom.LongitudinalPlanExt.VisionTurnControllerState.disabled
      ncs.tscState = int(lpext.visionTurnControllerState)
      ncs.tscVisionSpeed = float(lpext.visionTurnSpeed)
      ncs.tscMapSpeed = float(lpext.mapTurnSpeed)
      ncs.tscMapStale = getattr(lpext, "mapDataStale", False)
    else:
      ncs.tscActive = False
      ncs.tscState = 0
      ncs.tscVisionSpeed = 0.0
      ncs.tscMapSpeed = 0.0
      ncs.tscMapStale = False
    # DEM telemetry from planner (health score)
    if hasattr(self, "planner"):
      try:
        ncs.demActive = bool(self.planner.dem_active)
        ncs.demEngagedPercent = float(self.planner.dem_health_score)
      except Exception:
        ncs.demActive = False
        ncs.demEngagedPercent = 0.0
    else:
      ncs.demActive = False
      ncs.demEngagedPercent = 0.0
    self.pm.send('npControlsState', dat)

    # Publish CAT + stack status for UI/debug (Params throttled to ~1 Hz)
    now = time.monotonic()
    if now - self.last_cat_status_log > 1.0:
      status_payload = {
        "enabled": self.cat_enabled,
        "adaptive": bool(self.cat_status.adaptive) if self.cat_status else False,
        "confidence": float(self.cat_status.confidence) if self.cat_status else 0.0,
        "steerRatio": float(self.cat_status.steer_ratio) if self.cat_status else float(self.CP.steerRatio),
        "stiffnessFactor": float(self.cat_status.stiffness_factor) if self.cat_status else 1.0,
        "angleOffsetDeg": float(self.cat_status.angle_offset_deg) if self.cat_status else 0.0,
        "samples": int(self.cat_status.samples) if self.cat_status else 0,
        "manualOverride": bool(self.cat_status.note == "manual_sr") if self.cat_status else False,
        "note": self.cat_status.note if self.cat_status else ("disabled" if not self.cat_enabled else "idle"),
      }
      try:
        self.params.put_nonblocking("np_cat_status", json.dumps(status_payload))
      except Exception as e:
        cloudlog.exception(f"Failed to write np_cat_status: {e}")
      stack_payload = {
        "dlp": {
          "available": bool(ncs.dlpAvailable),
          "active": bool(ncs.dlpActive),
          "confidence": float(ncs.dlpConfidence),
          "mode": int(ncs.dlpMode),
          "lcaMode": int(ncs.dlpLcaMode),
          "reasonCode": int(ncs.dlpReasonCode),
        },
        "tsc": {
          "active": bool(ncs.tscActive),
          "state": int(ncs.tscState),
          "visionSpeed": float(ncs.tscVisionSpeed),
          "mapSpeed": float(ncs.tscMapSpeed),
          "mapStale": bool(ncs.tscMapStale),
        },
        "dem": {
          "active": bool(ncs.demActive),
          "health": float(ncs.demEngagedPercent),
        },
      }
      try:
        self.params.put_nonblocking("np_stack_status", json.dumps(stack_payload))
      except Exception as e:
        cloudlog.exception(f"Failed to write np_stack_status: {e}")
      self.last_cat_status_log = now

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
    cs.forceDecel = bool((self.sm['driverMonitoringState'].awarenessStatus < 0.) or
                         (self.sm['selfdriveState'].state == State.softDisabling))

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

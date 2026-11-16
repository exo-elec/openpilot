#!/usr/bin/env python3
import math
import time
from numbers import Number
from typing import Any

from cereal import car, log
import cereal.messaging as messaging
from openpilot.common.constants import CV
from openpilot.common.params import Params
from openpilot.common.realtime import config_realtime_process, Priority, Ratekeeper, DT_CTRL
from openpilot.common.core_config import set_daemon_affinity
from openpilot.common.swaglog import cloudlog

from openpilot.system.socketd.vehicle.car.vehicle_model import VehicleModel
from openpilot.selfdrive.controls.lib.drive_helpers import clip_curvature
from openpilot.selfdrive.controls.lib.latcontrol import LatControl
from openpilot.selfdrive.controls.lib.latcontrol_pid import LatControlPID
from openpilot.selfdrive.controls.lib.latcontrol_angle import LatControlAngle, STEER_ANGLE_SATURATION_THRESHOLD
from openpilot.selfdrive.controls.lib.latcontrol_torque import LatControlTorque
from openpilot.selfdrive.controls.lib.longcontrol import LongControl
from exopilot.selfdrive.controls.lib.cat import CAT
from exopilot.selfdrive.controls.lib.blinker_pause import BlinkerPause
from exopilot.selfdrive.controls.lib.driver_activity import monitoring_force_decel
from exopilot.selfdrive.controls.lib.dlat import DLAT, DLATSuggestion
from exopilot.selfdrive.controls.lib.red import RED, curvature_nudge
from exopilot.selfdrive.controls.lib.alcc import ALCCInput, ALCC, BrakeMode
from exopilot.selfdrive.controls.lib.lateral_authorization import lateral_authorized
from openpilot.selfdrive.controls.lib.radar_zones import RadarZoneMonitor, ZoneSide
from openpilot.selfdrive.controls.lib.aeb import AEB
from openpilot.selfdrive.locationd.helpers import PoseCalibrator, Pose
from exopilot.selfdrive.controls.lib.steering_policy import SteeringResumeRamp
from exopilot.selfdrive.controls.lib.rule_channel import RuleChannelConsumer, apply_curvature

State = log.SelfdriveState.OpenpilotState
LaneChangeState = log.LaneChangeState
ButtonType = car.CarState.ButtonEvent.Type
ALCC_ENGAGE_BUTTONS = (ButtonType.lkas, ButtonType.setCruise, ButtonType.resumeCruise)
LaneChangeDirection = log.LaneChangeDirection

ACTUATOR_FIELDS = tuple(car.CarControl.Actuators.schema.fields.keys())

# Ordinary cruise/following authority. This is a behavior limit based on the
# real-world comfort band, not an arbitrary percentage of a hardware range.
# AEB is applied later and may cross this boundary only from a confirmed lead
# measured by the built-in forward 77 GHz radar.
NORMAL_BRAKE_LIMIT_MS2 = -2.5


class Controls:
  def __init__(self) -> None:
    self.params = Params()
    cloudlog.info("controlsd is waiting for CarParams")
    _car_params = self.params.get("CarParams")
    assert _car_params is not None, "CarParams not available"
    self.CP = messaging.log_from_bytes(_car_params, car.CarParams)
    cloudlog.info("controlsd got CarParams")

    # Tesla-only: No need for generic interface lookup
    self.CI = None  # Replaced by vehicled daemon

    self.sm = messaging.SubMaster(['liveParameters', 'liveTorqueParameters', 'modelV2', 'selfdriveState',
                                   'liveCalibration', 'livePose', 'longitudinalPlan', 'carState', 'carOutput',
                                   'driverMonitoringState', 'onroadEvents', 'driverAssistance', 'pandaStates',
                                   'enhancedTrajectory',  # EOP: pathd
                                   'surfaceStatus', 'radarState',
                                   'monoDetections', 'stereoDetections', 'stereoObjects', 'pathAdjust',
                                   'sideDetections', 'rearDetections',  # EOP: camera fallback for zone monitor
                                   'gridObjects',  # EOP: fused drivable layer for the zone monitor's off-road check
                                   'adaptiveDrivingState'],  # EOP: adaptd
                                  poll='selfdriveState')
    self.pm = messaging.PubMaster(['carControl', 'controlsState', 'blindSpotAlert', 'ttsRequest', 'alccState'])

    self.steer_limited_by_safety = False
    self.curvature = 0.0
    self.desired_curvature = 0.0

    self.pose_calibrator = PoseCalibrator()
    self.calibrated_pose: Pose | None = None

    self.LoC = LongControl(self.CP)
    self.VM = VehicleModel(self.CP)

    # EOP: CAT (Car Adaptive Tuning) — smoothed steer ratio / stiffness learning
    self.cat = CAT(self.CP)
    # Blinker pause (BlinkerPauseLateralSpeed, 0 = off): no lateral assistance with a signal on below this speed
    pause_mph = self.params.get("BlinkerPauseLateralSpeed", return_default=True)
    self.blinker_pause = BlinkerPause(float(pause_mph) * CV.MPH_TO_MS) if pause_mph else None

    # DLAT (Dynamic Lateral Profile): the shared exopilot.selfdrive.controls.lib.dlat, same as NGP10
    self.dlat = DLAT()
    self.dlat_use_laneless = False
    self.dlat_mode = "Auto"
    self.dlat_state = "laneful"
    self.dlat_lane_confidence = 1.0

    # RED (Road Edge Detection): the shared one; RedControllerEnabled re-read every 2 s
    self.red = RED(self.params.get_bool)

    # EOP: radar zone monitor — adjacent-lane + rear threat assessment
    self.zone_monitor = RadarZoneMonitor()

    # EOP: AEB (Autonomous Emergency Braking)
    self.aeb = AEB()
    self.rule_consumer = RuleChannelConsumer()

    # ALCC (LatALCC): the shared state machine, as NGP10; ALCCBrakeMode says what a brake press does
    self.alcc_enabled = self.params.get_bool("LatALCC")
    self.alcc = ALCC(BrakeMode.from_param(self.params.get("ALCCBrakeMode", return_default=True)))
    self.alcc_status = None
    self.alcc_active = False

    # EOP: TJA resume alert debounce
    self._tja_resume_alerted = False

    self.steering_resume_ramp = SteeringResumeRamp()

    # EOP-CLEANUP: Cached params — refreshed once per second, not every frame
    self._param_refresh_s = 1.0
    self._last_param_t = 0.0
    self._cached_feature_params: dict[str, Any] = {}

    self.LaC: LatControl
    if self.CP.steerControlType == car.CarParams.SteerControlType.angle:
      # Tesla uses angle-based steering control
      self.LaC = LatControlAngle(self.CP, None)
    elif self.CP.lateralTuning.which() == 'pid':
      self.LaC = LatControlPID(self.CP, None)
    elif self.CP.lateralTuning.which() == 'torque':
      self.LaC = LatControlTorque(self.CP, None)

  def update(self):
    self.sm.update(15)
    if self.sm.updated["liveCalibration"]:
      self.pose_calibrator.feed_live_calib(self.sm['liveCalibration'])
    if self.sm.updated["livePose"]:
      device_pose = Pose.from_live_pose(self.sm['livePose'])
      self.calibrated_pose = self.pose_calibrator.build_calibrated_pose(device_pose)

  def state_control(self):
    CS = self.sm['carState']

    # Update VehicleModel — apply CAT corrections if confident
    lp = self.sm['liveParameters']
    cat_status = self.cat.update(self.sm)
    if cat_status.adaptive:
      x  = max(cat_status.stiffness_factor, 0.1)
      sr = max(cat_status.steer_ratio, 0.1)
    else:
      x  = max(lp.stiffnessFactor, 0.1)
      sr = max(lp.steerRatio, 0.1)
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

    # EOP-CLEANUP: Refresh cached params once per second
    now = time.monotonic()
    if now - self._last_param_t >= self._param_refresh_s:
      self._last_param_t = now
      self._cached_feature_params = {
        'BSDEnabled': self.params.get_bool("BSDEnabled"),
        'BSDMinSpeed': float(self.params.get("BSDMinSpeed") or 5.5),
        'BSDChimeEnabled': self.params.get_bool("BSDChimeEnabled"),
        'TTSAlertsEnabled': self.params.get_bool("TTSAlertsEnabled"),
        'AEBEnabled': self.params.get_bool("AEBEnabled"),
        'DLPCurvesEnabled': self.params.get_bool("DLPCurvesEnabled"),
      }
    p = self._cached_feature_params

    # EOP: radar zone monitor — advisory only, never intervenes in steering/braking
    # Camera-only operation remains available when a BLE corner is absent.
    # Feature-specific speed/gear gates are owned by RadarZoneMonitor so
    # low-speed cross-traffic warnings are not disabled by the BSD threshold.
    if p.get('BSDEnabled'):
      if self.sm.updated['gridObjects'] and self.sm.valid.get('gridObjects'):
        self.zone_monitor.cache_drivable(self.sm['gridObjects'], now)
      self.zone_monitor.update(
        stereo_objects_msg=self.sm['stereoObjects'] if self.sm.valid.get('stereoObjects') else None,
        carstate=CS,
        side_detections_msg=self.sm['sideDetections'] if self.sm.valid.get('sideDetections') else None,
        rear_detections_msg=self.sm['rearDetections'] if self.sm.valid.get('rearDetections') else None,
        t=now,
      )
      zm = self.zone_monitor

      zone_msg = messaging.new_message('blindSpotAlert')
      cross_threats = (zm.fcta_state, zm.rcta_state)
      left_cross = [threat for threat in cross_threats if threat.detected and threat.side == ZoneSide.LEFT]
      right_cross = [threat for threat in cross_threats if threat.detected and threat.side == ZoneSide.RIGHT]
      left_level = max([zm.left_state.alert_level.value] + [threat.alert_level.value for threat in left_cross])
      right_level = max([zm.right_state.alert_level.value] + [threat.alert_level.value for threat in right_cross])
      left_primary = min(left_cross, key=lambda threat: threat.ttc_s) if left_cross else None
      right_primary = min(right_cross, key=lambda threat: threat.ttc_s) if right_cross else None

      zone_msg.blindSpotAlert.leftAlertLevel  = left_level
      zone_msg.blindSpotAlert.rightAlertLevel = right_level
      zone_msg.blindSpotAlert.leftDetected    = zm.left_state.detected or bool(left_cross)
      zone_msg.blindSpotAlert.rightDetected   = zm.right_state.detected or bool(right_cross)
      zone_msg.blindSpotAlert.leftDistance    = left_primary.distance_m if left_primary else zm.left_state.distance_m
      zone_msg.blindSpotAlert.rightDistance   = right_primary.distance_m if right_primary else zm.right_state.distance_m
      zone_msg.blindSpotAlert.leftRelativeSpeed  = left_primary.vRel_ms if left_primary else zm.left_state.vRel_ms
      zone_msg.blindSpotAlert.rightRelativeSpeed = right_primary.vRel_ms if right_primary else zm.right_state.vRel_ms
      zone_msg.blindSpotAlert.rearCrossTrafficDetected     = zm.rcta_state.detected
      zone_msg.blindSpotAlert.rearCrossTrafficAlertLevel   = zm.rcta_state.alert_level.value
      zone_msg.blindSpotAlert.rearCrossTrafficDistance     = zm.rcta_state.distance_m
      zone_msg.blindSpotAlert.rearCrossTrafficRelativeSpeed = zm.rcta_state.vRel_ms
      zone_msg.blindSpotAlert.alertMessage   = zm.alert_message() or ""
      # Wide TTC-based LCA gate (reads radar3d far-range adjacent objects)
      zone_msg.blindSpotAlert.lcaBlockedLeft  = zm.lca_blocked_left
      zone_msg.blindSpotAlert.lcaBlockedRight = zm.lca_blocked_right

      should_chime, _ = zm.chime_request(now)
      zone_msg.blindSpotAlert.chimeRequest = should_chime and p.get('BSDChimeEnabled', False)

      if should_chime and p.get('TTSAlertsEnabled', False):
        alert_txt = zm.alert_message()
        if alert_txt:
          tts_msg = messaging.new_message('ttsRequest')
          tts_msg.ttsRequest.text = alert_txt
          tts_msg.ttsRequest.priority = 1
          tts_msg.ttsRequest.interrupt = True
          self.pm.send('ttsRequest', tts_msg)

      self.pm.send('blindSpotAlert', zone_msg)

    CC = car.CarControl.new_message()
    CC.enabled = self.sm['selfdriveState'].enabled

    # DLAT: automatic Laneful/Laneless arbitration (DLP curve assist behind DLPCurvesEnabled)
    dlat_result = self.dlat.update_model(model_v2, v_ego=CS.vEgo, curve_assist_enabled=p.get('DLPCurvesEnabled', True))
    self.dlat_use_laneless = dlat_result.suggestion is DLATSuggestion.LANELESS
    self.dlat_state = dlat_result.suggestion.value
    self.dlat_lane_confidence = float(dlat_result.lane_confidence)

    # RED (Road Edge Detection): only active in laneless mode
    red_output = self.red.update(model_v2, (0.0, 0.0), CS.vEgo, [], self.dlat_use_laneless)

    # ALCC: the shared state machine, as NGP10. It never steers while the driver overrides and needs the
    # Panda's live authorization.
    standstill = abs(CS.vEgo) <= max(self.CP.minSteerSpeed, 0.3) or CS.standstill
    calibrated = self.sm['liveCalibration'].calStatus == log.LiveCalibrationData.Status.calibrated
    gear_ok = CS.gearShifter not in (car.CarState.GearShifter.park,
                                     car.CarState.GearShifter.neutral,
                                     car.CarState.GearShifter.reverse)
    safety_ok = not (CS.steerFaultTemporary or CS.steerFaultPermanent or
                     CS.seatbeltUnlatched or CS.doorOpen)
    self.alcc_status = self.alcc.update(ALCCInput(
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
      brake_pressed=CS.brakePressed,
      engage_button=any(be.pressed and be.type in ALCC_ENGAGE_BUTTONS for be in CS.buttonEvents),
    ))
    authorized = lateral_authorized(self.sm['pandaStates'], self.CP.safetyConfigs, self.CP.alternativeExperience,
                                    self.sm.valid['pandaStates'] and self.sm.alive['pandaStates']
                                    and time.monotonic() - self.sm.recv_time['pandaStates'] < 0.5)
    self.alcc_active = self.alcc_status.active_suggestion and self.alcc_status.available and authorized
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
    # Tesla-only actuator envelope. Keep ordinary cruise/following inside the
    # comfort boundary; the built-in-radar AEB path is applied separately below.
    # CarParams has no accelMin/accelMax (neither the old fork nor opendbc defines them), so this always raised
    # AttributeError; the Tesla envelope below is what the expression fell back to.
    pid_accel_limits = (-3.48, 2.0)
    pid_accel_limits = (max(pid_accel_limits[0], NORMAL_BRAKE_LIMIT_MS2), pid_accel_limits[1])

    # EOP: adaptd adaptive driving — clamp accel/decel limits from OBD telemetry
    if self.sm.valid.get('adaptiveDrivingState', False):
      ads = self.sm['adaptiveDrivingState']
      if ads.enabled:
        ads_accel_max = float(ads.accelMax)
        ads_decel_max = float(ads.decelMax)
        # Only clamp if values are sane (non-zero, finite)
        if 0.0 < ads_accel_max < 10.0 and 0.0 < ads_decel_max < 10.0:
          pid_accel_limits = (
            max(pid_accel_limits[0], -ads_decel_max),
            min(pid_accel_limits[1], ads_accel_max),
          )

    actuators.accel = float(self.LoC.update(CC.longActive, CS, long_plan.aTarget, long_plan.shouldStop, pid_accel_limits))

    # EOP: TJA resume required alert (soundd TTS)
    if self.LoC.tja_resume_required and not self._tja_resume_alerted:
      tts_msg = messaging.new_message('ttsRequest')
      tts_msg.ttsRequest.text = "Traffic Jam Assist hold expired. Please resume manually."
      tts_msg.ttsRequest.priority = 1  # high
      tts_msg.ttsRequest.interrupt = True
      self.pm.send('ttsRequest', tts_msg)
      self._tja_resume_alerted = True
    elif not self.LoC.tja_resume_required:
      self._tja_resume_alerted = False

    # EOP: AEB - Autonomous Emergency Braking
    if p.get('AEBEnabled', False) and CC.longActive:
      aeb_result = self.aeb.update(self.sm, CS)
      if aeb_result.is_active and aeb_result.target_decel < 0 and math.isfinite(aeb_result.target_decel):
        # AEB requests emergency braking - clamp acceleration
        actuators.accel = min(actuators.accel, aeb_result.target_decel)
        cloudlog.warning(f"AEB active: decel={aeb_result.target_decel:.1f}m/s², TTC={aeb_result.ttc:.2f}s, reason={aeb_result.reason}")

    # EOP: pathd collision avoidance — clamp accel based on enhancedTrajectory
    if CC.longActive and self.sm.valid.get('enhancedTrajectory', False):
      et = self.sm['enhancedTrajectory']
      alert = et.trajectoryAlertLevel
      if alert == "critical":
        # Camera/path perception has no AEB authority. It may request firm but
        # comfortable mitigation only; crossing the boundary is reserved for
        # the confirmed built-in-radar AEB path above.
        actuators.accel = min(actuators.accel, NORMAL_BRAKE_LIMIT_MS2)
      elif alert == "warning" and et.speedAdjustment and et.speedAdjustment[0] < -0.1:
        # speedAdjustment is m/s² (pathd divides Δv by ACCEL_RESPONSE_TIME=1.0s)
        actuators.accel = min(actuators.accel, max(et.speedAdjustment[0], -2.0))

    # Steering PID loop and lateral MPC
    # Reset desired curvature to current to avoid violating the limits on engage
    new_desired_curvature = model_v2.action.desiredCurvature if CC.latActive else self.curvature
    # shared parallel rule channel (pathadjustd + DPP): policy curvature unless DPP gave it lateral authority
    new_desired_curvature = apply_curvature(self.rule_consumer, self.sm, new_desired_curvature, CC.latActive,
                                            CS.steeringPressed or CS.brakePressed or CS.gasPressed, DT_CTRL)

    # RED: push away from a close road edge (zero unless laneless and an edge is within 1 m)
    new_desired_curvature += curvature_nudge(red_output)

    self.desired_curvature, curvature_limited = clip_curvature(CS.vEgo, self.desired_curvature, new_desired_curvature, lp.roll)

    actuators.curvature = self.desired_curvature
    if hasattr(self.LaC, 'set_model_data'):
      self.LaC.set_model_data(self.sm['modelV2'])
    steer, steeringAngleDeg, lac_log = self.LaC.update(CC.latActive, CS, self.VM, lp,
                                                       self.steer_limited_by_safety, self.desired_curvature,
                                                       curvature_limited)  # TODO what if not available
    actuators.torque = float(steer)
    actuators.steeringAngleDeg = float(steeringAngleDeg)

    # Smooth torque/angle authority after lateral control resumes.
    actuators.torque, actuators.steeringAngleDeg = self.steering_resume_ramp.update(
      CC.latActive, actuators.torque, actuators.steeringAngleDeg, CS.steeringAngleDeg, time.monotonic())

    # Ensure no NaNs/Infs
    for field_name in ACTUATOR_FIELDS:
      attr = getattr(actuators, field_name)
      if not isinstance(attr, Number):
        continue
      if not math.isfinite(attr):
        cloudlog.error(f"actuators.{field_name} not finite {actuators.to_dict()}")
        setattr(actuators, field_name, 0.0)

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

    # EOP: DLAT debug info
    cs.dlatMode = self.dlat_mode
    cs.dlatState = self.dlat_state
    cs.alccActive = bool(self.alcc_active)
    cs.dlatLaneConfidence = self.dlat_lane_confidence
    cs.dlatUseLaneless = self.dlat_use_laneless

    # EOP: radar zone monitor state (for UI overlay)
    cs.leftBlindSpot  = self.zone_monitor.left_state.alert_level.value
    cs.rightBlindSpot = self.zone_monitor.right_state.alert_level.value

    cs.upAccelCmd = float(self.LoC.pid.p)
    cs.uiAccelCmd = float(self.LoC.pid.i)
    cs.ufAccelCmd = float(self.LoC.pid.f)
    cs.forceDecel = monitoring_force_decel(self.sm['driverMonitoringState'].awarenessStatus,
                                         self.sm['selfdriveState'].state == State.softDisabling)

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

    # EOP: alccState (Topic 01) — ALCC state for UI / logging
    alcc_send = messaging.new_message('alccState')
    alcc_send.valid = True
    a = alcc_send.alccState
    if self.alcc_status is not None:
      a.state = int(self.alcc_status.state)
      a.enabled = self.alcc_status.latched
      a.active = self.alcc_active
      a.available = self.alcc_status.available
    self.pm.send('alccState', alcc_send)

  def run(self):
    rk = Ratekeeper(100, print_delay_threshold=None)
    while True:
      self.update()
      CC, lac_log = self.state_control()
      self.publish(CC, lac_log)
      rk.monitor_time()


def main() -> int:
  try:
    set_daemon_affinity("controlsd")
    config_realtime_process(DT_CTRL, Priority.CTRL_HIGH)
    controls = Controls()
    controls.run()
    return 0
  except Exception as e:
    cloudlog.exception(f"ControlsD fatal error: {e}")
    raise


if __name__ == "__main__":
  exit(main())

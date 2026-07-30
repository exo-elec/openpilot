#!/usr/bin/env python3
import math
import time
from numbers import Number

from cereal import car, log
import cereal.messaging as messaging
from openpilot.common.constants import CV
from openpilot.common.params import Params
from openpilot.common.realtime import config_realtime_process, Priority, Ratekeeper, DT_CTRL
from openpilot.common.core_config import set_daemon_affinity
from openpilot.common.swaglog import cloudlog

from openpilot.selfdrive.vehicled.car.vehicle_model import VehicleModel
from openpilot.selfdrive.controls.lib.drive_helpers import clip_curvature
from openpilot.selfdrive.controls.lib.latcontrol import LatControl
from openpilot.selfdrive.controls.lib.latcontrol_pid import LatControlPID
from openpilot.selfdrive.controls.lib.latcontrol_angle import LatControlAngle, STEER_ANGLE_SATURATION_THRESHOLD
from openpilot.selfdrive.controls.lib.latcontrol_torque import LatControlTorque
from openpilot.selfdrive.controls.lib.longcontrol import LongControl
from openpilot.selfdrive.controls.lib.cat import CAT
from openpilot.selfdrive.controls.lib.dlat import DLAT
from openpilot.selfdrive.controls.lib.red import RED
from openpilot.selfdrive.controls.lib.alcc import AlccController, AlccStatus
from openpilot.selfdrive.controls.lib.radar_zones import RadarZoneMonitor, ZoneAlertLevel
from openpilot.selfdrive.controls.lib.aeb import AEB
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

    # Tesla-only: No need for generic interface lookup
    self.CI = None  # Replaced by vehicled daemon

    self.sm = messaging.SubMaster(['liveParameters', 'liveTorqueParameters', 'modelV2', 'selfdriveState',
                                   'liveCalibration', 'livePose', 'longitudinalPlan', 'carState', 'carOutput',
                                   'onroadEvents', 'driverAssistance',
                                   'enhancedTrajectory',  # EOP: pathd
                                   'surfaceStatus', 'radarState',
                                   'monoDetections', 'stereoDetections', 'stereoObjects',
                                   'sideDetections', 'rearDetections',  # EOP: camera fallback for zone monitor
                                   'radar4d',  # EOP: weather severity for AEB margins
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

    # EOP: DLAT (Dynamic Lateral Profile)
    self.dlat = DLAT()
    self.dlat_use_laneless = False
    self.dlat_mode = 0
    self.dlat_state = 0

    # EOP: RED (Road Edge Detection)
    self.red = RED()

    # EOP: radar zone monitor — adjacent-lane + rear threat assessment
    self.zone_monitor = RadarZoneMonitor()

    # EOP: AEB (Autonomous Emergency Braking)
    self.aeb = AEB()

    # EOP: ALCC (Always Lane Centering Control)
    self.alcc = AlccController(self.CP)
    self.alcc_status = AlccStatus()  # default-initialised; overwritten on first frame

    # EOP: per-frame state needed by ALCC
    self.CS_prev = car.CarState.new_message()  # zeroed CarState; safe on first frame
    self.events = []
    self.disengage_on_accelerator = self.params.get_bool("DisengageOnAccelerator")

    # EOP: TJA resume alert debounce
    self._tja_resume_alerted = False

    # EOP-CLEANUP: Cached params — refreshed once per second, not every frame
    self._param_refresh_s = 1.0
    self._last_param_t = 0.0
    self._cached_eop_params = {}

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
      self._cached_eop_params = {
        'EOPBSDEnabled': self.params.get_bool("EOPBSDEnabled"),
        'EOPBSDMinSpeed': float(self.params.get("EOPBSDMinSpeed") or 5.5),
        'EOPBSDChimeEnabled': self.params.get_bool("EOPBSDChimeEnabled"),
        'EOPTTSAlertsEnabled': self.params.get_bool("EOPTTSAlertsEnabled"),
        'EOPAEBEnabled': self.params.get_bool("EOPAEBEnabled"),
      }
    p = self._cached_eop_params

    # EOP: radar zone monitor — advisory only, never intervenes in steering/braking
    # Reads stereoObjects (gridd-fused: radar4d velocity + radar2d zones + camera)
    if p.get('EOPBSDEnabled') and CS.vEgo > p.get('EOPBSDMinSpeed', 5.5):
      self.zone_monitor.update(
        stereo_objects_msg=self.sm['stereoObjects'] if self.sm.valid.get('stereoObjects') else None,
        carstate=CS,
        side_detections_msg=self.sm['sideDetections'] if self.sm.valid.get('sideDetections') else None,
        rear_detections_msg=self.sm['rearDetections'] if self.sm.valid.get('rearDetections') else None,
        t=now,
      )
      zm = self.zone_monitor

      zone_msg = messaging.new_message('blindSpotAlert')
      zone_msg.blindSpotAlert.leftAlertLevel  = zm.left_state.alert_level.value
      zone_msg.blindSpotAlert.rightAlertLevel = zm.right_state.alert_level.value
      zone_msg.blindSpotAlert.leftDetected    = zm.left_state.detected
      zone_msg.blindSpotAlert.rightDetected   = zm.right_state.detected
      zone_msg.blindSpotAlert.leftDistance    = zm.left_state.distance_m
      zone_msg.blindSpotAlert.rightDistance   = zm.right_state.distance_m
      zone_msg.blindSpotAlert.leftRelativeSpeed  = zm.left_state.vRel_ms
      zone_msg.blindSpotAlert.rightRelativeSpeed = zm.right_state.vRel_ms
      zone_msg.blindSpotAlert.rearCrossTrafficDetected     = zm.rear_state.detected
      zone_msg.blindSpotAlert.rearCrossTrafficAlertLevel   = zm.rear_state.alert_level.value
      zone_msg.blindSpotAlert.rearCrossTrafficDistance     = zm.rear_state.distance_m
      zone_msg.blindSpotAlert.rearCrossTrafficRelativeSpeed = zm.rear_state.vRel_ms
      zone_msg.blindSpotAlert.alertMessage   = zm.alert_message() or ""
      # Wide TTC-based LCA gate (reads radar3d far-range adjacent objects)
      zone_msg.blindSpotAlert.lcaBlockedLeft  = zm.lca_blocked_left
      zone_msg.blindSpotAlert.lcaBlockedRight = zm.lca_blocked_right

      should_chime, _ = zm.chime_request(now)
      zone_msg.blindSpotAlert.chimeRequest = should_chime and p.get('EOPBSDChimeEnabled', False)

      if should_chime and p.get('EOPTTSAlertsEnabled', False):
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

    # EOP: DLAT - Update lateral profile mode
    self.dlat_use_laneless, self.dlat_mode, self.dlat_state = self.dlat.update(model_v2, CS)

    # EOP: RED (Road Edge Detection) - only active in laneless mode
    # Get YOLO detections from gridd if available
    yolo_detections = None
    stereo_data = None
    if self.sm.valid.get('enhancedTrajectory', False):
      et = self.sm['enhancedTrajectory']
      # gridd provides detections via enhancedTrajectory
      yolo_detections = getattr(et, 'detections', None)
      stereo_data = getattr(et, 'stereoData', None)

    red_output = self.red.update(
      model_v2=model_v2,
      yolo_detections=yolo_detections,
      stereo_data=stereo_data,
      vehicle_position=(0.0, 0.0),  # Relative to path center
      v_ego=CS.vEgo,
      planned_path=[],  # Path points from lateral planner
      is_laneless=self.dlat_use_laneless
    )

    # EOP: Enhanced ALCC (Always Lane Centering Control)
    calibrated = self.sm['liveCalibration'].calStatus == log.LiveCalibrationData.Status.calibrated
    gear_ok = CS.gearShifter not in (car.CarState.GearShifter.park, car.CarState.GearShifter.neutral, car.CarState.GearShifter.reverse)
    safety_ok = not CS.seatbeltUnlatched and not CS.doorOpen

    alcc_status = self.alcc.update(
      CS=CS, CS_prev=self.CS_prev,
      events=self.events,
      panda_states=self.sm['pandaStates'] if self.sm.valid.get('pandaStates', False) else [],
      stock_enabled=self.sm['selfdriveState'].enabled,
      stock_active=self.sm['selfdriveState'].active,
      calibrated=calibrated, gear_ok=gear_ok, safety_ok=safety_ok,
      disengage_on_accelerator=self.disengage_on_accelerator
    )
    self.alcc_status = alcc_status

    # Check which actuators can be enabled
    standstill = abs(CS.vEgo) <= max(self.CP.minSteerSpeed, 0.3) or CS.standstill

    # Standard active or ALCC
    CC.latActive = (self.sm['selfdriveState'].active or alcc_status.active) and \
                   not CS.steerFaultTemporary and not CS.steerFaultPermanent and \
                   (not standstill or self.CP.steerAtStandstill or alcc_status.hold_at_standstill)

    # Inhibit ALCC if user is actively steering (soft override)
    if alcc_status.active and not self.sm['selfdriveState'].active and abs(CS.steeringTorque) > 1.0:
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
    # Tesla-only: Use default accel limits (could be made configurable)
    # Fallback (-3.48, 2.0) matches Tesla CarControllerParams defaults
    pid_accel_limits = (self.CP.accelMin, self.CP.accelMax) if self.CP.accelMin < self.CP.accelMax else (-3.48, 2.0)

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
    if p.get('EOPAEBEnabled', False) and CC.longActive:
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
        actuators.accel = min(actuators.accel, -3.0)   # Emergency brake
      elif alert == "warning" and et.speedAdjustment and et.speedAdjustment[0] < -0.1:
        # speedAdjustment is m/s² (pathd divides Δv by ACCEL_RESPONSE_TIME=1.0s)
        actuators.accel = min(actuators.accel, max(et.speedAdjustment[0], -2.0))

    # Steering PID loop and lateral MPC
    # Reset desired curvature to current to avoid violating the limits on engage
    new_desired_curvature = model_v2.action.desiredCurvature if CC.latActive else self.curvature

    # EOP: RED - Apply road edge repulsive force to curvature
    if red_output['lateral_cost'] > 0 and self.dlat_use_laneless:
      # Apply repulsive offset: push away from detected edge
      # The cost is converted to a small curvature adjustment
      edge_offset = red_output['lateral_cost'] * 0.001  # Scale factor
      if red_output.get('edges_detected', 0) > 0:
        # Determine which side the edge is on and push away
        closest_dist = red_output.get('closest_distance', float('inf'))
        if closest_dist < 1.0:  # Only adjust if edge is close
          # edge_side: -1 = left edge → push right (negative curvature delta)
          #            +1 = right edge → push left (positive curvature delta)
          # positive desiredCurvature = left turn, so right-push = negative delta
          edge_sign = 1 if red_output.get('edge_side', 0) > 0 else -1
          new_desired_curvature += edge_sign * abs(edge_offset)

    self.desired_curvature, curvature_limited = clip_curvature(CS.vEgo, self.desired_curvature, new_desired_curvature, lp.roll)

    actuators.curvature = self.desired_curvature
    if hasattr(self.LaC, 'set_model_data'):
      self.LaC.set_model_data(self.sm['modelV2'])
    steer, steeringAngleDeg, lac_log = self.LaC.update(CC.latActive, CS, self.VM, lp,
                                                       self.steer_limited_by_safety, self.desired_curvature,
                                                       curvature_limited)  # TODO what if not available
    actuators.torque = float(steer)
    actuators.steeringAngleDeg = float(steeringAngleDeg)
    # Ensure no NaNs/Infs
    for field_name in ACTUATOR_FIELDS:
      attr = getattr(actuators, field_name)
      if not isinstance(attr, Number):
        continue
      if not math.isfinite(attr):
        cloudlog.error(f"actuators.{field_name} not finite {actuators.to_dict()}")
        setattr(actuators, field_name, 0.0)

    self.CS_prev = CS
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
    cs.dlatLaneConfidence = self.dlat.lane_confidence
    cs.dlatUseLaneless = self.dlat_use_laneless

    # EOP: radar zone monitor state (for UI overlay)
    cs.leftBlindSpot  = self.zone_monitor.left_state.alert_level.value
    cs.rightBlindSpot = self.zone_monitor.right_state.alert_level.value

    cs.upAccelCmd = float(self.LoC.pid.p)
    cs.uiAccelCmd = float(self.LoC.pid.i)
    cs.ufAccelCmd = float(self.LoC.pid.f)
    # EOP: driverMonitoringState removed (EOP uses its own monitoring daemons).
    # Inattention escalation is handled by monod → selfdriveState.state transitions.
    cs.forceDecel = bool(self.sm['selfdriveState'].state == State.softDisabling)

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
    a.state = self.alcc_status.state
    a.enabled = self.alcc_status.enabled
    a.active = self.alcc_status.active
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

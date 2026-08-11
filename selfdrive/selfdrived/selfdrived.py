#!/usr/bin/env python3
import os
import time
import threading

import cereal.messaging as messaging

from cereal import car, log
from msgq.visionipc import VisionIpcClient, VisionStreamType


from openpilot.common.params import Params
from openpilot.common.realtime import config_realtime_process, Priority, Ratekeeper, DT_CTRL
from openpilot.common.core_config import set_daemon_affinity
from openpilot.common.swaglog import cloudlog
from openpilot.common.gps import get_gps_location_service

from openpilot.system.socketd.vehicle.car.events import VehicleEvents as CarSpecificEvents
from openpilot.selfdrive.locationd.helpers import PoseCalibrator, Pose
from openpilot.selfdrive.selfdrived.events import Events, ET, AlertStatus, AudibleAlert
from openpilot.selfdrive.selfdrived.helpers import ExcessiveActuationCheck
from openpilot.selfdrive.selfdrived.state import StateMachine
from openpilot.selfdrive.selfdrived.alertmanager import AlertManager, set_offroad_alert
from openpilot.selfdrive.selfdrived.events import EmptyAlert

from openpilot.system.hardware import HARDWARE
from openpilot.system.version import get_build_metadata

REPLAY = "REPLAY" in os.environ
SIMULATION = "SIMULATION" in os.environ
TESTING_CLOSET = "TESTING_CLOSET" in os.environ

LONGITUDINAL_PERSONALITY_MAP = {v: k for k, v in log.LongitudinalPersonality.schema.enumerants.items()}

ThermalStatus = log.DeviceState.ThermalStatus
State = log.SelfdriveState.OpenpilotState
PandaType = log.PandaState.PandaType
LaneChangeState = log.LaneChangeState
LaneChangeDirection = log.LaneChangeDirection
EventName = log.OnroadEvent.EventName
ButtonType = car.CarState.ButtonEvent.Type
SafetyModel = car.CarParams.SafetyModel

IGNORED_SAFETY_MODES = (SafetyModel.silent, SafetyModel.noOutput)

# EOP: road invisible to the camera in severe weather (whiteout, mud-caked
# glass, spray wall). laneLineProbs collapse alone also fires on unmarked
# roads, so radar4d must corroborate bad weather / a blocked view.
LANE_PROB_ROAD_INVISIBLE = 0.05
LOW_VIS_SEVERITY_MIN = 2                       # radar4d moderate+ corroboration
LOW_VIS_CONFIRM_FRAMES = int(1.0 / DT_CTRL)    # 1 s sustained before takeover


class SelfdriveD:
  def __init__(self, CP=None):
    self.params = Params()

    # Ensure the current branch is cached, otherwise the first cycle lags
    build_metadata = get_build_metadata()

    if CP is None:
      cloudlog.info("selfdrived is waiting for CarParams")
      self.CP = messaging.log_from_bytes(self.params.get("CarParams", block=True), car.CarParams)
      cloudlog.info("selfdrived got CarParams")
    else:
      self.CP = CP

    self.car_events = CarSpecificEvents(self.CP)

    self.pose_calibrator = PoseCalibrator()
    self.calibrated_pose: Pose | None = None
    self.excessive_actuation_check = ExcessiveActuationCheck()
    self.excessive_actuation = self.params.get("Offroad_ExcessiveActuation") is not None

    # Setup sockets
    self.pm = messaging.PubMaster(['selfdriveState', 'onroadEvents', 'ttsRequest'])

    self.gps_location_service = get_gps_location_service(self.params)
    self.gps_packets = [self.gps_location_service]
    self.sensor_packets = ["accelerometer", "gyroscope"]
    self.camera_packets = ["roadCameraState", "wideRoadCameraState"]

    # TODO: de-couple selfdrived with card/conflate on carState without introducing controls mismatches
    self.car_state_sock = messaging.sub_sock('carState', timeout=20)

    ignore = self.sensor_packets + self.gps_packets + ['alertDebug']
    if SIMULATION:
      ignore += ['managerState']
    if REPLAY:
      # no vipc in replay will make them ignored anyways
      ignore += ['roadCameraState', 'wideRoadCameraState']
    if not self.params.get_bool("EOPRearCameraEnabled"):
      ignore += ['rearCameraState']
    _eop_status = ['stereoStatus', 'monoStatus', 'gridStatus', 'pointcloudStatus',
                   'rgaStatus', 'mppStatus', 'inferencedStatus', 'driverStatus', 'blindSpotAlert']
    # Optional perception sockets (absent when the feature param is off)
    _eop_optional = ['radar4d']
    self.sm = messaging.SubMaster(
      ['deviceState', 'pandaStates', 'peripheralState', 'modelV2', 'liveCalibration',
       'carOutput', 'longitudinalPlan', 'livePose', 'liveDelay',
       'managerState', 'liveParameters', 'radarState', 'liveTorqueParameters',
       'controlsState', 'carControl', 'driverAssistance', 'alertDebug',
       'driverPoseState', 'adaptiveDrivingState'] + _eop_status + _eop_optional + \
      self.camera_packets + self.sensor_packets + self.gps_packets,
      ignore_alive=ignore + _eop_status + _eop_optional + ['pandaStates', 'peripheralState'],
      ignore_avg_freq=ignore + _eop_status + _eop_optional + ['pandaStates', 'peripheralState'],
      ignore_valid=ignore + _eop_status + _eop_optional + ['pandaStates', 'peripheralState'],
      frequency=int(1/DT_CTRL))

    # read params
    self.is_metric = self.params.get_bool("IsMetric")
    self.is_ldw_enabled = self.params.get_bool("IsLdwEnabled")
    self.alcc_enabled = self.params.get_bool("EOPLatALCC")
    self.disengage_on_accelerator = self.params.get_bool("DisengageOnAccelerator")

    car_recognized = self.CP.brand != 'mock'

    # cleanup old params
    if not self.CP.alphaLongitudinalAvailable:
      self.params.remove("AlphaLongitudinalEnabled")
    if not self.CP.openpilotLongitudinalControl:
      self.params.remove("ExperimentalMode")

    self.CS_prev = car.CarState.new_message()
    self.AM = AlertManager()
    self.events = Events()

    self.initialized = False
    self.enabled = False
    self.active = False
    self.mismatch_counter = 0
    self.cruise_mismatch_counter = 0
    self.last_steering_pressed_frame = 0
    self.distance_traveled = 0
    self.last_functional_fan_frame = 0
    self.low_vis_frames = 0
    self.events_prev = []
    self.logged_comm_issue = None
    self.not_running_prev = None
    self.experimental_mode = False
    self.personality = self.params.get("LongitudinalPersonality", return_default=True)
    self.recalibrating_seen = False
    self.state_machine = StateMachine(self.alcc_enabled)
    self.rk = Ratekeeper(100, print_delay_threshold=None)

    # TTS alert announcement tracking
    self.tts_alerts_enabled = self.params.get_bool("EOPTTSAlertsEnabled")
    self._last_tts_alert_text = ""  # deduplicate repeated alerts
    self._tts_green_light_active = False
    self._tts_lead_departing_active = False

    # EOP: Green light / lead departing alert state
    self._was_force_stopped = False
    self._lead_distance_at_stop = None

    # EOP: Health Monitor — graduated system degradation on thermal/CPU/memory stress
    self._health_monitor_enabled = self.params.get_bool("EOPHealthMonitorEnabled")
    self._health_cpu_history = []  # CPU usage samples for trend detection
    self._health_thermal_history = []  # Thermal level samples
    self._health_warning_active = False
    self._health_warn_start_time = 0.0

    # some comma three with NVMe experience NVMe dropouts mid-drive that
    # cause loggerd to crash on write, so ignore it only on that platform
    self.ignored_processes = set()
    nvme_expected = os.path.exists('/dev/nvme0n1') or (not os.path.isfile("/persist/comma/living-in-the-moment"))
    if HARDWARE.get_device_type() == 'tici' and nvme_expected:
      self.ignored_processes = {'loggerd', }

    # Determine startup event
    self.startup_event = EventName.startup if build_metadata.openpilot.comma_remote and build_metadata.tested_channel else EventName.startupMaster
    if not car_recognized:
      self.startup_event = EventName.startupNoCar
    elif car_recognized and self.CP.passive:
      self.startup_event = EventName.startupNoControl
    elif self.CP.secOcRequired and not self.CP.secOcKeyAvailable:
      self.startup_event = EventName.startupNoSecOcKey

    if not car_recognized:
      self.events.add(EventName.carUnrecognized, static=True)
      set_offroad_alert("Offroad_CarUnrecognized", True)
    elif self.CP.passive:
      self.events.add(EventName.dashcamMode, static=True)

  def _evaluate_health_monitor(self):
    """EOP: Health Monitor — evaluate system health and trigger graduated responses.

    Merges VisionPilot health monitor concepts into openpilot's existing event system:
      - Level 0 (NORMAL): nothing
      - Level 1 (WARNING): yellow thermal OR high CPU/memory trend → healthWarning
      - Level 2 (DEGRADED): red thermal OR very high CPU/memory → healthDegradedStop (SOFT_DISABLE)
      - Level 3 (CRITICAL): danger thermal → healthCriticalStop (IMMEDIATE_DISABLE)
    """
    if not self._health_monitor_enabled or SIMULATION:
      return

    ds = self.sm['deviceState']
    thermal = ds.thermalStatus
    cpu_samples = ds.cpuUsagePercent
    cpu_avg = sum(cpu_samples) / max(len(cpu_samples), 1) if cpu_samples else 0
    mem = ds.memoryUsagePercent

    # Update history for trend detection (keep last 3s at 100Hz = 300 samples)
    self._health_cpu_history.append(cpu_avg)
    self._health_cpu_history = self._health_cpu_history[-300:]
    self._health_thermal_history.append(int(thermal.raw))
    self._health_thermal_history = self._health_thermal_history[-300:]

    # Trend: rising thermal or CPU over last 3 seconds (300 frames)
    cpu_trend = 0
    thermal_trend = 0
    if len(self._health_cpu_history) >= 300:
      cpu_trend = self._health_cpu_history[-1] - self._health_cpu_history[-300]
    if len(self._health_thermal_history) >= 300:
      thermal_trend = self._health_thermal_history[-1] - self._health_thermal_history[-300]

    # Count how many warning signs are present
    warning_signs = 0
    if thermal == ThermalStatus.yellow:
      warning_signs += 1
    if cpu_avg > 80:
      warning_signs += 1
    if mem > 85:
      warning_signs += 1
    if cpu_trend > 10:
      warning_signs += 1  # CPU rising fast
    if thermal_trend > 0:
      warning_signs += 1  # Thermal rising

    degraded_signs = 0
    if thermal == ThermalStatus.red:
      degraded_signs += 1
    if cpu_avg > 95:
      degraded_signs += 1
    if mem > 95:
      degraded_signs += 1

    # Health Monitor level decision
    if thermal == ThermalStatus.danger:
      self.events.add(EventName.healthCriticalStop)
      self._health_warning_active = False
      return

    if degraded_signs >= 1:
      self.events.add(EventName.healthDegradedStop)
      self._health_warning_active = False
      return

    if warning_signs >= 2:
      # Multiple warning signs → escalate immediately
      self.events.add(EventName.healthWarning)
      if not self._health_warning_active:
        self._health_warning_active = True
        self._health_warn_start_time = time.monotonic()
      # Escalate to comfortable stop if warning persists > 10 seconds
      if time.monotonic() - self._health_warn_start_time > 10.0:
        self.events.add(EventName.healthDegradedStop)
      return

    if warning_signs >= 1:
      if self._health_warning_active:
        # Sustained single warning → escalate
        self.events.add(EventName.healthWarning)
        if time.monotonic() - self._health_warn_start_time > 10.0:
          self.events.add(EventName.healthDegradedStop)
        return
      else:
        # First single warning → arm but don't escalate yet
        self._health_warning_active = True
        self._health_warn_start_time = time.monotonic()
        return

    # No issues
    self._health_warning_active = False

  def _update_low_visibility(self):
    """Road invisible to the camera in severe weather → takeover.

    Camera side: modelV2 lane-structure collapse (all laneLineProbs ~0).
    Radar corroboration (weatherSeverity moderate+ or visionBlocked) is
    required so unmarked roads in clear weather never trigger this.
    """
    camera_blind = False
    if self.sm.valid['modelV2']:
      probs = self.sm['modelV2'].laneLineProbs
      camera_blind = len(probs) > 0 and max(probs) < LANE_PROB_ROAD_INVISIBLE
    radar_severity = 0
    radar_blocked = False
    if self.sm.valid['radar4d']:
      radar_severity = int(self.sm['radar4d'].weatherSeverity)
      radar_blocked = bool(self.sm['radar4d'].visionBlocked)
    blind = camera_blind and (radar_severity >= LOW_VIS_SEVERITY_MIN or radar_blocked)
    self.low_vis_frames = self.low_vis_frames + 1 if blind else 0
    if self.low_vis_frames >= LOW_VIS_CONFIRM_FRAMES:
      self.events.add(EventName.lowVisibility)

  def update_events(self, CS):
    """Compute onroadEvents from carState"""

    self.events.clear()

    if self.sm['controlsState'].lateralControlState.which() == 'debugState':
      self.events.add(EventName.joystickDebug)
      self.startup_event = None

    if self.sm.recv_frame['alertDebug'] > 0:
      self.events.add(EventName.longitudinalManeuver)
      self.startup_event = None

    # Add startup event
    if self.startup_event is not None:
      self.events.add(self.startup_event)
      self.startup_event = None

    # Don't add any more events if not initialized
    if not self.initialized:
      self.events.add(EventName.selfdriveInitializing)
      return

    # Don't add any more events while in dashcam mode
    if self.CP.passive:
      return

    # Block resume if cruise never previously enabled
    resume_pressed = any(be.type in (ButtonType.accelCruise, ButtonType.resumeCruise) for be in CS.buttonEvents)
    if not self.CP.pcmCruise and CS.vCruise > 250 and resume_pressed:
      self.events.add(EventName.resumeBlocked)

    if not self.CP.notCar:
      if self.sm.valid.get('driverPoseState', False):
        self.events.add_from_msg(self.sm['driverPoseState'].events)

    # Add car events, ignore if CAN isn't valid
    if CS.canValid:
      car_events = self.car_events.update(CS, self.CS_prev, self.sm['carControl']).to_msg()
      self.events.add_from_msg(car_events)

      if self.CP.notCar:
        # wait for everything to init first
        if self.sm.frame > int(5. / DT_CTRL) and self.initialized:
          # body always wants to enable
          self.events.add(EventName.pcmEnable)

      # Disable on rising edge of accelerator or brake. Also disable on brake when speed > 0
      if (CS.gasPressed and not self.CS_prev.gasPressed and self.disengage_on_accelerator) or \
        (CS.brakePressed and (not self.CS_prev.brakePressed or not CS.standstill)) or \
        (CS.regenBraking and (not self.CS_prev.regenBraking or not CS.standstill)):
        self.events.add(EventName.pedalPressed)

    # EOP: Health Monitor — graduated system degradation
    # Evaluates aggregated health: thermal + CPU + memory trends
    self._evaluate_health_monitor()

    # Legacy individual health checks (kept for compatibility)
    if self.sm['deviceState'].thermalStatus >= ThermalStatus.red:
      self.events.add(EventName.overheat)
    if self.sm['deviceState'].freeSpacePercent < 7 and not SIMULATION:
      self.events.add(EventName.outOfSpace)
    if self.sm['deviceState'].memoryUsagePercent > 90 and not SIMULATION:
      self.events.add(EventName.lowMemory)

    # Alert if fan isn't spinning for 5 seconds
    if self.sm['peripheralState'].pandaType != log.PandaState.PandaType.unknown:
      if self.sm['peripheralState'].fanSpeedRpm < 500 and self.sm['deviceState'].fanSpeedPercentDesired > 50:
        # allow enough time for the fan controller in the panda to recover from stalls
        if (self.sm.frame - self.last_functional_fan_frame) * DT_CTRL > 15.0:
          self.events.add(EventName.fanMalfunction)
      else:
        self.last_functional_fan_frame = self.sm.frame

    # Handle calibration status
    cal_status = self.sm['liveCalibration'].calStatus
    if cal_status != log.LiveCalibrationData.Status.calibrated:
      if cal_status == log.LiveCalibrationData.Status.uncalibrated:
        self.events.add(EventName.calibrationIncomplete)
      elif cal_status == log.LiveCalibrationData.Status.recalibrating:
        if not self.recalibrating_seen:
          set_offroad_alert("Offroad_Recalibration", True)
        self.recalibrating_seen = True
        self.events.add(EventName.calibrationRecalibrating)
      else:
        self.events.add(EventName.calibrationInvalid)

    # Lane departure warning
    if self.is_ldw_enabled and self.sm.valid['driverAssistance']:
      if self.sm['driverAssistance'].leftLaneDeparture or self.sm['driverAssistance'].rightLaneDeparture:
        self.events.add(EventName.ldw)

    # ******************************************************************************************
    #  NOTE: To fork maintainers.
    #  Disabling or nerfing safety features will get you and your users banned from our servers.
    #  We recommend that you do not change these numbers from the defaults.
    if self.sm.updated['liveCalibration']:
      self.pose_calibrator.feed_live_calib(self.sm['liveCalibration'])
    if self.sm.updated['livePose']:
      device_pose = Pose.from_live_pose(self.sm['livePose'])
      self.calibrated_pose = self.pose_calibrator.build_calibrated_pose(device_pose)

    if self.calibrated_pose is not None:
      excessive_actuation = self.excessive_actuation_check.update(self.sm, CS, self.calibrated_pose)
      if not self.excessive_actuation and excessive_actuation is not None:
        set_offroad_alert("Offroad_ExcessiveActuation", True, extra_text=str(excessive_actuation))
        self.excessive_actuation = True

    if self.excessive_actuation:
      self.events.add(EventName.excessiveActuation)
    # ******************************************************************************************

    # Handle lane change
    if self.sm['modelV2'].meta.laneChangeState == LaneChangeState.preLaneChange:
      direction = self.sm['modelV2'].meta.laneChangeDirection
      bsa = self.sm['blindSpotAlert'] if self.sm.valid.get('blindSpotAlert') else None
      left_blocked = CS.leftBlindspot or (bsa is not None and (bsa.leftDetected or bsa.leftAlertLevel >= 1))
      right_blocked = CS.rightBlindspot or (bsa is not None and (bsa.rightDetected or bsa.rightAlertLevel >= 1))
      if (left_blocked and direction == LaneChangeDirection.left) or \
         (right_blocked and direction == LaneChangeDirection.right):
        self.events.add(EventName.laneChangeBlocked)
      else:
        if direction == LaneChangeDirection.left:
          self.events.add(EventName.preLaneChangeLeft)
        else:
          self.events.add(EventName.preLaneChangeRight)
    elif self.sm['modelV2'].meta.laneChangeState in (LaneChangeState.laneChangeStarting,
                                                    LaneChangeState.laneChangeFinishing):
      self.events.add(EventName.laneChange)

    for i, pandaState in enumerate(self.sm['pandaStates']):
      # All pandas must match the list of safetyConfigs, and if outside this list, must be silent or noOutput
      if i < len(self.CP.safetyConfigs):
        safety_mismatch = pandaState.safetyModel != self.CP.safetyConfigs[i].safetyModel or \
                          pandaState.safetyParam != self.CP.safetyConfigs[i].safetyParam or \
                          pandaState.alternativeExperience != self.CP.alternativeExperience
      else:
        safety_mismatch = pandaState.safetyModel not in IGNORED_SAFETY_MODES

      # safety mismatch allows some time for socketd to configure safety mode
      if (safety_mismatch and self.sm.frame*DT_CTRL > 10.) or pandaState.safetyRxChecksInvalid or self.mismatch_counter >= 200:
        self.events.add(EventName.controlsMismatch)

      if log.PandaState.FaultType.relayMalfunction in pandaState.faults:
        self.events.add(EventName.relayMalfunction)

    # Handle HW and system malfunctions
    # Order is very intentional here. Be careful when modifying this.
    # All events here should at least have NO_ENTRY and SOFT_DISABLE.
    num_events = len(self.events)

    not_running = {p.name for p in self.sm['managerState'].processes if not p.running and p.shouldBeRunning}
    if self.sm.recv_frame['managerState'] and len(not_running):
      if not_running != self.not_running_prev:
        cloudlog.event("process_not_running", not_running=not_running, error=True)
      self.not_running_prev = not_running
    if self.sm.recv_frame['managerState'] and (not_running - self.ignored_processes):
      self.events.add(EventName.processNotRunning)
    else:
      if not SIMULATION and not self.rk.lagging:
        if not self.sm.all_alive(self.camera_packets):
          self.events.add(EventName.cameraMalfunction)
        elif not self.sm.all_freq_ok(self.camera_packets):
          self.events.add(EventName.cameraFrameRate)
    if not REPLAY and self.rk.lagging:
      self.events.add(EventName.selfdrivedLagging)
    _radar_err = str(self.sm['radarState'].radarErrors)
    if _radar_err == 'canError':
      self.events.add(EventName.canError)
    elif _radar_err not in ('none', 'canError'):
      self.events.add(EventName.radarFault)
    # EOP: road invisible to the camera in severe weather — takeover, not gating
    self._update_low_visibility()
    # EOP: Stereo GPU fault → VisionPilot-style IMMEDIATE_DISABLE (no CPU fallback)
    if self.sm.valid['stereoStatus'] and self.sm['stereoStatus'].enabled:
      if self.sm['stereoStatus'].fault:
        self.events.add(EventName.stereoFault)

    # EOP: Mono NPU fault → IMMEDIATE_DISABLE
    if self.sm.valid['monoStatus'] and self.sm['monoStatus'].enabled:
      if self.sm['monoStatus'].fault:
        self.events.add(EventName.monoFault)

    # EOP: Grid detection NPU fault → IMMEDIATE_DISABLE
    if self.sm.valid['gridStatus'] and self.sm['gridStatus'].enabled:
      if self.sm['gridStatus'].fault:
        self.events.add(EventName.gridFault)

    # EOP: RGA hardware fault → SOFT_DISABLE (OpenCV fallback active, latency risk)
    # (RgaStatus has no 'enabled' field; fault already implies the HW path was active)
    if self.sm.valid['rgaStatus'] and self.sm['rgaStatus'].fault:
      self.events.add(EventName.rgaFault)

    # EOP: MPP encode fault → PERMANENT alert only (recording stops, driving unaffected)
    if self.sm.valid['mppStatus'] and self.sm['mppStatus'].fault:
      self.events.add(EventName.mppFault)

    # EOP: Inference backend fault → IMMEDIATE_DISABLE (all backends unavailable)
    if self.sm.valid['inferencedStatus'] and self.sm['inferencedStatus'].enabled and self.sm['inferencedStatus'].fault:
      self.events.add(EventName.inferenceFault)

    # EOP: Point cloud recording fault → PERMANENT alert (recording stops, driving OK)
    if self.sm.valid['pointcloudStatus'] and self.sm['pointcloudStatus'].enabled and self.sm['pointcloudStatus'].fault:
      self.events.add(EventName.pointcloudFault)

    if not self.sm.valid['pandaStates']:
      self.events.add(EventName.usbError)
    if CS.canTimeout:
      self.events.add(EventName.canBusMissing)
    elif not CS.canValid:
      self.events.add(EventName.canError)

    # generic catch-all. ideally, a more specific event should be added above instead
    has_disable_events = self.events.contains(ET.NO_ENTRY) and (self.events.contains(ET.SOFT_DISABLE) or self.events.contains(ET.IMMEDIATE_DISABLE))
    no_system_errors = (not has_disable_events) or (len(self.events) == num_events)
    if not self.sm.all_checks() and no_system_errors:
      if not self.sm.all_alive():
        self.events.add(EventName.commIssue)
      elif not self.sm.all_freq_ok():
        self.events.add(EventName.commIssueAvgFreq)
      else:
        self.events.add(EventName.commIssue)

      logs = {
        'invalid': [s for s, valid in self.sm.valid.items() if not valid],
        'not_alive': [s for s, alive in self.sm.alive.items() if not alive],
        'not_freq_ok': [s for s, freq_ok in self.sm.freq_ok.items() if not freq_ok],
      }
      if logs != self.logged_comm_issue:
        cloudlog.event("commIssue", error=True, **logs)
        self.logged_comm_issue = logs
    else:
      self.logged_comm_issue = None

    if not self.CP.notCar:
      if not self.sm['livePose'].posenetOK:
        self.events.add(EventName.posenetInvalid)
      if not self.sm['livePose'].inputsOK:
        self.events.add(EventName.locationdTemporaryError)
      if not self.sm['liveParameters'].valid and cal_status == log.LiveCalibrationData.Status.calibrated and not TESTING_CLOSET and (not SIMULATION or REPLAY):
        self.events.add(EventName.paramsdTemporaryError)

    # conservative HW alert. if the data or frequency are off, locationd will throw an error
    if any((self.sm.frame - self.sm.recv_frame[s])*DT_CTRL > 10. for s in self.sensor_packets):
      self.events.add(EventName.sensorDataInvalid)

    if not REPLAY:
      # Check for mismatch between openpilot and car's PCM
      cruise_mismatch = CS.cruiseState.enabled and (not self.enabled or not self.CP.pcmCruise)
      self.cruise_mismatch_counter = self.cruise_mismatch_counter + 1 if cruise_mismatch else 0
      if self.cruise_mismatch_counter > int(6. / DT_CTRL):
        self.events.add(EventName.cruiseMismatch)

    # Send a "steering required alert" if saturation count has reached the limit
    if CS.steeringPressed:
      self.last_steering_pressed_frame = self.sm.frame
    recent_steer_pressed = (self.sm.frame - self.last_steering_pressed_frame)*DT_CTRL < 2.0
    controlstate = self.sm['controlsState']
    lac = getattr(controlstate.lateralControlState, controlstate.lateralControlState.which())
    if lac.active and not recent_steer_pressed and not self.CP.notCar:
      clipped_speed = max(CS.vEgo, 0.3)
      actual_lateral_accel = controlstate.curvature * (clipped_speed**2)
      desired_lateral_accel = self.sm['modelV2'].action.desiredCurvature * (clipped_speed**2)
      undershooting = abs(desired_lateral_accel) / abs(1e-3 + actual_lateral_accel) > 1.2
      turning = abs(desired_lateral_accel) > 1.0
      # TODO: lac.saturated includes speed and other checks, should be pulled out
      if undershooting and turning and lac.saturated:
        self.events.add(EventName.steerSaturated)

    # Check for FCW
    stock_long_is_braking = self.enabled and not self.CP.openpilotLongitudinalControl and CS.aEgo < -1.25
    model_fcw = self.sm['modelV2'].meta.hardBrakePredicted and not CS.brakePressed and not stock_long_is_braking
    planner_fcw = self.sm['longitudinalPlan'].fcw and self.enabled
    if (planner_fcw or model_fcw) and not self.CP.notCar:
      self.events.add(EventName.fcw)

    # EOP: Green light alert — stopped for light, light turned green
    dlon_force_stop = self.sm.valid.get('longitudinalPlan', False) and self.sm['longitudinalPlan'].dlonForceStop
    if self._was_force_stopped and not dlon_force_stop and CS.standstill:
      self.events.add(EventName.greenLightAlert)
      if self.tts_alerts_enabled and not self._tts_green_light_active:
        self._publish_tts_direct("Green light. You may proceed.", priority=1)
        self._tts_green_light_active = True
    else:
      self._tts_green_light_active = False
    self._was_force_stopped = dlon_force_stop

    # EOP: Lead departing alert — lead moves away while we're stopped
    radar_state = self.sm['radarState']
    if CS.standstill and radar_state.leadOne.status:
      if self._lead_distance_at_stop is None:
        self._lead_distance_at_stop = radar_state.leadOne.dRel
      elif radar_state.leadOne.dRel - self._lead_distance_at_stop > 1.0 and radar_state.leadOne.vLead > 1.0:
        self.events.add(EventName.leadDepartingAlert)
        if self.tts_alerts_enabled and not self._tts_lead_departing_active:
          self._publish_tts_direct("Lead vehicle departing.", priority=1)
          self._tts_lead_departing_active = True
    else:
      self._lead_distance_at_stop = None
      self._tts_lead_departing_active = False

    # GPS checks
    gps_ok = self.sm.recv_frame[self.gps_location_service] > 0 and (self.sm.frame - self.sm.recv_frame[self.gps_location_service]) * DT_CTRL < 2.0
    if not gps_ok and self.sm['livePose'].inputsOK and (self.distance_traveled > 1500):
      self.events.add(EventName.noGps)
    if gps_ok:
      self.distance_traveled = 0
    self.distance_traveled += abs(CS.vEgo) * DT_CTRL

    # TODO: fix simulator
    if not SIMULATION or REPLAY:
      if self.sm['modelV2'].frameDropPerc > 20:
        self.events.add(EventName.modeldLagging)

    # Decrement personality on distance button press (4 personalities with traffic mode)
    if self.CP.openpilotLongitudinalControl:
      if any(not be.pressed and be.type == ButtonType.gapAdjustCruise for be in CS.buttonEvents):
        self.personality = (self.personality - 1) % 4
        self.params.put_nonblocking('LongitudinalPersonality', self.personality)
        self.events.add(EventName.personalityChanged)

    # EOP: adaptd adaptive driving override — only when no button press this frame
    if self.CP.openpilotLongitudinalControl and self.sm.valid.get('adaptiveDrivingState', False):
      ads = self.sm['adaptiveDrivingState']
      if ads.enabled:
        ads_personality = int(ads.personality)
        if 0 <= ads_personality <= 3 and ads_personality != self.personality:
          self.personality = ads_personality
          self.params.put_nonblocking('LongitudinalPersonality', self.personality)

  def data_sample(self):
    _car_state = messaging.recv_one(self.car_state_sock)
    CS = _car_state.carState if _car_state else self.CS_prev

    self.sm.update(0)

    if not self.initialized:
      all_valid = CS.canValid and self.sm.all_checks()
      timed_out = self.sm.frame * DT_CTRL > 6.
      if all_valid or timed_out or (SIMULATION and not REPLAY):
        available_streams = VisionIpcClient.available_streams("v4l2d", block=False)
        if VisionStreamType.VISION_STREAM_ROAD not in available_streams:
          self.sm.ignore_alive.append('roadCameraState')
          self.sm.ignore_valid.append('roadCameraState')
        if VisionStreamType.VISION_STREAM_WIDE_ROAD not in available_streams:
          self.sm.ignore_alive.append('wideRoadCameraState')
          self.sm.ignore_valid.append('wideRoadCameraState')

        if REPLAY and any(ps.controlsAllowed for ps in self.sm['pandaStates']):
          self.state_machine.state = State.enabled

        self.initialized = True
        cloudlog.event(
          "selfdrived.initialized",
          dt=self.sm.frame*DT_CTRL,
          timeout=timed_out,
          canValid=CS.canValid,
          invalid=[s for s, valid in self.sm.valid.items() if not valid],
          not_alive=[s for s, alive in self.sm.alive.items() if not alive],
          not_freq_ok=[s for s, freq_ok in self.sm.freq_ok.items() if not freq_ok],
          error=True,
        )

    # When the panda and selfdrived do not agree on controls_allowed
    # we want to disengage openpilot. However the status from the panda goes through
    # another socket other than the CAN messages and one can arrive earlier than the other.
    # Therefore we allow a mismatch for two samples, then we trigger the disengagement.
    if not self.enabled:
      self.mismatch_counter = 0

    # All pandas not in silent mode must have controlsAllowed when openpilot is enabled
    if self.enabled and any(not ps.controlsAllowed for ps in self.sm['pandaStates']
           if ps.safetyModel not in IGNORED_SAFETY_MODES):
      self.mismatch_counter += 1

    return CS

  def _publish_tts_direct(self, text: str, priority: int = 1):
    """Publish a TTS request directly (for non-critical contextual announcements)."""
    msg = messaging.new_message('ttsRequest')
    msg.ttsRequest.text = text
    msg.ttsRequest.priority = priority
    msg.ttsRequest.interrupt = True
    self.pm.send('ttsRequest', msg)

  def _publish_tts_for_alert(self, alert):
    """Publish TTS request for critical alerts that need voice announcement."""
    if not self.tts_alerts_enabled:
      return
    if alert == EmptyAlert:
      return

    # Build TTS text from alert
    text = alert.alert_text_1
    if alert.alert_text_2:
      text = f"{text}. {alert.alert_text_2}"

    # Skip if same as last announced alert (deduplication)
    if text == self._last_tts_alert_text:
      return
    self._last_tts_alert_text = text

    # Only announce alerts that are critical, warnings, or user prompts
    # Skip normal/info alerts to avoid chatter
    if alert.alert_status not in (AlertStatus.critical, AlertStatus.userPrompt):
      return

    # Skip alerts that already have audible_alert (they make their own sound)
    if alert.audible_alert != AudibleAlert.none:
      return

    # Priority mapping
    priority = 1  # high default
    if alert.alert_status == AlertStatus.critical:
      priority = 0  # critical

    cloudlog.info(f"selfdrived: TTS alert: {text}")
    msg = messaging.new_message('ttsRequest')
    msg.ttsRequest.text = text
    msg.ttsRequest.priority = priority
    msg.ttsRequest.interrupt = True
    self.pm.send('ttsRequest', msg)

  def update_alerts(self, CS):
    clear_event_types = set()
    if ET.WARNING not in self.state_machine.current_alert_types:
      clear_event_types.add(ET.WARNING)
    if self.enabled:
      clear_event_types.add(ET.NO_ENTRY)

    pers = LONGITUDINAL_PERSONALITY_MAP[self.personality]
    alerts = self.events.create_alerts(self.state_machine.current_alert_types, [self.CP, CS, self.sm, self.is_metric,
                                                                                self.state_machine.soft_disable_timer, pers])
    self.AM.add_many(self.sm.frame, alerts)
    self.AM.process_alerts(self.sm.frame, clear_event_types)

  def publish_selfdriveState(self, CS):
    # selfdriveState
    ss_msg = messaging.new_message('selfdriveState')
    ss_msg.valid = True
    ss = ss_msg.selfdriveState
    ss.enabled = self.enabled
    ss.active = self.active
    ss.state = self.state_machine.state
    ss.engageable = not self.events.contains(ET.NO_ENTRY)
    ss.experimentalMode = self.experimental_mode
    ss.personality = self.personality

    ss.alertText1 = self.AM.current_alert.alert_text_1
    ss.alertText2 = self.AM.current_alert.alert_text_2
    ss.alertSize = self.AM.current_alert.alert_size
    ss.alertStatus = self.AM.current_alert.alert_status
    ss.alertType = self.AM.current_alert.alert_type
    ss.alertSound = self.AM.current_alert.audible_alert
    ss.alertHudVisual = self.AM.current_alert.visual_alert

    self.pm.send('selfdriveState', ss_msg)

    # TTS for critical alerts (only when alert changes)
    self._publish_tts_for_alert(self.AM.current_alert)

    # onroadEvents - logged every second or on change
    if (self.sm.frame % int(1. / DT_CTRL) == 0) or (self.events.names != self.events_prev):
      ce_send = messaging.new_message('onroadEvents', len(self.events))
      ce_send.valid = True
      ce_send.onroadEvents = self.events.to_msg()
      self.pm.send('onroadEvents', ce_send)
    self.events_prev = self.events.names.copy()

  def step(self):
    CS = self.data_sample()
    self.update_events(CS)
    if not self.CP.passive and self.initialized:
      self.enabled, self.active = self.state_machine.update(self.events)
    self.update_alerts(CS)

    self.publish_selfdriveState(CS)

    self.CS_prev = CS

  def params_thread(self, evt):
    while not evt.is_set():
      self.is_metric = self.params.get_bool("IsMetric")
      self.is_ldw_enabled = self.params.get_bool("IsLdwEnabled")
      self.disengage_on_accelerator = self.params.get_bool("DisengageOnAccelerator")
      self.experimental_mode = self.params.get_bool("ExperimentalMode") and self.CP.openpilotLongitudinalControl
      self.personality = self.params.get("LongitudinalPersonality", return_default=True)
      time.sleep(0.1)

  def run(self):
    e = threading.Event()
    t = threading.Thread(target=self.params_thread, args=(e, ))
    try:
      t.start()
      while True:
        self.step()
        self.rk.monitor_time()
    finally:
      e.set()
      t.join(timeout=2.0)


def main() -> int:
  try:
    set_daemon_affinity("selfdrived")
    config_realtime_process(DT_CTRL, Priority.CTRL_HIGH)
    s = SelfdriveD()
    s.run()
    return 0
  except Exception as e:
    cloudlog.exception(f"SelfdriveD fatal error: {e}")
    raise

if __name__ == "__main__":
  exit(main())

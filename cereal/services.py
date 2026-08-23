#!/usr/bin/env python3


class Service:
  def __init__(self, should_log: bool, frequency: float, decimation: int | None = None):
    self.should_log = should_log
    self.frequency = frequency
    self.decimation = decimation


_services: dict[str, tuple] = {
  # service: (should_log, frequency, qlog decimation (optional))
  # note: the "EncodeIdx" packets will still be in the log
  "liveLocationKalman": (True, 20., 4),
  "navInstruction": (True, 1., 10),
  "navRoute": (True, 0.),
  "mapData": (True, 1., 1),
  "gyroscope": (True, 104., 104),
  "gyroscope2": (True, 100., 100),
  "accelerometer": (True, 104., 104),
  "accelerometer2": (True, 100., 100),
  "magnetometer": (True, 25.),
  "lightSensor": (True, 100., 100),
  "temperatureSensor": (True, 2., 200),
  "temperatureSensor2": (True, 2., 200),
  "gpsNMEA": (True, 9.),
  "deviceState": (True, 2., 1),
  "touch": (True, 20., 1),
  "can": (True, 100., 2053),  # decimation gives ~3 msgs in a full segment
  "controlsState": (True, 100., 10),
  "selfdriveState": (True, 100., 10),
  "peripheralState": (True, 2., 1),
  "radarState": (True, 20., 5),
  "roadEncodeIdx": (False, 20., 1),
  "driverEncodeIdx": (False, 20., 1),
  "radar3d": (True, 20.),   # long-range UART radar (was liveTracks / car OEM CAN radar)
  "radar2d": (True, 20.),   # ESP32-S3 corner radars — tracked objects + legacy zone presence
  "stereoObjects": (True, 20.),
  "stereoGround": (True, 20.),
  "gridObjects": (True, 20.),
  "enhancedTrajectory": (True, 20., 5),
  # NOTE: predictedObjects removed - predictd deleted, internal to pathd now
  # NOTE: groundObjects removed - groundd never implemented, use stereoGround
  "sendcan": (True, 100., 139),
  "logMessage": (True, 0.),
  "errorLogMessage": (True, 0., 1),
  "liveCalibration": (True, 4., 4),
  "calibrationState": (True, 4., 4),  # EOP: Multi-camera calibration state
  "systemState": (True, 10., 10),     # EOP: VisionPilot-style system state
  "liveTorqueParameters": (True, 4., 1),
  "liveDelay": (True, 4., 1),
  "androidLog": (True, 0.),
  "carState": (True, 100., 10),
  "carControl": (True, 100., 10),
  "carOutput": (True, 100., 10),
  "longitudinalPlan": (True, 20., 10),
  "alccState": (True, 100., 10),
  "speedLimitState": (True, 4., 1),
  "driverAssistance": (True, 20., 20),
  "procLog": (True, 0.5, 15),
  "gpsLocationExternal": (True, 10., 10),
  "gpsLocation": (True, 1., 1),
  "ubloxGnss": (True, 10.),
  "gnssMeasurements": (True, 10., 10),
  "clocks": (True, 0.1, 1),
  "ubloxRaw": (True, 20.),
  "livePose": (True, 20., 4),
  "sgmCorrectedPose": (True, 10., 4),
  "fusedPosition": (True, 5., 10),     # coordinationd: ECEF position with road constraints
  "liveParameters": (True, 20., 5),
  "cameraOdometry": (True, 20., 10),
  "thumbnail": (True, 1 / 60., 1),
  "onroadEvents": (True, 1., 1),
  "carParams": (True, 0.02, 1),
  "roadCameraState": (True, 20., 20),
  "driverCameraState": (True, 20., 20),
  "wideRoadEncodeIdx": (False, 20., 1),
  "wideRoadCameraState": (True, 20., 20),
  "teleRoadEncodeIdx": (False, 20., 1),
  "teleRoadCameraState": (True, 20., 20),
  "leftCameraState": (True, 20., 20),
  "rightCameraState": (True, 20., 20),
  "rearCameraState": (True, 20., 20),
  "drivingModelData": (True, 20., 10),
  "modelV2": (True, 20.),
  "managerState": (True, 2., 1),
  "uploaderState": (True, 0., 1),
  "subscriptionState": (True, 1., 1),  # EOP: NavPilot subscription & hardware auth
  "soundPressure": (True, 10., 10),
  "microphoneData": (True, 10.),
  "rawAudioData": (False, 20.),
  "qRoadEncodeIdx": (False, 20.),
  "uiEncodeIdx": (False, 20.),
  "recorddState": (False, 1., 1),
  "recordersHealth": (False, 1., 1),

  # upstream services preserved for log compat (not produced on EOP hardware)
  "pandaStates": (True, 10., 1),
  "pandaState": (False, 0.),    # singular alias used by safety_panel.cc; no publisher on EOP
  "sensorEvents": (False, 0.),  # legacy IMU bundle; EOP publishes gyroscope/accelerometer separately
  "qcomGnss": (True, 2.),
  "driverStateV2": (True, 20., 10),
  "driverMonitoringState": (True, 20., 10),
  "driverStatus": (True, 20., 10),
  "driverPoseState": (True, 20., 10),
  "vehicleState": (True, 10., 5),
  "thermalStatus": (True, 10., 5),
  "navThumbnail": (True, 0.),
  "userBookmark": (True, 0., 1),
  "bookmarkButton": (True, 0., 1),
  "audioFeedback": (True, 0., 1),

  # debug
  "uiDebug": (True, 0., 1),
  "testJoystick": (True, 0.),
  "alertDebug": (True, 20., 5),
  "roadEncodeData": (False, 20.),
  "wideRoadEncodeData": (False, 20.),
  "teleRoadEncodeData": (False, 20.),
  "qRoadEncodeData": (False, 20.),
  "uiEncodeData": (False, 20.),
  "stereoLeftCameraEncodeData": (False, 20.),
  "stereoRightCameraEncodeData": (False, 20.),
  "leftCameraEncodeData": (False, 20.),
  "rightCameraEncodeData": (False, 20.),
  "stereoLeftCameraEncodeIdx": (False, 20., 1),
  "stereoRightCameraEncodeIdx": (False, 20., 1),
  "leftCameraEncodeIdx": (False, 20., 1),
  "rightCameraEncodeIdx": (False, 20., 1),
  "stereoDepthMapEncodeData": (False, 20.),
  "stereoDepthMapEncodeIdx": (False, 20., 1),
  "livestreamWideRoadEncodeIdx": (False, 20.),
  "livestreamRoadEncodeIdx": (False, 20.),
  "livestreamStereoLeftEncodeIdx": (False, 20.),
  "livestreamStereoRightEncodeIdx": (False, 20.),
  "livestreamRearLeftEncodeIdx": (False, 20.),
  "livestreamRearRightEncodeIdx": (False, 20.),
  "livestreamWideRoadEncodeData": (False, 20.),
  "livestreamRoadEncodeData": (False, 20.),
  "livestreamStereoLeftEncodeData": (False, 20.),
  "livestreamStereoRightEncodeData": (False, 20.),
  "livestreamRearLeftEncodeData": (False, 20.),
  # upstream entries kept for wire/tooling parity (no driver cam on EOP; dormant)
  "driverEncodeData": (False, 20.),
  "livestreamDriverEncodeIdx": (False, 20.),
  "livestreamDriverEncodeData": (False, 20.),
  "customReservedRawData0": (True, 0.),
  "customReservedRawData1": (True, 0.),
  "customReservedRawData2": (True, 0.),
  "livestreamRearRightEncodeData": (False, 20.),

  # OBD2 / NCP
  "obdCommand": (False, 0.),           # SPP/GATT → obd2d
  "obdResponse": (False, 0.),          # obd2d → SPP
  "obdState": (True, 2., 10),          # Periodic OBD state (RPM, speed, temps)
  "adaptiveDrivingState": (True, 2., 10),   # Adaptive driving params from adaptd
  "ncpVehicleData": (True, 2., 10),         # Interpreted OBD from NavPilot
  "voiceCommandRequest": (False, 0.),       # bluetoothd → voice pipeline

  # Bluetooth SPP services
  # NOTE: HFP removed - both projects use SPP-only with I2S speaker
  "sppStatus": (True, 1., 1),     # SPP connection status

  # ExoPilot Hailo AI Detection Services (monod)
  "monoDetections": (True, 20., 5),    # Multi-camera YOLO detections
  "monoSegments": (True, 20., 20),     # TeleRoad SceneSeg segmentation
  "monoFeatures": (True, 20., 10),     # Visual features from all cameras
  "monoStatus": (True, 2., 1),         # monod status and TOPS usage

  # Stereo depth services (stereod)
  "stereoDepth": (True, 20., 5),       # Point cloud from SGBM
  "stereoDetections": (True, 20., 10), # 3D objects from stereo + YOLO (placeholder, 20Hz)
  "stereoSegments": (True, 20., 20),   # PP-LiteSeg masks, 19-class (placeholder, 20Hz)
  "stereoStatus": (True, 2., 1),       # stereod status
  "pointcloudProcessed": (True, 5., 5), # pointcloudd: clean point cloud (objects removed)
  # Note: mapperd removed — alignedPointCloud, localMapPose, mapperStatus deprecated
  "osmCorrectedPose": (True, 10., 10),  # coordinationd OSM module: map-matched pose
  "osmLocalizerStatus": (True, 1., 1),  # osm_localizer: status
  "drivableArea": (True, 20., 10),      # surfaced: BEV drivable area grid
  "surfaceStatus": (True, 20., 10),     # surfaced: surface quality + history
  "gridStatus": (True, 1., 1),          # gridd status (1Hz)

  # Inference scheduler services (inferenced)
  "inferencedStatus": (True, 1., 1),       # inferenced backend health (1Hz)
  "rgaStatus": (True, 1., 1),             # RGA hardware accelerator health (1Hz)
  "mppStatus": (True, 1., 1),             # MPP video encoder health (1Hz)
  "inferenceJobRequest": (True, 100., 1), # Job submission: daemon → inferenced
  "inferenceJobResult": (True, 100., 1),  # Job result: inferenced → daemon

  # Point cloud recording (pointcloudd)
  "pointcloudStatus": (True, 1., 1),   # pointcloudd I/O health (1Hz)

  # Power management and safety services (EXO platforms)
  "impactEvent": (True, 0., 1),        # Impact detection event (immediate, LSM6DS3)

  # Audio services — adaptive loudness + Piper TTS navigation (both platforms)
  "micStatus": (True, 1., 1),          # micd: SPL level for adaptive loudness
  "ttsRequest": (False, 0.),           # soundd: Piper TTS request (nav + alerts)
  "audioData": (False, 100.),          # soundd → spkd: raw PCM audio chunks
  "sounddStatus": (True, 1., 1),       # soundd: playback health
  "spkdStatus": (True, 1., 1),         # spkd: I2S output health
  "blindSpotAlert": (True, 10., 10),   # BSD: Blind spot detection alert (10Hz)

  # Side camera perception (sided)
  "sideDetections": (True, 20., 5),    # sided: side camera detections
  "sideStatus": (True, 2., 1),         # sided: daemon status
  # Rear camera perception (reard)
  "rearDetections": (True, 20., 5),    # reard: rear camera detections
  "rearStatus": (True, 2., 1),         # reard: daemon status

  # EOP platform status daemons (publishers: hardwared, wdgd, rtcd, imud, networkd)
  "powerState": (True, 2., 1),
  "wdgState": (True, 1., 1),
  "rtcStatus": (True, 1., 1),
  "temperature": (True, 2., 10),
  "networkState": (True, 1., 1),

  # Voice pipeline UI feeds (subscribed by native ui; published by voice daemons)
  "voiceState": (True, 2., 1),
  "ttsStatus": (True, 2., 1),
}
SERVICE_LIST = {name: Service(*vals) for name, vals in _services.items()}


def build_header():
  h = ""
  h += "/* THIS IS AN AUTOGENERATED FILE, PLEASE EDIT services.py */\n"
  h += "#ifndef __SERVICES_H\n"
  h += "#define __SERVICES_H\n"

  h += "#include <map>\n"
  h += "#include <string>\n"

  h += "struct service { std::string name; bool should_log; int frequency; int decimation; };\n"
  h += "static std::map<std::string, service> services = {\n"
  for k, v in SERVICE_LIST.items():
    should_log = "true" if v.should_log else "false"
    decimation = -1 if v.decimation is None else v.decimation
    h += f'  {{ "{k}", {{"{k}", {should_log}, {v.frequency}, {decimation}}}}},\n'
  h += "};\n"

  h += "#endif\n"
  return h


if __name__ == "__main__":
  print(build_header())

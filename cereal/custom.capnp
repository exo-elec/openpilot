using Cxx = import "./include/c++.capnp";
$Cxx.namespace("cereal");

@0xb526ba661d550a59;

# custom.capnp: a home for empty structs reserved for custom forks
# These structs are guaranteed to remain reserved and empty in mainline
# cereal, so use these if you want custom events in your fork.

# DO rename the structs
# DON'T change the identifier (e.g. @0x81c2f05a394cf4af)

struct CustomReserved0 @0x81c2f05a394cf4af {
}

struct CustomReserved1 @0xaedffd8f31e7b55d {
}

struct AlccState @0xf35cc4560bbf6ec2 {
  state @0 :AlccStateEnum;
  enabled @1 :Bool;
  active @2 :Bool;
  available @3 :Bool;

  enum AlccStateEnum {
    disabled @0;
    paused @1;
    enabled @2;
    softDisabling @3;
    overriding @4;
  }
}

struct SpeedLimitState @0xda96579883444c35 {
  # Resolver is policy-only — offsets are applied inside MSLC (EOPMSLCOffsetPercent / EOPMSLCOffsetFixed).
  source @0 :SpeedLimitSource;
  limitMps @1 :Float32;
  distanceToChangeM @2 :Float32;
  active @3 :Bool;

  enum SpeedLimitSource {
    none @0;
    car @1;
    map @2;
    nav @3;
  }
}

struct CustomReserved4 @0x80ae746ee2596b11 {
}

struct CustomReserved5 @0xa5cd762cd951a455 {
}

struct CustomReserved6 @0xf98d843bfd7004a3 {
}

struct CustomReserved7 @0xb86e6369214c01c8 {
}

struct CustomReserved8 @0xf416ec09499d9d19 {
}

struct CustomReserved9 @0xa1680744031fdb2d {
}

struct CustomReserved10 @0xcb9fd56c7057593a {
}

struct CustomReserved11 @0xc2243c65e0340384 {
}

struct CustomReserved12 @0x9ccdc8676701b412 {
}

struct CustomReserved13 @0xcd96dafb67a082d0 {
}

struct CustomReserved14 @0xb057204d7deadf3f {
}

struct CustomReserved15 @0xbd443b539493bc68 {
}

struct CustomReserved16 @0xfc6241ed8877b611 {
}

struct CustomReserved17 @0xa30662f84033036c {
}

struct CustomReserved18 @0xc86a3d38d13eb3ef {
}

struct CustomReserved19 @0xa4f1eb3323f5f582 {
}

# EOP10 fork-specific structs
struct EopControlsState @0xa4b8f242816cbfcc {
}

struct ModelExt @0xd1d06c516b291666 {
}

struct LoopdState @0xbedce50b44bd7982 {
  # Deprecated: use RecorddState instead
}

struct RecorddState @0xbedce50b44bd7983 {
  # Unified recording daemon state (loopd + impactd + snapd)
  mode @0 :Text;           # "off", "normal", "timelapse", "event", "impact"
  recording @1 :Bool;      # Currently recording to disk
  fps @2 :Float32;         # Current recording FPS
  segmentDuration @3 :UInt32;  # Segment duration in seconds
  
  # Storage info
  storageUsedGB @4 :Float32;
  storageTotalGB @5 :Float32;
  clipsCount @6 :UInt32;
  eventsCount @7 :UInt32;
  
  # Impact detection
  impactEnabled @8 :Bool;
  impactSensitivity @9 :UInt8;   # 0-100 scale
  lastImpactTime @10 :Float64;
  lastImpactGForce @11 :Float32;
  
  # Pre-impact buffer
  preBufferSeconds @12 :UInt32;
  postBufferSeconds @13 :UInt32;
  
  # Stats
  framesEncoded @14 :UInt64;
  bytesWritten @15 :UInt64;
  segmentsSaved @16 :UInt32;
  eventsSaved @17 :UInt32;
  snapsSaved @18 :UInt32;
  
  # Current G-force for UI
  currentGForce @19 :Float32;
  inImpact @20 :Bool;
}

struct RecordersHealth @0xd8d93e2e2b58e0cb {
}

# Voice AI structs for EOP - Hailo-8 integration
struct WakeWordDetection @0xe1d0e5f2a3b4c5d6 {
  phrase @0 :Text;
  confidence @1 :Float32;
  timestamp @2 :UInt64;
  language @3 :Text;
}

struct VoiceResponse @0xa3f6e7d8c9b0a1d2 {
  text @0 :Text;
  priority @1 :Priority;
  language @2 :Text;
  
  enum Priority {
    low @0;
    normal @1;
    high @2;
    critical @3;
  }
}

struct VoiceStatus @0xb4a7c8d9e0f1a2b3 {
  wakeWordListening @0 :Bool;
  wakeWordDetected @1 :Bool;
  isListening @2 :Bool;
  isProcessing @3 :Bool;
  isSpeaking @4 :Bool;
  currentLanguage @5 :Text;
  errorCount @6 :UInt32;
}

# OBD2 structs for NCP v2.0
# bluetoothd ←→ obd2d communication

struct ObdCommand @0xc5d6e7f8a9b0c1d2 {
  command @0 :Text;       # ELM327 command string (e.g., "010C", "ATZ")
  timestamp @1 :UInt64;   # Command timestamp
  source @2 :Text;        # Source identifier (e.g., "ble")
}

struct ObdResponse @0xd6e7f8a9b0c1d2e3 {
  response @0 :Text;      # ELM327 response string
  success @1 :Bool;       # Command success
  timestamp @2 :UInt64;   # Response timestamp
  command @3 :Text;       # Original command (for matching)
}

struct ObdState @0xf1e2d3c4b5a69788 {
  engineRpm @0 :Float32;        # Engine RPM
  vehicleSpeed @1 :Float32;    # Vehicle speed (km/h)
  coolantTemp @2 :Float32;     # Coolant temperature (°C)
  intakeTemp @3 :Float32;      # Intake air temperature (°C)
  throttlePos @4 :Float32;     # Throttle position (%)
  engineLoad @5 :Float32;      # Engine load (%)
  fuelLevel @6 :Float32;       # Fuel level (%)
  obdConnected @7 :Bool;       # OBD2 connection status
  protocol @8 :UInt8;          # Active protocol number

  # Vehicle identification (populated once on detection)
  vin @9 :Text;                # Vehicle Identification Number
  vehicleType @10 :Text;       # 'generic_ice', 'generic_ev', 'byd', 'mg', etc.
  make @11 :Text;              # Vehicle manufacturer

  # EV / Mode 22 telemetry
  batterySoc @12 :Float32;     # Battery SOC (%)
  batterySoh @13 :Float32;     # Battery SOH (%)
  batteryVoltage @14 :Float32; # Battery voltage (V)
  batteryCurrent @15 :Float32; # Battery current (A), signed
  batteryTempMax @16 :Float32; # Battery max temperature (°C)
  batteryTempMin @17 :Float32; # Battery min temperature (°C)
  batteryPower @18 :Float32;   # Battery power (kW), signed
  chargingStatus @19 :UInt8;   # 0=off, 1=AC, 2=DC
  chargingPower @20 :Float32; # Charging power (kW)
  rangeRemaining @21 :Float32; # Range remaining (km)
  motorRpm @22 :Float32;       # Motor RPM
  motorTemp @23 :Float32;      # Motor temperature (°C)
  inverterTemp @24 :Float32;   # Inverter temperature (°C)
  auxBatteryVoltage @25 :Float32; # 12V aux battery voltage (V)
  odometer @26 :Float32;       # Odometer (km)
}

# Adaptive Driving State — published by adaptd, consumed by controlsd
# Dynamically adjusts driving personality based on OBD telemetry
struct AdaptiveDrivingState @0xb4ea0a039234a515 {
  enabled @0 :Bool;            # Adaptive driving active
  personality @1 :UInt8;       # Recommended LongitudinalPersonality (0-3)
  reason @2 :Text;             # Human-readable reason for recommendation
  reasonCode @3 :Text;         # Machine-readable reason code (e.g. "low_soc", "thermal")
  accelMax @4 :Float32;        # Max acceleration limit (m/s²), 0 = no limit
  decelMax @5 :Float32;        # Max deceleration limit (m/s²), 0 = no limit
  regenStrength @6 :Float32;   # Regen strength multiplier (0.0-1.0), EV only
  thermalDerating @7 :Bool;    # True when thermal limits active
  soc @8 :Float32;             # Current battery SOC (%), EV only
  rangeKm @9 :Float32;         # Current range remaining (km)
  batteryTemp @10 :Float32;    # Battery temperature (°C), EV only
  motorTemp @11 :Float32;      # Motor temperature (°C), EV only
  coolantTemp @12 :Float32;    # Coolant temperature (°C), ICE only
  timestamp @13 :UInt64;       # Mono time in nanoseconds
}

# NCP Vehicle Data — sent from NavPilot (phone) to device via BLE
# Contains interpreted OBD telemetry that NavPilot decoded from raw ELM327 responses
struct NcpVehicleData @0xbe2faa50e7c9e4d4 {
  valid @0 :Bool;              # Data is fresh and valid
  batterySoc @1 :Float32;      # Battery SOC (%)
  batterySoh @2 :Float32;      # Battery SOH (%)
  batteryVoltage @3 :Float32;  # Battery voltage (V)
  batteryCurrent @4 :Float32;  # Battery current (A)
  batteryTempMax @5 :Float32;  # Battery max temp (°C)
  batteryTempMin @6 :Float32;  # Battery min temp (°C)
  batteryPower @7 :Float32;    # Battery power (kW)
  chargingStatus @8 :UInt8;    # 0=off, 1=AC, 2=DC
  chargingPower @9 :Float32;   # Charging power (kW)
  rangeRemaining @10 :Float32; # Range remaining (km)
  motorRpm @11 :Float32;       # Motor RPM
  motorTemp @12 :Float32;      # Motor temperature (°C)
  inverterTemp @13 :Float32;   # Inverter temperature (°C)
  auxBatteryVoltage @14 :Float32; # 12V aux voltage (V)
  engineRpm @15 :Float32;      # Engine RPM (ICE)
  coolantTemp @16 :Float32;    # Coolant temperature (°C)
  throttlePos @17 :Float32;    # Throttle position (%)
  engineLoad @18 :Float32;     # Engine load (%)
  fuelLevel @19 :Float32;      # Fuel level (%)
  vehicleSpeed @20 :Float32;   # Vehicle speed (km/h)
  odometer @21 :Float32;       # Odometer (km)
  vin @22 :Text;               # Vehicle VIN
  vehicleType @23 :Text;       # Vehicle type string
  timestamp @24 :UInt64;       # Mono time (ns)
}

# Mono Detection structs for ExoPilot 01M
# monod - Road/wide camera YOLO + SceneSeg via RKNN NPU

struct MonoDetection @0xf8a9b0c1d2e3f4a5 {
  # Single detection from one camera
  trackId @0 :Int32;
  className @1 :Text;
  confidence @2 :Float32;
  cameraSource @3 :Text;       # "wide_road", "road", "tele_road", "drivevision_fused", "stereo_fused"
  
  # Image coordinates (normalized 0-1)
  u @4 :Float32;               # center x
  v @5 :Float32;               # center y
  w @6 :Float32;               # width
  h @7 :Float32;               # height
  
  # 3D position (road frame, ego-centered)
  x @8 :Float32;               # forward (meters)
  y @9 :Float32;               # left (meters)
  z @10 :Float32;              # up (meters)
  
  # Velocity (m/s)
  vx @11 :Float32;
  vy @12 :Float32;
  
  # Physical size (meters)
  width @13 :Float32;
  height @14 :Float32;
  
  # Distance from camera
  distance @15 :Float32;
  
  # BEV grid position (for gridd lazy BEV)
  gridX @16 :Int16;
  gridY @17 :Int16;
  
  # Uncertainty for probabilistic fusion in gridd
  sigmaX @18 :Float32;         # Position uncertainty forward (meters)
  sigmaY @19 :Float32;         # Position uncertainty lateral (meters)
}

struct MonoDetections @0xa9b0c1d2e3f4a5b6 {
  # Fused detections from all cameras
  frameId @0 :UInt32;
  timestamp @1 :Float64;
  detections @2 :List(MonoDetection);
  numTracks @3 :UInt16;
}

struct MonoSegment @0xb0c1d2e3f4a5b6c7 {
  # Segmentation mask for one camera
  camera @0 :Text;              # Camera name
  hasRoad @1 :Bool;
  hasEdge @2 :Bool;
  hasDrivable @3 :Bool;
  # Mask data would be compressed/encoded here
}

struct MonoSegments @0xc1d2e3f4a5b6c7d8 {
  frameId @0 :UInt32;
  segments @1 :List(MonoSegment);
}

struct MonoFeature @0xd2e3f4a5b6c7d8e9 {
  # Visual feature for BEV alignment
  trackId @0 :Int32;
  x @1 :Float32;               # Road frame X
  y @2 :Float32;               # Road frame Y
  camera @3 :Text;
  confidence @4 :Float32;
}

struct MonoFeatures @0xe3f4a5b6c7d8e9f0 {
  frameId @0 :UInt32;
  numTracks @1 :UInt16;
  features @2 :List(MonoFeature);
}

struct MonoStatus @0xf4a5b6c7d8e9f0a1 {
  enabled @0 :Bool;
  hailoActive @1 :Bool;
  hasTeleRoad @2 :Bool;
  numTracks @3 :UInt16;
  processingTimeMs @4 :Float32;

  # Fault state — mirrors VisionPilot hardware health / StereoStatus.fault pattern.
  # fault=True → monoFault event → IMMEDIATE_DISABLE + NO_ENTRY in selfdrived.
  fault @5 :Bool;
  faultReason @6 :Text;   # "npu_unavailable", "npu_timeout", "npu_consecutive_failures"
  consecutiveFailures @7 :UInt32;
}

# Impact detection structure for impactd (LSM6DS3 IMU)
struct IMUSample @0xd4e5c6b7a8f9e0f1 {
  timestamp @0 :UInt64;         # nanoseconds
  accelX @1 :Float32;           # m/s^2
  accelY @2 :Float32;
  accelZ @3 :Float32;
  gyroX @4 :Float32;            # rad/s
  gyroY @5 :Float32;
  gyroZ @6 :Float32;
}

struct ImpactEvent @0xe5f4d5c6b7a8e9f0 {
  timestamp @0 :UInt64;         # nanoseconds
  detected @1 :Bool;            # True if impact detected
  severity @2 :Float32;         # 0.0-1.0 severity score
  accelMagnitude @3 :Float32;   # Peak G-force (m/s^2)
  gyroMagnitude @4 :Float32;    # Peak angular rate (rad/s)
  deltaV @5 :Float32;           # Change in velocity (m/s)
  source @6 :Text;              # Detection source ("LSM6DS3", "can", "fusion")
  preImpactSamples @7 :UInt32;  # Number of pre-impact samples available
  
  # EOP additions for configurable sensitivity
  type @8 :Text;                # Impact type: "high_g", "jerk", "rollover"
  sensitivityLevel @9 :Int8;    # 0=Low(6G), 1=Medium(4.5G), 2=High(3G)
  thresholdG @10 :Float32;      # Threshold that was triggered
  isParkingMode @11 :Bool;      # True if detected in parking mode
  
  # Pre-impact IMU data (last 1 second = ~100 samples at 100Hz)
  # Available in BOTH normal and parking modes (IMU always runs at 100Hz)
  preImpactData @12 :List(IMUSample);
}

# Navigation confirmation structures for navconfirmd
struct NavConfirmation @0xf6a94b04adcc09d2 {
  # User confirmation responses for navigation maneuvers
  timestamp @0 :Float64;
  maneuverId @1 :Text;          # Unique maneuver identifier
  maneuverType @2 :Text;        # "lane_change", "exit", "merge", "turn"
  confirmed @3 :Bool;           # True if user confirmed
  denied @4 :Bool;              # True if user denied
  timeoutSec @5 :Float32;       # Time remaining to respond
  source @6 :Text;              # "user", "auto", "timeout"
}

struct NavGuidance @0xe1dc1c6e34161f6a {
  # Navigation guidance state from navconfirmd
  timestamp @0 :Float64;
  active @1 :Bool;
  currentStep @2 :UInt16;
  totalSteps @3 :UInt16;
  awaitingConfirmation @4 :Bool;
  currentManeuver @5 :Text;
  nextManeuver @6 :Text;
  distanceToManeuver @7 :Float32;  # meters
}

# Stereo depth structures for stereod
struct StereoDepthPoint @0xa0dea03b3a87a8db {
  x @0 :Float32;  # lateral (meters, right positive)
  y @1 :Float32;  # vertical (meters, down positive)
  z @2 :Float32;  # forward (meters)
}

struct StereoDepth @0xb6bdb722f54f3902 {
  # 2D stereo depth output (disparity map) - NO 3D reconstruction here
  timestamp @0 :UInt64;
  frameId @1 :UInt32;
  width @2 :UInt16;
  height @3 :UInt16;
  
  # Disparity map from SGM (2D offsets, GPU computed)
  # Consumers: gridd (BEV projection), pointcloudd (3D reconstruction)
  disparityMap @4 :Data;   # float32 array, HxW disparity values
  
  # Confidence map for disparity
  confidenceMap @5 :Data;  # float32 array, HxW confidence (0-1)
  
  # Basic stats
  meanDisparity @6 :Float32;
  validPixels @7 :UInt32;
}

# 3D object detections from stereo fusion (stereod, YOLO + depth)
struct Detection3D @0xe1f2a3b4c5d6e7f8 {
  className @0 :Text;           # "car", "truck", "person", etc.
  confidence @1 :Float32;       # 2D detection confidence (0-1)
  depthConfidence @2 :Float32;  # 3D depth estimate confidence (0-1)
  center @3 :StereoDepthPoint;  # 3D center position
  size @4 :StereoDepthPoint;    # 3D dimensions (length, width, height)
  yaw @5 :Float32;              # Heading angle (radians)
  bbox2d @6 :List(Float32);     # [x1, y1, x2, y2] in image coordinates
}

struct StereoDetections @0xa1c2d3e4f5a6b7c8 {
  timestamp @0 :UInt64;
  frameId @1 :UInt32;
  detections @2 :List(Detection3D);  # 3D object detections
}

# Per-pixel semantic segmentation from PP-LiteSeg (stereod, Cityscapes 19-class)
struct StereoSegments @0xa2c3d4e5f6a7b8c9 {
  timestamp @0 :UInt64;
  frameId @1 :UInt32;
  
  # Stereo right camera segmentation (stereo matching reference)
  stereoRightCamera @2 :Data;  # Compressed uint8 array (class indices 0-18)
  stereoRightWidth @3 :UInt16;
  stereoRightHeight @4 :UInt16;

  # Wide road camera segmentation (for broader context)
  wideRoadCamera @5 :Data;     # Compressed uint8 array (class indices 0-18)
  wideRoadWidth @6 :UInt16;
  wideRoadHeight @7 :UInt16;
}

struct StereoStatus @0xc8f4a5e2d3b1a907 {
  timestamp @0 :UInt64;
  enabled @1 :Bool;
  frameId @2 :UInt32;
  processingTimeMs @3 :Float32;
  droppedFrames @4 :UInt32;
  fps @5 :Float32;
  downscale @6 :UInt8;
  hasCalibration @7 :Bool;

  # Fault state — mirrors VisionPilot's hardware health pattern.
  # fault=True → stereoFault event → IMMEDIATE_DISABLE + NO_ENTRY in selfdrived.
  # Clears automatically when GPU recovers (no explicit ack required yet).
  fault @8 :Bool;
  faultReason @9 :Text;   # "gpu_unavailable", "gpu_timeout", "gpu_consecutive_failures"
  consecutiveFailures @10 :UInt32;  # Number of consecutive GPU SGM failures
}

# Grid Detection Status (gridd daemon)
struct GridStatus @0xd4e5f6a7b8c9d0e1 {
  timestamp @0 :UInt64;
  enabled @1 :Bool;
  frameId @2 :UInt32;
  processingTimeMs @3 :Float32;
  fps @4 :Float32;
  
  # Detection stats
  objectsDetected @5 :UInt32;  # Number of objects detected in last frame
  
  # Fault state — NPU inference failure tracking
  # fault=True → gridFault event → IMMEDIATE_DISABLE + NO_ENTRY in selfdrived
  fault @6 :Bool;
  faultReason @7 :Text;   # "npu_unavailable", "npu_timeout", "npu_consecutive_failures"
  consecutiveFailures @8 :UInt32;  # Number of consecutive NPU inference failures
}


# Voice Command Request from NavPilot via BLE
struct VoiceCommandRequest @0xd9e8f7a6b5c4d3e2 {
  # NCP v3.1 voice command from phone
  timestamp @0 :UInt64;        # nanoseconds
  commandId @1 :Text;          # Unique command ID from NavPilot
  action @2 :Text;             # Command action: "increaseSpeed", "decreaseSpeed", etc.
  params @3 :Text;             # JSON-encoded parameters
  source @4 :Text;             # "ble", "local_stt", "touch"
  processed @5 :Bool;          # Whether command has been processed
  resultCode @6 :Int16;        # 0=success, negative=error codes
  resultMessage @7 :Text;      # Human-readable result for TTS
  command @8 :Text;            # JSON-encoded full command payload (ncp_session transport)
}

# Point cloud recording daemon status (pointcloudd)
struct PointcloudStatus @0xb5c4d3e2f1a0b1c2 {
  timestamp @0 :UInt64;
  enabled @1 :Bool;
  frameId @2 :UInt32;
  savedFrames @3 :UInt32;        # Total frames written to disk
  droppedFrames @4 :UInt32;      # Frames dropped (queue full or I/O slow)
  storageUsedMb @5 :Float32;     # MB used in output directory
  storageLimitMb @6 :Float32;    # Configured storage limit in MB
  currentFile @7 :Text;          # Currently open file (MCAP) or last PCD written
  format @8 :Text;               # "pcd", "mcap", or "both"
  writeTimeMs @9 :Float32;       # Last write latency in ms
  hasGps @10 :Bool;              # GPS fix available for georeferencing
  
  # Fault state — I/O failures or storage issues
  # fault=True → pointcloudFault event → PERMANENT alert (recording stops, driving OK)
  fault @11 :Bool;
  faultReason @12 :Text;         # "io_write_failed", "storage_full", "consecutive_drops"
  consecutiveFailures @13 :UInt32;  # Number of consecutive write failures
  validPoints @14 :UInt32;       # Points in last frame after filtering
  isFiltered @15 :Bool;          # Statistical outlier removal was applied
}

# Inference hardware backend status (published by inferenced at 1Hz)
# RGA: Rockchip 2D Graphics Accelerator (image resize/format conversion)
struct RgaStatus @0xa1b2c3d4e5f6a7b9 {
  timestamp @0 :UInt64;
  hwAccelerated @1 :Bool;       # False = OpenCV fallback active (performance risk at 20Hz)
  fault @2 :Bool;               # True = was HW, now consecutively failing → SOFT_DISABLE
  faultReason @3 :Text;         # "rga_init_failed", "rga_consecutive_failures"
  consecutiveFailures @4 :UInt32;
  tasksCompleted @5 :UInt32;
  tasksFailed @6 :UInt32;
  avgExecTimeMs @7 :Float32;
}

# MPP: Rockchip Media Process Platform (H.264/H.265 encode for recordd)
struct MppStatus @0xb2c3d4e5f6a7b8c9 {
  timestamp @0 :UInt64;
  hwAvailable @1 :Bool;
  fault @2 :Bool;               # True = encode failing → PERMANENT alert (recording stops)
  faultReason @3 :Text;         # "mpp_unavailable", "mpp_encode_failed"
  consecutiveFailures @4 :UInt32;
  framesEncoded @5 :UInt32;
  encodeFailures @6 :UInt32;
}

# Inference Scheduler Status (published by inferenced at 1Hz)
# Aggregated health of all inference backends (NPU, GPU, etc.)
struct InferencedStatus @0xc3d4e5f6a7b8c9d0 {
  timestamp @0 :UInt64;
  enabled @1 :Bool;
  
  # Backend availability (snapshotted at init)
  npuAvailable @2 :Bool;        # RKNN NPU available
  gpuAvailable @3 :Bool;        # OpenCL GPU available
  
  # Overall fault state
  # fault=True when ALL backends are unavailable (no inference possible)
  fault @4 :Bool;
  faultReason @5 :Text;         # "all_backends_unavailable", "npu_failed", "gpu_failed"
  
  # Stats
  tasksCompleted @6 :UInt32;
  tasksFailed @7 :UInt32;
  avgExecTimeMs @8 :Float32;

  # Capability discovery (published at 1Hz, consumed by daemons to decide
  # whether optional/enhancement models can be scheduled).
  availableBackends @9 :List(Text);   # e.g. ["NPU", "EGPU", "HAILO_8"]
  availableModels @10 :List(Text);    # Model IDs loaded or loadable
  backendHealth @11 :Text;            # JSON snapshot of per-backend health
}

# =============================================================================
# Inference Job Submission & Results (for daemon-based resource management)
# =============================================================================

struct InferenceJobRequest @0xd1e2f3a4b5c6d7e8 {
  # Job submission from client daemon to inferenced daemon
  timestamp @0 :UInt64;
  jobId @1 :UInt32;              # Unique job ID for tracking
  daemonName @2 :Text;            # e.g. "modeld", "stereod"
  backendType @3 :UInt8;          # 1=NPU, 2=GPU, 3=CPU, 4=RGA, 5=MPP, 6=Hailo, 7=ONNX
  modelName @4 :Text;             # e.g. "driving_model"
  priority @5 :UInt8;             # 0=critical, 1=high, 2=normal, 3=low
  timeoutMs @6 :UInt32;           # Max execution time in ms
  inputData @7 :Data;             # Serialized input tensor (numpy .tobytes())
  inputShape @8 :List(UInt32);    # Tensor shape, e.g. [1, 12, 128, 256]
  inputDtype @9 :Text;            # Numpy dtype string, e.g. "float32"
}

struct InferenceJobResult @0xe8f9a0b1c2d3e4f5 {
  # Job result from inferenced daemon back to client
  timestamp @0 :UInt64;
  jobId @1 :UInt32;               # Matches request jobId
  success @2 :Bool;               # True if inference succeeded
  executionTimeMs @3 :Float32;    # Actual execution time
  errorReason @4 :Text;           # Error description if success=False
  resultSize @5 :UInt32;          # Size of result data (if stored externally)
  outputData @6 :Data;            # Serialized output tensor (numpy .tobytes())
  outputShape @7 :List(UInt32);   # Output tensor shape
  outputDtype @8 :Text;           # Output numpy dtype string
}

# =============================================================================
# Point Cloud Processing (pointcloudd daemon)
# =============================================================================

struct PointcloudProcessed @0xf3e4d5c6b7a80913 {
  # Clean point cloud from pointcloudd (objects removed)
  # Published at reduced rate (5Hz) for surfaced/sgm_localizer to consume.
  # Points are in ego (camera) frame. GeoPosition carries the vehicle pose
  # at capture time so consumers have a single time-aligned message.
  timestamp @0 :UInt64;
  frameId @1 :UInt32;

  points @2 :List(Point3D);
  validPoints @3 :UInt32;

  wasFiltered @4 :Bool;      # True if dynamic objects were removed
  source @5 :Text;           # "pointcloudd_3d"

  # Vehicle geo-pose at capture time (from fusedPosition or gpsLocationExternal)
  # Allows consumers (sgm_localizer, map builder) to geo-reference points
  # without a separate GPS subscription or timestamp skew.
  hasGeoPosition @6 :Bool;
  geoLat @7 :Float64;        # degrees
  geoLon @8 :Float64;        # degrees
  geoAlt @9 :Float32;        # meters above WGS84
  geoHeading @10 :Float32;   # radians, NED yaw (0=North, clockwise)
  geoAccuracy @11 :Float32;  # horizontal accuracy in meters (0 = unknown)
  geoSource @12 :Text;       # "fused", "gps", "none"
}

struct Point3D @0xe2f3a4b5c6d7e8f9 {
  x @0 :Float32;  # forward
  y @1 :Float32;  # left
  z @2 :Float32;  # up
}

# =============================================================================
# Mapper (DEPRECATED — mapperd daemon removed)
# =============================================================================
# Note: AlignedPointCloud, LocalMapPose, MapperStatus structs removed.
# Local map building can be added to mapd if NDT localization needed in future.

# =============================================================================
# OSM Localization (osm_localizer daemon)
# =============================================================================

struct OsmCorrectedPose @0xf586d9e1d15ad461 {
  # Map-corrected pose from OSM localizer
  timestamp @0 :UInt64;
  frameId @1 :UInt32;
  
  # Original GPS (from EKF)
  originalLat @2 :Float64;
  originalLon @3 :Float64;
  
  # Corrected (map-matched)
  correctedLat @4 :Float64;
  correctedLon @5 :Float64;
  correctedHeading @6 :Float32;  # degrees
  
  # Road information
  wayId @7 :Int64;
  roadName @8 :Text;
  roadType @9 :Text;        # motorway, primary, etc.
  laneIndex @10 :UInt8;     # 0=leftmost
  isOneway @11 :Bool;
  
  # Quality metrics
  confidence @12 :Float32;      # 0-1
  distanceToRoad @13 :Float32;  # meters
}

struct OsmLocalizerStatus @0xf2a3b4c5d6e7f8a9 {
  timestamp @0 :UInt64;
  frameId @1 :UInt32;
  enabled @2 :Bool;
  hasMatch @3 :Bool;
  matchCount @4 :UInt32;
  matchFailureCount @5 :UInt32;
}

# =============================================================================
# SGM Localization (sgm_localizer daemon, 10Hz)
# =============================================================================

struct SgmCorrectedPose @0x992d7d11534711b8 {
  # SGM-based pose correction from stereo pointcloud matching
  position @0 :Position;
  orientation @1 :Orientation;
  confidence @2 :Float32;   # Match confidence (0-1)
  inliers @3 :Int32;        # Number of inlier points
  mapSource @4 :Text;       # 'SGM', 'OSM', 'FUSED'

  struct Position {
    x @0 :Float32;
    y @1 :Float32;
    z @2 :Float32;
  }

  struct Orientation {
    x @0 :Float32;
    y @1 :Float32;
    z @2 :Float32;
    w @3 :Float32;
  }
}

# =============================================================================
# Coordination Daemon (coordinationd, 5Hz)
# =============================================================================

struct FusedPosition @0x9822884ca2e8e0f5 {
  # Fused ECEF position from coordinationd
  # Combines GNSS + OSM + SGM for globally-consistent position

  positionECEF @0 :XYZMeasurement;
  positionGeodetic @1 :Geodetic;

  # Source information
  source @2 :Text;          # 'gnss', 'fusion', 'dead_reckoning'
  confidence @3 :Float32;   # 0-1 overall confidence

  # Road constraint info
  onRoad @4 :Bool;          # True if on known road
  roadWidth @5 :Float32;    # Road width in meters (0 if unknown)

  struct Geodetic {
    latitude @0 :Float64;
    longitude @1 :Float64;
    altitude @2 :Float32;
  }

  struct XYZMeasurement {
    x @0 :Float64;
    y @1 :Float64;
    z @2 :Float64;
    xStd @3 :Float32;
    yStd @4 :Float32;
    zStd @5 :Float32;
  }
}

# =============================================================================
# Drivable Area (surfaced daemon output)
# =============================================================================

struct DrivableArea @0xa4c5d6e7f8a9b0cb {
  # BEV drivable area grid from surfaced
  # Enhanced with historical data
  timestamp @0 :UInt64;
  frameId @1 :UInt32;
  
  # Grid configuration
  resolution @2 :Float32;     # meters per cell
  width @3 :UInt16;          # cells
  height @4 :UInt16;         # cells
  originX @5 :Float32;       # meters (lateral offset)
  originY @6 :Float32;       # meters (forward offset)
  
  # Grid data: 0=unknown, 1=drivable, 2=occupied, 3=rough, 4=learned_rough
  data @7 :Data;
  
  # Statistics
  numDrivable @8 :UInt32;
  numOccupied @9 :UInt32;
  numRough @10 :UInt32;
  numLearnedRough @11 :UInt32;
  hasHistoryEnhancement @12 :Bool;
  
  # Clearances from boundary detection (NEW)
  leftClearanceM @13 :Float32;   # Distance to left road boundary
  rightClearanceM @14 :Float32;  # Distance to right road boundary
  frontClearanceM @15 :Float32;  # Distance to obstacle ahead
  
  # Surface quality from current frame (NEW)
  hasSurfaceQuality @16 :Bool;
  surfaceQuality @17 :SurfaceQuality;
}

# =============================================================================
# Surface Perception (surfaced daemon status)
# =============================================================================

struct SurfaceShock @0xb1c2d3e4f5a6b7c8 {
  # Detected surface shock/obstacle
  type @0 :Text;           # "bump", "pothole", "rough_patch"
  source @1 :Text;         # "imu" (already hit) or "stereo" (ahead)
  distanceM @2 :Float32;   # 0 = now, >0 = meters ahead
  lateralM @3 :Float32;    # Offset from center line
  severity @4 :Float32;    # 0-1 (higher = more severe)
  confidence @5 :Float32;  # 0-1 detection confidence
}

struct SurfaceQuality @0xa5c6d7e8f9a0b1cc {
  # Road surface quality assessment
  score @0 :Float32;       # 0-1 (0=smooth, 1=rough)
  texture @1 :Text;        # "smooth", "normal", "rough", "very_rough"
  roughnessRms @2 :Float32; # RMS acceleration m/s²
  confidence @3 :Float32;  # 0-1 based on sample count
}

struct SurfacePrediction @0xd3e4f5a6b7c8d9e0 {
  # Predictive surface quality from GPS history
  distanceM @0 :Float32;       # Distance ahead (50, 100, 200, 300m)
  qualityScore @1 :Float32;    # 0-1 roughness
  texture @2 :Text;            # Predicted texture
  featureType @3 :Text;        # "pothole_cluster", "rough_road", etc.
  recommendedSpeedMs @4 :Float32;  # Learned driver speed
  confidence @5 :Float32;      # 0-1 based on pass count
}

struct SurfaceStatus @0xa6c7d8e9f0a1b2cd {
  # Unified surface status from surfaced daemon (20Hz)
  timestamp @0 :UInt64;
  
  # Real-time data (0-80m range)
  hasShocks @1 :Bool;
  shocks @2 :List(SurfaceShock);
  
  hasSurfaceQuality @3 :Bool;
  surfaceQuality @4 :SurfaceQuality;
  
  # Predictive data (80-300m from GPS history)
  hasPredictions @5 :Bool;
  predictions @6 :List(SurfacePrediction);
  
  # Convenience: most restrictive ahead
  recommendedSpeedMs @7 :Float32;  # 0 = no limit
  nextRoughDistanceM @8 :Float32;  # Distance to next rough patch
}

# =============================================================================
# Bluetooth SPP (Serial Port Profile) structs
# =============================================================================

struct SppStatus @0xa6b5c4d3e2f1a0b1 {
  # SPP connection status from SPPD
  timestamp @0 :UInt64;
  serverRunning @1 :Bool;      # SPP server is listening
  numClients @2 :UInt8;        # Number of connected clients
  maxClients @3 :UInt8;        # Maximum allowed clients
  bytesSent @4 :UInt64;        # Total bytes sent
  bytesReceived @5 :UInt64;    # Total bytes received
  channel @6 :UInt8;           # RFCOMM channel
}

# =============================================================================
# Voice Assistant & AI (ExoPilot 02M)
# =============================================================================

struct VoiceState @0xb7c6d5e4f3a2b1c3 {
  # Voice assistant state from voiced
  state @0 :VoiceAssistantState;
  enum VoiceAssistantState {
    idle @0;
    listening @1;       # Wake word detected, recording
    processing @2;       # STT -> Intent pipeline
    clarifying @3;       # Need more info from user
    responding @4;       # TTS playing
  }
  
  transcript @1 :Text;       # What user said
  confidence @2 :Float32;    # STT confidence
  language @3 :Text;         # Detected language
  intent @4 :Text;           # Resolved intent
  reply @5 :Text;            # Assistant's reply
  
  intentSource @6 :IntentSource;
  enum IntentSource {
    dict @0;      # Tier 1: Regex match
    llm @1;       # Tier 2: Local LLM
    unknown @2;   # No match / fallback
  }
}

struct VoiceIntent @0xc8d7e6f5a4b3c2d4 {
  # Resolved voice intent
  action @0 :Text;
  params @1 :List(ParamEntry);
  confidence @2 :Float32;
  reply @3 :Text;
  source @4 :VoiceState.IntentSource;
  language @5 :Text;

  struct ParamEntry {
    key @0 :Text;
    value @1 :Text;
  }
}

struct TtsRequest @0xd9e8f7a6b5c4d3e5 {
  # TTS request from voiced to audiod
  text @0 :Text;
  priority @1 :Int8;         # 0=critical, 1=high, 2=normal
  interrupt @2 :Bool;        # Allow barge-in
  language @3 :Text;         # Optional language hint
}

struct MicStatus @0xfba019c8d7e6f5a7 {
  # Microphone/voice input status from voiced
  wakeWordActive @0 :Bool;   # Wake word detection running
  vadActive @1 :Bool;        # Voice activity detected
  sttActive @2 :Bool;        # STT processing
  micLevelDb @3 :Float32;    # For UI visualization
  doa @4 :Float32;           # Direction of arrival (degrees, 0=front)
}

struct AudioStatus @0xacb120d9e8f7a6b8 {
  # Audio output status from audiod (single speaker, mono)
  ttsPlaying @0 :Bool;
  ttsBufferLevel @1 :Float32;    # 0.0-1.0
}

struct BlindSpotAlert @0xdfe4530cb12ad9eb {
  # Blind Spot Detection alert state
  leftAlertLevel @0 :Int8;     # 0=off, 1=caution, 2=warning
  rightAlertLevel @1 :Int8;    # 0=off, 1=caution, 2=warning
  leftDetected @2 :Bool;
  rightDetected @3 :Bool;
  leftDistance @4 :Float32;     # meters
  rightDistance @5 :Float32;    # meters
  leftRelativeSpeed @6 :Float32;  # m/s
  rightRelativeSpeed @7 :Float32; # m/s
  chimeRequest @8 :Bool;        # True when warning chime should play
  alertMessage @9 :Text;         # Human-readable alert message

  # EOP: Rear Cross Traffic Alert (RCTA) — rear camera + Hailo-8 (when present)
  rearCrossTrafficDetected @10 :Bool;
  rearCrossTrafficAlertLevel @11 :Int8;  # 0=off, 1=caution, 2=warning
  rearCrossTrafficDistance @12 :Float32;  # meters
  rearCrossTrafficRelativeSpeed @13 :Float32; # m/s

  # EOP: LCA gate — wide zone TTC-based block (up to 100m behind in adjacent lane).
  # No chime/icon — used only by desire_helper to gate lane-change requests.
  # Distinct from leftDetected/rightDetected which cover only the immediate ±15m zone.
  lcaBlockedLeft  @14 :Bool;
  lcaBlockedRight @15 :Bool;
}

struct UiCommand @0xf2b7863fe45d0c1e {
  # UI control command from intentd to ui
  action @0 :Action;
  enum Action {
    showCard @0;
    openSettings @1;
    closeSettings @2;
    openPanel @3;
    closePanel @4;
  }
  cardIndex @1 :Int8;      # For showCard action
  panelName @2 :Text;      # For openPanel/closePanel actions
}

struct BargeIn @0xf3c8974fe56e1d2f {
  # Barge-in detection event
  triggered @0 :Bool;
  timestamp @1 :UInt64;
  vadLevel @2 :Float32;
}

struct TtsStatus @0xeaf908b7c6d5e4f6 {
  # TTS playback status from audiod
  playing @0 :Bool;
  text @1 :Text;
  progress @2 :Float32;  # 0.0 to 1.0
}

struct SideDetections @0xae03cd6c006d8071 {
  # Detections from side cameras (side_left, side_right)
  frameId @0 :UInt32;
  timestamp @1 :Float64;
  detections @2 :List(MonoDetection);
  numTracks @3 :UInt16;
  cameraSource @4 :Text;       # "side_left", "side_right", "both"
}

struct SideStatus @0xfc2fc76d6342a4bf {
  enabled @0 :Bool;
  fault @1 :Bool;
  faultReason @2 :Text;
  consecutiveFailures @3 :UInt32;
  numTracks @4 :UInt16;
  processingTimeMs @5 :Float32;
}

struct RearDetections @0xfd4fd87e7453b5d1 {
  # Detections from rear camera (RCTA)
  frameId @0 :UInt32;
  timestamp @1 :Float64;
  detections @2 :List(MonoDetection);
  numTracks @3 :UInt16;
  cameraSource @4 :Text;       # "rear"
}

struct RearStatus @0xfe5fe98f8564c6e2 {
  enabled @0 :Bool;
  fault @1 :Bool;
  faultReason @2 :Text;
  consecutiveFailures @3 :UInt32;
  numTracks @4 :UInt16;
  processingTimeMs @5 :Float32;
}

struct DriverStatus @0xfc3fc76d6342a4c0 {
  enabled @0 :Bool;
  faceDetected @1 :Bool;
  faceForward @2 :Bool;
  steeringActive @3 :Bool;
  attentionProb @4 :Float32;
  faceState @5 :Text;
  backendName @6 :Text;
  hailoAvailable @7 :Bool;
  opencvAvailable @8 :Bool;
  backendFault @9 :Bool;
  faceX @10 :Float32;
  faceY @11 :Float32;
  faceYaw @12 :Float32;
  facePitch @13 :Float32;
  faceRoll @14 :Float32;
  occupantGuardActive @15 :Bool;
  tooDistracted @16 :Bool;
  adaptiveScale @17 :Float32;
  safeSpeedLimitMps @18 :Float32;
  unconsciousActive @19 :Bool;
}

struct DriverDistractionState @0xfc3fc76d6342a4d0 {
  active @0 :Bool;
  speedLimitMps @1 :Float32;
  tooDistracted @2 :Bool;
  attentionProb @3 :Float32;
}

# EOP: Face pose detection state (sunglasses-invariant, lightweight NPU)
# NOTE: FacePoseState is defined in log.capnp because it references OnroadEvent

struct VehicleState @0xab3bc87d7453c6e3 {
  state @0 :Text;           # "off", "accessory", "on", "running", "parking"
  ignition @1 :Bool;
  engineRunning @2 :Bool;
  parkingMode @3 :Bool;
  standstill @4 :Bool;
}

struct EopThermalZone @0xbc4cd98e8564d7f4 {
  name @0 :Text;
  temp @1 :Float32;
}

struct EopThermalStatus @0xcd5de0a9f675e8a5 {
  cpu @0 :Float32;
  gpu @1 :Float32;
  mem @2 :Float32;
  fanSpeed @3 :Int32;
  thermalThrottling @4 :Bool;
  thermalZones @5 :List(EopThermalZone);
}

# ---- EOP: Radar pipeline structs ----

struct Radar4DPoint @0xa1b2c3d4e5f60718 {
  # BGT60TR13C 60GHz FMCW short-range 4D radar point.
  # Full polar measurement — position derived in gridd._fuse_radar4d(), not stored here.
  trackId       @0 :UInt64;   # stable ID (confirm/drop hysteresis in radar4d_tracker.py)
  rangM         @1 :Float32;  # radial distance (m); BGT60 range res = 2.7 cm
  azimuth       @2 :Float32;  # angle (deg, 0=forward, +left, -right); dual-baseline phase AoA
  vRel          @3 :Float32;  # Doppler relative velocity (m/s); NEGATIVE = approaching
  snrDb         @4 :Float32;  # peak SNR dB — proxy for radar cross section (RCS)
  elevation     @5 :Float32;  # angle (deg, 0=boresight, +up); dual-baseline phase AoA
  existenceProb @6 :Float32;  # 0-100, from tracker confirm hit-streak (radar4d_tracker.Track)
  isStatic      @7 :Bool;     # ego-velocity compensated: true = stationary clutter
  dynProp       @8 :UInt8;    # ARS-style: 0=stationary, 1=moving, 2=stopped
  aRel          @9 :Float32;  # longitudinal relative acceleration (m/s^2), negative = braking
}

struct Radar4DObject @0xb3c4d5e6f7a80921 {
  # Lidar-style object produced by clustering radar4d point tracks.
  # Mirrors Autoware DetectedObject / cluster shape for camera-radar fusion.
  trackId       @0 :UInt64;   # stable cluster/track ID
  rangM         @1 :Float32;  # radial distance to object center (m)
  azimuth       @2 :Float32;  # deg, 0=forward, +left, -right
  elevation     @3 :Float32;  # deg, 0=boresight, +up
  vRel          @4 :Float32;  # Doppler relative velocity (m/s), NEGATIVE = approaching
  aRel          @5 :Float32;  # longitudinal relative acceleration (m/s^2)
  snrDb         @6 :Float32;  # peak SNR dB of cluster
  existenceProb @7 :Float32;  # 0-100, from cluster tracker confirm hit-streak
  isStatic      @8 :Bool;     # ego-velocity compensated: true = stationary clutter
  dynProp       @9 :UInt8;    # ARS-style: 0=stationary, 1=moving, 2=stopped
  lengthM       @10 :Float32; # estimated object length (m), forward axis
  widthM        @11 :Float32; # estimated object width (m), lateral axis
  heightM       @12 :Float32; # estimated object height (m), vertical axis
  yawRad        @13 :Float32; # estimated object heading (rad), 0=forward
  pointCount    @14 :UInt8;   # number of radar points in the cluster
}

struct Radar4D @0xf2a3b4c5d6e7f8e1 {
  # Socket: radar4d  |  Published by: selfdrive/controls/radar4d.py
  # Consumed by: selfdrive/gridd/gridd.py (_fuse_radar4d)
  points  @0 :List(Radar4DPoint);
  objects @1 :List(Radar4DObject);  # lidar-style clustered objects (new)
  # Environment inference from the raw return cloud (radar4d_pointcloud.py):
  precipProb    @2 :Float32;  # 0-1 rain/snow clutter probability (EMA-smoothed)
  wiperOn       @5 :Bool;     # wiper sweeps detected = driver-confirmed wet road
  glassContaminated @6 :Bool; # water film/snow on the windshield attenuating the radar
  weatherSeverity @7 :UInt8;  # 0=clear, 1=light, 2=moderate, 3=heavy (rain/snow/mud)
  visionBlocked @8 :Bool;     # contamination film blocks all far returns — road not visible to radar
  dropOffHazard @3 :Bool;     # negative obstacle ahead (cliff edge / ditch)
  dropOffDistM  @4 :Float32;  # distance to the drop-off evidence (m, 0 = none)
}

struct Radar2DReturn @0xd2a3b4c5e6f70819 {
  # Corner/blind-spot radar return.
  # No position — presence detection only + optional approaching speed.
  side    @0 :UInt8;    # 0=left-front, 1=left-rear, 2=right-front, 3=right-rear
  present @1 :Bool;     # something detected in coverage zone
  vRel    @2 :Float32;  # approaching speed (m/s, negative=approaching); NaN if unavailable
}

struct Radar2DObject @0xc4d5e6f7a8092131 {
  # Tracked object from an ESP32-S3 corner radar node (on-node Kalman tracker
  # with occlusion coasting — see ESP32_RADAR repo).  Polar position is in the
  # corner node's own frame; gridd converts to vehicle frame with the
  # per-corner mounting pose.
  trackId       @0 :UInt64;   # stable track ID from the on-node tracker
  corner        @1 :UInt8;    # 0=FL, 1=FR, 2=RL, 3=RR — matches ESP32_RADAR radar_corner_id_t
  rangM         @2 :Float32;  # radial distance to object center (m)
  azimuthDeg    @3 :Float32;  # deg, 0=sensor boresight, +left, -right
  vRel          @4 :Float32;  # Doppler relative velocity (m/s), NEGATIVE = approaching
  aRel          @5 :Float32;  # relative acceleration (m/s^2), NaN if unavailable
  snrDb         @6 :Float32;  # peak SNR dB — proxy for radar cross section (RCS)
  existenceProb @7 :Float32;  # 0-100, from tracker confirm hit-streak (same convention as Radar4DObject)
  measured      @8 :Bool;     # false = coasted through occlusion, predict-only this frame
  dynProp       @9 :UInt8;    # ARS-style: 0=stationary, 1=moving, 2=stopped
  lengthM       @10 :Float32; # estimated object length (m), forward axis
  widthM        @11 :Float32; # estimated object width (m), lateral axis
  ttcS          @12 :Float32; # node-computed time-to-collision (s), NaN if unavailable.
                              # The NODE computes this, not us, and that is not an
                              # arbitrary split: the fields above carry polar position
                              # plus RADIAL Doppler only, and radial rate cannot separate
                              # an object converging on us from one crossing harmlessly
                              # past. That needs the full Cartesian [vx,vy] the node's
                              # Kalman tracker holds and does not transmit, so the
                              # trajectory judgement is made on-node and shipped.
                              # Direction-agnostic by construction — a perpendicular
                              # pass yields NaN, a diagonal cut-in yields a real TTC,
                              # whatever bearing it arrives from — and independent of
                              # mounting pose, which matters on an aftermarket install
                              # whose yaw is not known.
                              # NOT tiered: ISO 17387 sets the closing-vehicle-warning
                              # criterion at 2.5/3.0/3.5 s TTC selected by ego speed, and
                              # ego speed is authoritative HERE, not on the node (whose
                              # vehicle_state input is optional and freshness-gated), so
                              # threshold selection belongs to this side. See
                              # selfdrive/controls/lib/radar_zones.py.
                              # Constant-velocity extrapolation, inherited from the
                              # node's KF: a warning signal, not a safety interlock.
  ttcValid      @13 :Bool;    # true = the sender computes ttcS, so it is authoritative
                              # INCLUDING an absent/NaN value, which then means "node
                              # evaluated this object and it is NOT closing" rather than
                              # "no opinion". A consumer must not substitute its own
                              # range/radial-rate estimate in that case — that estimate
                              # is what over-alarms on crossing traffic, so doing so
                              # re-alarms precisely what the node just cleared.
                              # false = sender predates TTC; keep the local estimate.
}

struct Radar2D @0xe3f4a5b6c7d80920 {
  # Socket: radar2d  |  ESP32-S3 corner radars (on-node tracked objects)
  # objects carries real corner-tracked objects when the node tracker is
  # running; returns stays as the legacy zone-presence fallback.
  returns @0 :List(Radar2DReturn);
  objects @1 :List(Radar2DObject);
}

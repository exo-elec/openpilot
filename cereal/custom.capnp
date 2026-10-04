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

struct CustomReserved2 @0xf35cc4560bbf6ec2 {
}

struct CustomReserved3 @0xda96579883444c35 {
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

# Mono Detection structs (same ids and ordinals as EOP10).
# NagasPilot: monod runs a YOLO detector on the road camera on the GPU (nagaspilot/runtime/monod.py).

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

  # Traffic lights (className "traffic light", road camera): lamp colour from
  # monod's HSV classifier. 0 unknown, 1 red, 2 yellow, 3 green -- the
  # ordinals of log.capnp's CameraObject.TrafficLightState.
  trafficLightState @20 :UInt8;
  trafficLightConfidence @21 :Float32;
}

struct MonoDetections @0xa9b0c1d2e3f4a5b6 {
  # Fused detections from all cameras
  frameId @0 :UInt32;
  timestamp @1 :Float64;
  detections @2 :List(MonoDetection);
  numTracks @3 :UInt16;
  modelExecutionTime @4 :Float32;   # seconds, detector forward pass only
}

# pathd add-on layer (NagasPilot): bounded lateral offset and speed factor chosen around the policy path.
# Publish-only until the replay proof; EOP10 maps these fields onto its own enhancedTrajectory.
struct PathAdjust @0x86ee74962cb31a1d {
  frameId @0 :UInt32;
  offsetM @1 :Float32;            # left positive, added to the policy path
  speedFactor @2 :Float32;        # <= 1.0, only ever lowers speed
  minClearanceM @3 :Float32;      # worst lateral clearance at the chosen offset (NaN: no object)
  reason @4 :Text;                # off | clear | nudge | slow
  roomLeftM @5 :Float32;
  roomRightM @6 :Float32;
  numObjects @7 :UInt16;
  # Horizon profile of the same request (append-only fields): element i is (i+1) * horizonDt seconds ahead.
  horizonDt @8 :Float32;
  offsetProfile @9 :List(Float32);      # lateral offsets, left positive
  speedCapProfile @10 :List(Float32);   # m/s, never above the current speed
  # Parallel rule channel (RulePlanner + DPP, append-only fields)
  ruleValid @11 :Bool;
  ruleCurvature @12 :Float32;           # 1/m, left positive
  ruleAccel @13 :Float32;               # m/s^2
  ruleSpeedTarget @14 :Float32;         # m/s
  dppMode @15 :UInt8;                   # ngp_policy_arbiter.Mode: 0 off 1 shadow 2 supervise 3 primary_long 4 primary_lat 5 primary_both
  dppCase @16 :Text;                    # why DPP chose it
  disagreeCurvature @17 :Float32;       # rule - policy
  disagreeAccel @18 :Float32;
}

# adaptd (shared with EOP10: same struct ids and field ordinals)
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


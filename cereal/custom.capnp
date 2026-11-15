using Cxx = import "/include/c++.capnp";
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

# mapd (pfeiferj/openpilot-mapd v2.4.0, third_party/mapd): replaces CustomReserved17-19, same struct ids.
struct MapdDownloadLocationDetails @0xff889853e7b0987f {
  location @0 :Text;
  totalFiles @1 :UInt32;
  downloadedFiles @2 :UInt32;
}

struct MapdDownloadProgress @0xfaa35dcac85073a2 {
  active @0 :Bool;
  cancelled @1 :Bool;
  totalFiles @2 :UInt32;
  downloadedFiles @3 :UInt32;
  locations @4 :List(Text);
  locationDetails @5 :List(MapdDownloadLocationDetails);
}

struct MapdPathPoint @0xd6f78acca1bc3939 {
  latitude @0 :Float64;
  longitude @1 :Float64;
  curvature @2 :Float32;
  targetVelocity @3 :Float32;
}

struct MapdPosition @0xde9705979aca8339 {
  latitude @0 :Float64;
  longitude @1 :Float64;
}

struct MapdExtendedOut @0xa30662f84033036c {
  downloadProgress @0 :MapdDownloadProgress;
  settings @1 :Text;
  path @2 :List(MapdPathPoint);
  position @3 :MapdPosition;
  loopRateAverage @4 :Float32;
  loopRateMin @5 :Float32;
}

enum MapdInputType {
  download @0;
  reloadSettings @9;
  saveSettings @10;
  loadDefaultSettings @21;
  loadRecommendedSettings @22;
  loadPersistentSettings @26;
  cancelDownload @27;
  setJsonPathFloat @43;
  setJsonPathText @44;
  setJsonPathBool @45;
  acceptSpeedLimit @34;

  # DEPRECATED settings inputs
  setLogLevel @6;
  setLogSource @29;
  setLogJson @28;
  setTargetLateralAccel @1;
  setSpeedLimitOffset @2;
  setSpeedLimitControl @3;
  setMapCurveSpeedControl @4;
  setVisionCurveSpeedControl @5;
  setVisionCurveTargetLatA @7;
  setVisionCurveMinTargetV @8;
  setEnableSpeed @11;
  setVisionCurveUseEnableSpeed @12;
  setMapCurveUseEnableSpeed @13;
  setSpeedLimitUseEnableSpeed @14;
  setHoldLastSeenSpeedLimit @15;
  setTargetSpeedJerk @16;
  setTargetSpeedAccel @17;
  setTargetSpeedTimeOffset @18;
  setDefaultLaneWidth @19;
  setMapCurveTargetLatA @20;
  setSlowDownForNextSpeedLimit @23;
  setSpeedUpForNextSpeedLimit @24;
  setHoldSpeedLimitWhileChangingSetSpeed @25;
  setExternalSpeedLimitControl @30;
  setExternalSpeedLimit @31;
  setSpeedLimitPriority @32;
  setSpeedLimitChangeRequiresAccept @33;
  setPressGasToAcceptSpeedLimit @35;
  setAdjustSetSpeedToAcceptSpeedLimit @36;
  setAcceptSpeedLimitTimeout @37;
  setPressGasToOverrideSpeedLimit @38;
  setConditionalSpeedLimitControl @39;
  setShadowCarState @40;
  setShadowModelV2 @41;
  setShadowGpsLocation @42;
  setShadowGpsLocationExternal @46;
}

enum WaySelectionType {
  current @0;
  predicted @1;
  possible @2;
  extended @3;
  fail @4;
}

enum SpeedLimitOffsetType {
  static @0;
  percent @1;
}

struct MapdIn @0xc86a3d38d13eb3ef {
  type @0 :MapdInputType;
  float @1 :Float32;
  str @2 :Text;
  bool @3 :Bool;
  jsonPath @4 :Text;
}

enum RoadContext {
  freeway @0;
  city @1;
  unknown @2;
}

# WARNING: must be kept in perfect sync (names and values) with the
# HighwayClass enum in cereal/offline/offline.capnp — state.go casts directly
# between the two generated enum types.
# unknown either means the way's highway tag was not one of the listed values
# or the loaded map tiles predate this field.
enum HighwayClass {
  unknown @0;
  motorway @1;
  motorwayLink @2;
  trunk @3;
  trunkLink @4;
  primary @5;
  primaryLink @6;
  secondary @7;
  secondaryLink @8;
  tertiary @9;
  tertiaryLink @10;
  unclassified @11;
  residential @12;
  livingStreet @13;
}

struct MapdOut @0xa4f1eb3323f5f582 {
  wayName @0 :Text;
  wayRef @1 :Text;
  roadName @2 :Text;
  speedLimit @3 :Float32;
  nextSpeedLimit @4 :Float32;
  nextSpeedLimitDistance @5 :Float32;
  hazard @6 :Text;
  nextHazard @7 :Text;
  nextHazardDistance @8 :Float32;
  advisorySpeed @9 :Float32;
  nextAdvisorySpeed @10 :Float32;
  nextAdvisorySpeedDistance @11 :Float32;
  oneWay @12 :Bool;
  lanes @13 :UInt8;
  tileLoaded @14 :Bool;
  speedLimitSuggestedSpeed @15 :Float32;
  suggestedSpeed @16 :Float32;
  estimatedRoadWidth @17 :Float32;
  roadContext @18 :RoadContext;
  distanceFromWayCenter @19 :Float32;
  visionCurveSpeed @20 :Float32;
  mapCurveSpeed @21 :Float32;
  waySelectionType @22 :WaySelectionType;
  speedLimitAccepted @23 :Bool;
  highwayClass @24 :HighwayClass;
  wayId @25 :Int64;
  conditionalSpeedLimit @26 :Text;
  isForward @27 :Bool;
}

# Mono Detection structs (same ids and ordinals as EOP10).
# ExoPilot: monod runs a YOLO detector on the road camera on the GPU (exopilot/selfdrive/monod/monod.py).

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

# pathd add-on layer (ExoPilot): bounded lateral offset and speed factor chosen around the policy path.
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
  dppMode @15 :UInt8;                   # policy_arbiter.Mode: 0 off 1 shadow 2 supervise 3 primary_long 4 primary_lat 5 primary_both
  dppCase @16 :Text;                    # why DPP chose it
  disagreeCurvature @17 :Float32;       # rule - policy
  disagreeAccel @18 :Float32;
}


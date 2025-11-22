using Cxx = import "./include/c++.capnp";
$Cxx.namespace("cereal");

@0xb526ba661d550a59;

# custom.capnp: a home for empty structs reserved for custom forks
# These structs are guaranteed to remain reserved and empty in mainline
# cereal, so use these if you want custom events in your fork.

# DO rename the structs
# DON'T change the identifier (e.g. @0x81c2f05a394cf4af)

struct NpControlsState @0x81c2f05a394cf4af {
  alccActive @0 :Bool;
  dlpMode @1 :UInt16;
  dlpLcaMode @2 :UInt16;
  dlpActive @3 :Bool;
  dlpAvailable @4 :Bool;
  dlpConfidence @5 :Float32;
  dlpDesire @6 :Int16;
  dlpReasonCode @7 :UInt16;
  catAdaptive @8 :Bool;
  catConfidence @9 :Float32;
  catSteerRatio @10 :Float32;
  catStiffnessFactor @11 :Float32;
  catSamples @12 :UInt16;
  catManualOverride @13 :Bool;
  dlpAvailable @14 :Bool;
  dlpActive @15 :Bool;
  dlpConfidence @16 :Float32;
  dlpReasonCode @17 :UInt16;
  tscActive @18 :Bool;
  tscState @19 :UInt8;  # custom.LongitudinalPlanExt.VisionTurnControllerState / MapTurnControllerState
  tscVisionSpeed @20 :Float32;
  tscMapSpeed @21 :Float32;
  demActive @22 :Bool;
  demEngagedPercent @23 :Float32;
  tscMapStale @24 :Bool;
}

struct NpModelExt @0xaedffd8f31e7b55d {
  leftEdgeDetected @0 :Bool;
  rightEdgeDetected @1 :Bool;
}

struct NpLongitudinalPlanExt @0xf35cc4560bbf6ec2 {
  visionTurnControllerState @0 :VisionTurnControllerState;
  visionTurnSpeed @1 :Float32;
  mapTurnControllerState @14 :MapTurnControllerState;
  mapTurnSpeed @15 :Float32;
  speedLimitControlState @2 :SpeedLimitControlState;
  speedLimit @3 :Float32;
  speedLimitOffset @4 :Float32;
  distToSpeedLimit @5 :Float32;
  isMapSpeedLimit @6 :Bool;
  speedLimitPercOffset @7 :Bool;
  speedLimitValueOffset @8 :Float32;

  distToTurn @9 :Float32;
  turnSpeed @10 :Float32;
  turnSpeedControlState @11 :SpeedLimitControlState;
  turnSign @12 :Int16;

  visionPlanIsBlended @13 :Bool;
  longitudinalPlanExtSource @16 :LongitudinalPlanExtSource;

  enum LongitudinalPlanExtSource {
    cruise @0;
    lead0 @1;
    lead1 @2;
    lead2 @3;
    e2e @4;
    turn @5;
    limit @6;
    turnlimit @7;
  }

  enum SpeedLimitControlState {
    inactive @0;
    tempInactive @1;
    adapting @2;
    active @3;
  }

  enum VisionTurnControllerState {
    disabled @0;
    entering @1;
    turning @2;
    leaving @3;
  }

  enum MapTurnControllerState {
    disabled @0;
    entering @1;
    turning @2;
    leaving @3;
  }
}

struct NpLateralPlanExt @0xda96579883444c35 {
  dPathWLinesX @0 :List(Float32);
  dPathWLinesY @1 :List(Float32);
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

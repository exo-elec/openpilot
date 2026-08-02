using Cxx = import "./include/c++.capnp";
$Cxx.namespace("cereal");

@0xb526ba661d550a59;

# custom.capnp: a home for empty structs reserved for custom forks
# These structs are guaranteed to remain reserved and empty in mainline
# cereal, so use these if you want custom events in your fork.

# DO rename the structs
# DON'T change the identifier (e.g. @0x81c2f05a394cf4af)

struct NGPState @0x81c2f05a394cf4af {
  controlAuthority @0 :Bool;
  modelValid @1 :Bool;

  dlatSuggestion @2 :UInt8;
  dlatLaneConfidence @3 :Float32;
  dlatPathConfidence @4 :Float32;
  dlatModelConfidence @5 :Float32;
  dlatHasPathDeviation @6 :Bool;
  dlatPathDeviation @7 :Float32;

  dlonE2eSuggestion @8 :Bool;
  dlonTriggers @9 :List(Text);
  dlonForceStopSuggestion @10 :Bool;

  vtscState @11 :UInt8;
  vtscHasTarget @12 :Bool;
  vtscTargetSpeed @13 :Float32;
  vtscPredictedLatAccel @14 :Float32;

  speedZone @15 :UInt8;
  speedLimitSource @16 :UInt8;
  speedLimitValid @17 :Bool;
  speedLimit @18 :Float32;

  alccState @19 :UInt8;
  alccActiveSuggestion @20 :Bool;
  lcaState @21 :UInt8;
  lcaDirection @22 :UInt8;
  lcaSafeToStart @23 :Bool;
  lcaDesireSuggestion @24 :Bool;
  lcaBlockedReasons @25 :List(Text);

  roadEdgeValid @26 :Bool;
  leftRoadEdge @27 :Bool;
  rightRoadEdge @28 :Bool;
  radarTrackCount @29 :UInt16;
  radarLeftBlocked @30 :Bool;
  radarRightBlocked @31 :Bool;

  socActiveSuggestion @32 :Bool;
  socOffset @33 :Float32;
  bevAvailable @34 :Bool;
  bevCellCount @35 :UInt16;

  collisionLevel @36 :UInt8;
  collisionTrackValid @37 :Bool;
  collisionTrackId @38 :UInt64;
  collisionTtc @39 :Float32;
  collisionSafeDistance @40 :Float32;
  tripDistance @41 :Float32;
  tripEngagementRatio @42 :Float32;
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

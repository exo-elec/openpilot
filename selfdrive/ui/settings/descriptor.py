# ruff: noqa: E501
# Generated data. Each Control is one line on purpose: the generator's first
# attempt wrapped them with textwrap and split string literals mid-quote, so
# line length is exempted here rather than reintroducing that.
"""Declarative settings descriptor.

Generated from `selfdrive/ui/qt/offroad/eop_panel.cc` rather than transcribed
by hand -- 259 EOP params exist and hand-copying any part of that is how a
settings UI silently loses controls (plan section 5.5). Pages follow the
Nagasware page set, not openpilot's sidebar.

This is data, not code: `BaseOffroadPage` renders its sections from it, and
`tests/test_params_coverage.py` asserts every `EOP*` key in
`common/params_keys.h` is either present here or explicitly excluded with a
reason. That test is the completeness gate for settings.

Regenerate after changing eop_panel.cc
the coverage test will tell you if it
drifted.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum


class Kind(Enum):
  TOGGLE = "toggle"
  SPINBOX = "spinbox"
  BUTTONS = "buttons"


@dataclass(frozen=True)
class Control:
  key: str
  kind: Kind
  title: str
  desc: str = ""
  min: float | None = None
  max: float | None = None
  step: float | None = None
  unit: str = ""
  options: tuple[str, ...] = ()

  @property
  def feature_id(self) -> str:
    return self.key.removeprefix("EOP").removesuffix("Enabled")

  def __post_init__(self):
    if self.kind is Kind.SPINBOX and self.min is None:
      raise ValueError(f"{self.key}: SPINBOX needs a range")
    if self.kind is Kind.BUTTONS and not self.options:
      raise ValueError(f"{self.key}: BUTTONS needs options")


@dataclass(frozen=True)
class Page:
  name: str
  controls: list[Control] = field(default_factory=list)


PAGES: list[Page] = [
  Page("cruise", [
    Control("EOPAEBEnabled", Kind.TOGGLE, "Automatic Emergency Braking (AEB)", desc="⚠️ SAFETY-CRITICAL: Requires extensive testing.\nRSS-based emergency braking for collision avoidance.\nUses radar + vision + monod detections.\nDisabled by default - enable only after validation."),
    Control("EOPNavBleEnabled", Kind.TOGGLE, "BLE Navigation App", desc="Bluetooth connection for wireless destination input via mobile app."),
    Control("EOPNudgeEnabled", Kind.TOGGLE, "Stereo Path Nudge", desc="Enable stereo-based path enhancements:\n• LatNudge — lateral obstacle avoidance using stereo boundaries\n• LonNudge — speed reduction based on drivable distance and occupancy"),
    Control("EOPRCDEnabled", Kind.TOGGLE, "Road Condition Detection (RCD)", desc="Detects wet, icy, snowy, or debris-covered roads.\nAutomatically reduces speed for hazardous conditions.\nUses surface data + classical CV analysis."),
    Control("EOPTSCTargetLatAccel", Kind.SPINBOX, "Curve Speed Limit:", desc="Max lateral acceleration for curve speed control. Lower = more cautious.", min=1.0, max=2.5, step=0.1, unit="m/s²"),
  ]),
  Page("device", [
    # Options come from a std::vector variable (audible_alert_mode_texts) rather than
    # an inline list, so the generator could not see them -- filled in by hand, and
    # caught by Control.__post_init__ rather than shipping empty.
    Control("EOPDeviceAudibleAlertMode", Kind.BUTTONS, "Alert Sound", desc="Std - all alerts. Warning - warnings only. Off - silent.", options=("Std.", "Warning", "Off")),
    Control("EOPDeviceAutoShutdownIn", Kind.SPINBOX, "Auto Shutdown In:", desc="0 mins = Immediately", min=-5, max=300, step=5, unit="mins"),
    Control("EOPUIHideHudSpeedKph", Kind.SPINBOX, "Hide HUD When Moves above:", desc="To prevent screen burn-in, hide Speed, MAX Speed, and Steering Icons when the car moves.\nOff = Stock Behavior", min=0, max=120, step=5, unit="km/h"),
  ]),
  Page("dvr", [
    Control("EOPRecordEnabled", Kind.TOGGLE, "Enable On-Road Recording", desc="When enabled and storage is present, recordd runs automatically for loop recording, impact detection, and snapshots."),
  ]),
  Page("lateral", [
    Control("EOPALCCBrakeMode", Kind.BUTTONS, "ALCC Brake Behaviour", desc="Choose how ALCC responds when the brake pedal is pressed.\nMaintain - keep steering active.\nPause - hold steering until the brake is released.\nDisengage - fully release ALCC when braking.", options=("Maintain", "Pause", "Disengage")),
    Control("EOPAutoLaneChange", Kind.TOGGLE, "Auto Lane Change", desc="Enable automatic lane changes when turn signal is activated."),
    Control("EOPLaneChangeDelay", Kind.SPINBOX, "Lane Change Delay:", desc="Delay before executing lane change after turn signal activation.", min=0.5, max=5.0, step=0.1, unit="s"),
    Control("EOPLatLCASpeed", Kind.SPINBOX, "Lane Change Assist (LCA) Speed:", desc="Off = Disable Lane Change Assist", min=0, max=160, step=5, unit="km/h"),
    Control("EOPMinimumLaneWidth", Kind.SPINBOX, "Minimum Lane Width:", desc="Minimum lane width required for lane change assist.", min=2.0, max=4.0, step=0.1, unit="m"),
    Control("EOPOneLaneChange", Kind.TOGGLE, "One Lane Change Only", desc="Limit to one lane change per turn signal activation for safety."),
  ]),
  Page("map", [
    Control("EOPAutoTileEnabled", Kind.TOGGLE, "Auto-Download Map Tiles", desc="Automatically download OSM and SGM tiles based on GPS location.\nRequires internet connection."),
    Control("EOPAutoTileWifiOnly", Kind.TOGGLE, "WiFi-Only Downloads", desc="Only auto-download tiles when connected to WiFi to save cellular data."),
    Control("EOPGlobaldEnabled", Kind.TOGGLE, "Enable Global Localization", desc="Fuse GPS with OSM road data and SGM point cloud matching.\nProvides accurate lane-level positioning without RTK."),
    Control("EOPNTRIPEnabled", Kind.TOGGLE, "Enable NTRIP Corrections", desc="Receive RTCM3.3 differential corrections for RTK Fixed mode."),
    Control("EOPRTKEnabled", Kind.TOGGLE, "Enable RTK GPS", desc="Activate centimeter-level positioning via u-blox ZED-F9P-04B.\nBaud is auto-negotiated from 38400 (factory) to 115200 on boot."),
    Control("EOPSGMConfidenceThreshold", Kind.SPINBOX, "Match Confidence Threshold:", desc="Minimum confidence for SGM position match. Higher = more reliable but fewer matches.", min=0.3, max=0.95, step=0.05),
    Control("EOPSGMLocalizerEnabled", Kind.TOGGLE, "Enable SGM Point Cloud Matching", desc="Match live stereo point clouds against SGM 3D map tiles.\nRequires pre-built SGM map tiles in /data/maps/sgm/"),
    Control("EOPSGMMaxRange", Kind.SPINBOX, "Max Matching Range (m):", desc="Maximum search radius for point cloud matching.", min=20.0, max=200.0, step=10.0),
    Control("EOPSGMMode", Kind.BUTTONS, "SGM Matching Mode", desc="Select localization mode:\nLive - Match against live point clouds only\nMap - Match against pre-built SGM tiles only\nFused - Combine both sources for best accuracy", options=("Live", "Map", "Fused")),
  ]),
  Page("perception", [
    Control("EOPMonoDEnabled", Kind.TOGGLE, "Enable Long-Range Detection", desc="Activate Hailo-8 inference for distant object detection.\nExtends detection range to 500m using 16mm tele_road camera."),
    Control("EOPMonoDMaxTracks", Kind.SPINBOX, "Max Tracked Objects:", desc="Maximum number of objects to track simultaneously.", min=16, max=128, step=8),
    Control("EOPMonoDSceneSegEnabled", Kind.TOGGLE, "Enable Scene Segmentation", desc="Run PP-LiteSeg on tele_road feed for semantic understanding."),
    Control("EOPMonoDTeleEnabled", Kind.TOGGLE, "Enable 16mm TeleRoad Camera", desc="Use tele_road camera for long-range detection (primary MonoD input)."),
    Control("EOPMonoDWideEnabled", Kind.TOGGLE, "Enable 1.7mm Wide Camera", desc="Use ultra-wide camera for close-range blind spot coverage."),
    Control("EOPMonoDYoloConf", Kind.SPINBOX, "YOLO Confidence Threshold:", desc="Minimum confidence for object detection. Lower = more detections but more false positives.", min=0.1, max=0.9, step=0.05),
    Control("EOPPointcloudEnabled", Kind.TOGGLE, "Enable Point Cloud Recording", desc="Save 3D reconstructions from stereo depth to SD card.\nUsed for fleet mapping and digital twin generation.\nDoes not affect core ADAS functionality."),
    Control("EOPPointcloudMaxGB", Kind.SPINBOX, "Max Storage (GB):", desc="Maximum storage for point clouds. Oldest data auto-deleted when exceeded.", min=1.0, max=32.0, step=0.5),
    Control("EOPPointcloudRateHz", Kind.SPINBOX, "Recording Rate (Hz):", desc="Frame rate for point cloud capture. Higher = more data but more storage.", min=1, max=20, step=1),
    Control("EOPPointcloudUseGPU", Kind.TOGGLE, "Use GPU Acceleration", desc="Use Mali GPU for 3D reprojection. Faster but uses GPU resources.\nFalls back to CPU if GPU unavailable."),
    Control("EOPSurfaceGridRange", Kind.SPINBOX, "Grid Forward Range (m):", desc="How far ahead to map surface conditions.", min=30.0, max=150.0, step=10.0),
    Control("EOPSurfaceGridResolution", Kind.SPINBOX, "Grid Resolution (m):", desc="Size of each grid cell for surface quality mapping.", min=0.1, max=1.0, step=0.05),
    Control("EOPSurfaceGridWidth", Kind.SPINBOX, "Grid Width (m):", desc="Lateral coverage of surface quality grid.", min=10.0, max=60.0, step=5.0),
    Control("EOPSurfaceLongHorizon", Kind.TOGGLE, "Enable Long Horizon", desc="Extend surface quality detection to 100m for highway comfort.\nUses more GPU resources."),
  ]),
  Page("safety", [
    Control("EOPBSDChimeEnabled", Kind.TOGGLE, "BSD Warning Chime", desc="Audible warning when a fast-approaching vehicle enters the blind spot."),
    Control("EOPBlindSpotIndicator", Kind.TOGGLE, "Blind Spot Edge Indicator", desc="Amber or red band down the side of the driving screen when a vehicle is in the blind spot."),
    Control("EOPRearCameraEnabled", Kind.TOGGLE, "Rear Camera", desc="USB rear camera for reverse view. Shows when reverse gear engaged."),
  ]),
  Page("vehicle", [
    Control("EOPCATManualSREnabled", Kind.TOGGLE, "Use Fixed Steer Ratio", desc="Disable learning and apply a fixed steer ratio instead."),
  ]),
  Page("voice", [
    Control("EOPCloudVoiceEnabled", Kind.TOGGLE, "Online Voice", desc="Upload activated voice audio and receive online spoken replies. Google recognition and synthesis run on the server."),
    Control("EOPWakeEnabled", Kind.TOGGLE, "Wake Phrase", desc="CPU-only activation phrase detector. Requires an installed phrase model; speech is sent online only after activation."),
    Control("EOPVoiceEnabled", Kind.TOGGLE, "Microphone Input", desc="Local microphone beamforming and voice activity. Wake activation uses CPU; full speech recognition and spoken replies use the online gateway."),
  ]),
]


# Verified persistent feature switches missing from the legacy panel.
PAGES.append(Page("features", [
  Control("EOPBEVWidgetEnabled", Kind.TOGGLE, "BEV · Bird’s-Eye View", desc="Allow the live bird’s-eye visualization. Enable BEV Display Mode to replace the camera view; normal mode keeps the basic full-screen camera."),
  Control("EOPUIDisplayMode", Kind.TOGGLE, "BEV Display Mode", desc="Show lanes, road edges, radar leads and blind-spot severity from above. Requires the BEV feature switch."),
  Control('EOPALCCAllowAlways', Kind.TOGGLE, 'ALCC Allow Always', desc='Feature ID: ALCCAllowAlways. Enable or disable this feature. Hardware and parent-feature requirements still apply. Some processes read this setting at startup; restart while parked if needed.'),
  Control('EOPALCCHoldAtStandstill', Kind.TOGGLE, 'ALCC Hold At Standstill', desc='Feature ID: ALCCHoldAtStandstill. Enable or disable this feature. Hardware and parent-feature requirements still apply. Some processes read this setting at startup; restart while parked if needed.'),
  Control('EOPALCCUnifiedEngagement', Kind.TOGGLE, 'ALCC Unified Engagement', desc='Feature ID: ALCCUnifiedEngagement. Enable or disable this feature. Hardware and parent-feature requirements still apply. Some processes read this setting at startup; restart while parked if needed.'),
  Control('EOPAdaptdEnabled', Kind.TOGGLE, 'Adaptd', desc='Feature ID: Adaptd. Enable or disable this feature. Hardware and parent-feature requirements still apply. Some processes read this setting at startup; restart while parked if needed.'),
  Control('EOPBSDEnabled', Kind.TOGGLE, 'BSD · Blind Spot Detection', desc='Feature ID: BSD. Enable or disable this feature. Hardware and parent-feature requirements still apply. Some processes read this setting at startup; restart while parked if needed.'),
  Control('EOPBluetoothEnabled', Kind.TOGGLE, 'Bluetooth', desc='Feature ID: Bluetooth. Enable or disable this feature. Hardware and parent-feature requirements still apply. Some processes read this setting at startup; restart while parked if needed.'),
  Control('EOPBluetoothRadarEnabled', Kind.TOGGLE, 'Bluetooth Radar', desc='Feature ID: BluetoothRadar. Enable or disable this feature. Hardware and parent-feature requirements still apply. Some processes read this setting at startup; restart while parked if needed.'),
  Control('EOPCATEnabled', Kind.TOGGLE, 'CAT · Car Adaptive Tuning', desc='Feature ID: CAT. Enable or disable this feature. Hardware and parent-feature requirements still apply. Some processes read this setting at startup; restart while parked if needed.'),
  Control('EOPCurveSpeedLearnEnabled', Kind.TOGGLE, 'Curve Speed Learn', desc='Feature ID: CurveSpeedLearn. Enable or disable this feature. Hardware and parent-feature requirements still apply. Some processes read this setting at startup; restart while parked if needed.'),
  Control('EOPDLONCurvesEnabled', Kind.TOGGLE, 'DLON Curves', desc='Feature ID: DLONCurves. Enable or disable this feature. Hardware and parent-feature requirements still apply. Some processes read this setting at startup; restart while parked if needed.'),
  Control('EOPDLONForceStopsEnabled', Kind.TOGGLE, 'DLON Force Stops', desc='Feature ID: DLONForceStops. Enable or disable this feature. Hardware and parent-feature requirements still apply. Some processes read this setting at startup; restart while parked if needed.'),
  Control('EOPDLONLaneConfidenceEnabled', Kind.TOGGLE, 'DLON Lane Confidence', desc='Feature ID: DLONLaneConfidence. Enable or disable this feature. Hardware and parent-feature requirements still apply. Some processes read this setting at startup; restart while parked if needed.'),
  Control('EOPDLONLowSpeedEnabled', Kind.TOGGLE, 'DLON Low Speed', desc='Feature ID: DLONLowSpeed. Enable or disable this feature. Hardware and parent-feature requirements still apply. Some processes read this setting at startup; restart while parked if needed.'),
  Control('EOPDLONNavigationEnabled', Kind.TOGGLE, 'DLON Navigation', desc='Feature ID: DLONNavigation. Enable or disable this feature. Hardware and parent-feature requirements still apply. Some processes read this setting at startup; restart while parked if needed.'),
  Control('EOPDLONSignalEnabled', Kind.TOGGLE, 'DLON Signal', desc='Feature ID: DLONSignal. Enable or disable this feature. Hardware and parent-feature requirements still apply. Some processes read this setting at startup; restart while parked if needed.'),
  Control('EOPDLONSlowLeadEnabled', Kind.TOGGLE, 'DLON Slow Lead', desc='Feature ID: DLONSlowLead. Enable or disable this feature. Hardware and parent-feature requirements still apply. Some processes read this setting at startup; restart while parked if needed.'),
  Control('EOPDLONSpeedLimitEnabled', Kind.TOGGLE, 'DLON Speed Limit', desc='Feature ID: DLONSpeedLimit. Enable or disable this feature. Hardware and parent-feature requirements still apply. Some processes read this setting at startup; restart while parked if needed.'),
  Control('EOPDLONStopPredictionEnabled', Kind.TOGGLE, 'DLON Stop Prediction', desc='Feature ID: DLONStopPrediction. Enable or disable this feature. Hardware and parent-feature requirements still apply. Some processes read this setting at startup; restart while parked if needed.'),
  Control('EOPDLPCurvesEnabled', Kind.TOGGLE, 'DLP Curves', desc='Feature ID: DLPCurves. Enable or disable this feature. Hardware and parent-feature requirements still apply. Some processes read this setting at startup; restart while parked if needed.'),
  Control('EOPGridEnabled', Kind.TOGGLE, 'Grid', desc='Feature ID: Grid. Enable or disable this feature. Hardware and parent-feature requirements still apply. Some processes read this setting at startup; restart while parked if needed.'),
  Control('EOPHealthMonitorEnabled', Kind.TOGGLE, 'Health Monitor', desc='Feature ID: HealthMonitor. Enable or disable this feature. Hardware and parent-feature requirements still apply. Some processes read this setting at startup; restart while parked if needed.'),
  Control('EOPHybridPlannerEnabled', Kind.TOGGLE, 'Hybrid Planner', desc='Feature ID: HybridPlanner. Enable or disable this feature. Hardware and parent-feature requirements still apply. Some processes read this setting at startup; restart while parked if needed.'),
  Control('EOPLCAControllerEnabled', Kind.TOGGLE, 'LCA Controller', desc='Feature ID: LCAController. Enable or disable this feature. Hardware and parent-feature requirements still apply. Some processes read this setting at startup; restart while parked if needed.'),
  Control('EOPLCAGapEvalEnabled', Kind.TOGGLE, 'LCA Gap Eval', desc='Feature ID: LCAGapEval. Enable or disable this feature. Hardware and parent-feature requirements still apply. Some processes read this setting at startup; restart while parked if needed.'),
  Control('EOPLCALaneWidthEnabled', Kind.TOGGLE, 'LCA Lane Width', desc='Feature ID: LCALaneWidth. Enable or disable this feature. Hardware and parent-feature requirements still apply. Some processes read this setting at startup; restart while parked if needed.'),
  Control('EOPLCAdjacentLeadHandoff', Kind.TOGGLE, 'LC Adjacent Lead Handoff', desc='Feature ID: LCAdjacentLeadHandoff. Enable or disable this feature. Hardware and parent-feature requirements still apply. Some processes read this setting at startup; restart while parked if needed.'),
  Control('EOPLatALCC', Kind.TOGGLE, 'ALCC · Always Lane Centering Control', desc='Feature ID: LatALCC. Enable or disable this feature. Hardware and parent-feature requirements still apply. Some processes read this setting at startup; restart while parked if needed.'),
  Control('EOPLeftCameraEnabled', Kind.TOGGLE, 'Left Camera', desc='Feature ID: LeftCamera. Enable or disable this feature. Hardware and parent-feature requirements still apply. Some processes read this setting at startup; restart while parked if needed.'),
  Control('EOPMSLCEnabled', Kind.TOGGLE, 'MSLC · Map Speed Limit Control', desc='Feature ID: MSLC. Enable or disable this feature. Hardware and parent-feature requirements still apply. Some processes read this setting at startup; restart while parked if needed.'),
  Control('EOPMTSCEnabled', Kind.TOGGLE, 'MTSC · Map Turn Speed Control', desc='Feature ID: MTSC. Enable or disable this feature. Hardware and parent-feature requirements still apply. Some processes read this setting at startup; restart while parked if needed.'),
  Control('EOPMapdEnabled', Kind.TOGGLE, 'Mapd', desc='Feature ID: Mapd. Enable or disable this feature. Hardware and parent-feature requirements still apply. Some processes read this setting at startup; restart while parked if needed.'),
  Control('EOPNSLCEnabled', Kind.TOGGLE, 'NSLC · Navigation Speed Limit Control', desc='Feature ID: NSLC. Enable or disable this feature. Hardware and parent-feature requirements still apply. Some processes read this setting at startup; restart while parked if needed.'),
  Control('EOPNavVoiceEnabled', Kind.TOGGLE, 'Nav Voice', desc='Feature ID: NavVoice. Enable or disable this feature. Hardware and parent-feature requirements still apply. Some processes read this setting at startup; restart while parked if needed.'),
  Control('EOPOsmLocalizerEnabled', Kind.TOGGLE, 'Osm Localizer', desc='Feature ID: OsmLocalizer. Enable or disable this feature. Hardware and parent-feature requirements still apply. Some processes read this setting at startup; restart while parked if needed.'),
  Control('EOPPathdFixScaleEnabled', Kind.TOGGLE, 'PDSF · Path Distance Scale Fix',
          desc='Use the corrected object-distance speed reduction lookup. Default off. Existing legacy settings remain readable.'),
  Control('EOPPredictEnabled' , Kind.TOGGLE, 'Predict', desc='Feature ID: Predict. Enable or disable this feature. Hardware and parent-feature requirements still apply. Some processes read this setting at startup; restart while parked if needed.'),
  Control('EOPRecorddParkingEnabled', Kind.TOGGLE, 'Recordd Parking', desc='Feature ID: RecorddParking. Enable or disable this feature. Hardware and parent-feature requirements still apply. Some processes read this setting at startup; restart while parked if needed.'),
  Control('EOPRedControllerEnabled', Kind.TOGGLE, 'RED · Road Edge Detection', desc='Feature ID: RedController. Enable or disable this feature. Hardware and parent-feature requirements still apply. Some processes read this setting at startup; restart while parked if needed.'),
  Control('EOPRightCameraEnabled', Kind.TOGGLE, 'Right Camera', desc='Feature ID: RightCamera. Enable or disable this feature. Hardware and parent-feature requirements still apply. Some processes read this setting at startup; restart while parked if needed.'),
  Control('EOPSOCControllerEnabled', Kind.TOGGLE, 'SOC · Smart Offset Controller', desc='Feature ID: SOCController. Enable or disable this feature. Hardware and parent-feature requirements still apply. Some processes read this setting at startup; restart while parked if needed.'),
  Control('EOPSPPEnabled', Kind.TOGGLE, 'SPP · Serial Port Profile', desc='Feature ID: SPP. Enable or disable this feature. Hardware and parent-feature requirements still apply. Some processes read this setting at startup; restart while parked if needed.'),
  Control('EOPSQSCEnabled', Kind.TOGGLE, 'SQSC · Surface Quality Speed Control', desc='Feature ID: SQSC. Enable or disable this feature. Hardware and parent-feature requirements still apply. Some processes read this setting at startup; restart while parked if needed.'),
  Control('EOPSQSCLookaheadEnabled', Kind.TOGGLE, 'SQSC Lookahead', desc='Feature ID: SQSCLookahead. Enable or disable this feature. Hardware and parent-feature requirements still apply. Some processes read this setting at startup; restart while parked if needed.'),
  Control('EOPSafetyEventLogEnabled', Kind.TOGGLE, 'Safety Event Log', desc='Feature ID: SafetyEventLog. Enable or disable this feature. Hardware and parent-feature requirements still apply. Some processes read this setting at startup; restart while parked if needed.'),
  Control('EOPSideCamerasEnabled', Kind.TOGGLE, 'Side Cameras', desc='Feature ID: SideCameras. Enable or disable this feature. Hardware and parent-feature requirements still apply. Some processes read this setting at startup; restart while parked if needed.'),
  Control('EOPStereoEnabled', Kind.TOGGLE, 'Stereo', desc='Feature ID: Stereo. Enable or disable this feature. Hardware and parent-feature requirements still apply. Some processes read this setting at startup; restart while parked if needed.'),
  Control('EOPSurfaceEnabled', Kind.TOGGLE, 'Surface', desc='Feature ID: Surface. Enable or disable this feature. Hardware and parent-feature requirements still apply. Some processes read this setting at startup; restart while parked if needed.'),
  Control('EOPTJAEnabled', Kind.TOGGLE, 'TJA · Traffic Jam Assist', desc='Feature ID: TJA. Enable or disable this feature. Hardware and parent-feature requirements still apply. Some processes read this setting at startup; restart while parked if needed.'),
  Control('EOPTLSCEnabled', Kind.TOGGLE, 'TLSC · Traffic Light Speed Control', desc='Feature ID: TLSC. Enable or disable this feature. Hardware and parent-feature requirements still apply. Some processes read this setting at startup; restart while parked if needed.'),
  Control('EOPTTSAlertsEnabled', Kind.TOGGLE, 'TTS Alerts', desc='Feature ID: TTSAlerts. Enable or disable this feature. Hardware and parent-feature requirements still apply. Some processes read this setting at startup; restart while parked if needed.'),
  Control('EOPTeleEnabled', Kind.TOGGLE, 'Tele', desc='Feature ID: Tele. Enable or disable this feature. Hardware and parent-feature requirements still apply. Some processes read this setting at startup; restart while parked if needed.'),
  Control('EOPTrackEnabled', Kind.TOGGLE, 'Track', desc='Feature ID: Track. Enable or disable this feature. Hardware and parent-feature requirements still apply. Some processes read this setting at startup; restart while parked if needed.'),
  Control('EOPVTSCEnabled', Kind.TOGGLE, 'VTSC · Vision Turn Speed Control', desc='Feature ID: VTSC. Enable or disable this feature. Hardware and parent-feature requirements still apply. Some processes read this setting at startup; restart while parked if needed.'),
  Control('EOPNeuralNetworkLateralControl', Kind.TOGGLE, 'NNLC · Neural Network Lateral Control', desc='Feature ID: NeuralNetworkLateralControl. Enable or disable this feature. Hardware and parent-feature requirements still apply. Some processes read this setting at startup; restart while parked if needed.'),
  Control('EOPSteamDEnabled', Kind.TOGGLE, 'Steam D', desc='Feature ID: SteamD. Enable or disable this feature. Hardware and parent-feature requirements still apply. Some processes read this setting at startup; restart while parked if needed.'),
  Control('EOPQuietMode', Kind.TOGGLE, 'Quiet Mode', desc="Enable or disable this option. Hardware and parent-feature requirements apply; cached settings may require a parked restart."),
  Control('EOPDeviceIsRhd', Kind.TOGGLE, 'RHD · Right-Hand Drive', desc="Enable or disable this option. Hardware and parent-feature requirements apply; cached settings may require a parked restart."),
  Control('EOPSLCConfirmHigher', Kind.TOGGLE, 'SLC · Confirm Higher Speed Limit', desc="Enable or disable this option. Hardware and parent-feature requirements apply; cached settings may require a parked restart."),
  Control('EOPSLCConfirmLower', Kind.TOGGLE, 'SLC · Confirm Lower Speed Limit', desc="Enable or disable this option. Hardware and parent-feature requirements apply; cached settings may require a parked restart."),
  Control('EOPSPPAutoReconnect', Kind.TOGGLE, 'SPP · Automatic Reconnection', desc="Enable or disable this option. Hardware and parent-feature requirements apply; cached settings may require a parked restart."),
  Control('EOPSQSCUseLearnedSpeed', Kind.TOGGLE, 'SQSC · Use Learned Speed', desc="Enable or disable this option. Hardware and parent-feature requirements apply; cached settings may require a parked restart."),
  Control('EOPSideCamerasSwapped', Kind.TOGGLE, 'Swap Side Cameras', desc="Enable or disable this option. Hardware and parent-feature requirements apply; cached settings may require a parked restart."),
  Control('EOPSteamDJoystickInput', Kind.TOGGLE, 'SteamD · Joystick Input', desc="Enable or disable this option. Hardware and parent-feature requirements apply; cached settings may require a parked restart."),
]))


def all_controls() -> list[Control]:
  return [c for p in PAGES for c in p.controls]


def declared_keys() -> frozenset[str]:
  return frozenset(c.key for c in all_controls())


def page(name: str) -> Page:
  for p in PAGES:
    if p.name == name:
      return p
  raise KeyError(name)

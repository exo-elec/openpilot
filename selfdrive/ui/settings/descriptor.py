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
    Control("EOPVoiceEnabled", Kind.TOGGLE, "Enable Voice Assistant", desc="Wake word detection and speech-to-text for hands-free control."),
  ]),
]


def all_controls() -> list[Control]:
  return [c for p in PAGES for c in p.controls]


def declared_keys() -> frozenset[str]:
  return frozenset(c.key for c in all_controls())


def page(name: str) -> Page:
  for p in PAGES:
    if p.name == name:
      return p
  raise KeyError(name)

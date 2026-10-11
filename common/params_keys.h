#pragma once

#include <string>
#include <unordered_map>

#include "cereal/gen/cpp/log.capnp.h"

inline static std::unordered_map<std::string, ParamKeyAttributes> keys = {
    {"AccessToken", {CLEAR_ON_MANAGER_START | DONT_LOG, STRING}},
    {"AdbEnabled", {PERSISTENT, BOOL}},
    {"ApiCache_Device", {PERSISTENT, STRING}},
    {"ApiCache_FirehoseStats", {PERSISTENT, JSON}},
    {"AssistNowToken", {PERSISTENT, STRING}},
    {"AthenadPid", {PERSISTENT, INT}},
    {"AthenadUploadQueue", {PERSISTENT, JSON}},
    {"AthenadRecentlyViewedRoutes", {PERSISTENT, STRING}},
    // Big model (phone over USB / eGPU): loading blocks engagement, active is false once it handed back or failed.
    // Read by selfdrived through nagaspilot/controls/bigmodel_gate.py. Nothing writes them on this branch yet.
    {"EOPBigModelActive", {CLEAR_ON_MANAGER_START, BOOL}},
    {"EOPBigModelLoading", {CLEAR_ON_MANAGER_START, BOOL}},
    {"BootCount", {PERSISTENT, INT}},
    {"CalibrationParams", {PERSISTENT, BYTES}},
    {"CameraDebugExpGain", {CLEAR_ON_MANAGER_START, STRING}},
    {"CameraDebugExpTime", {CLEAR_ON_MANAGER_START, STRING}},
    {"CarBatteryCapacity", {PERSISTENT, INT}},
    {"CarParams", {CLEAR_ON_MANAGER_START | CLEAR_ON_ONROAD_TRANSITION, BYTES}},
    {"CarParamsCache", {CLEAR_ON_MANAGER_START, BYTES}},
    {"CarParamsPersistent", {PERSISTENT, BYTES}},
    {"CarParamsPrevRoute", {PERSISTENT, BYTES}},
    {"CompletedTrainingVersion", {PERSISTENT, STRING, "0"}},
    {"ControlsReady", {CLEAR_ON_MANAGER_START | CLEAR_ON_ONROAD_TRANSITION, BOOL}},
    {"CurrentBootlog", {PERSISTENT, STRING}},
    {"CurrentRoute", {CLEAR_ON_MANAGER_START | CLEAR_ON_ONROAD_TRANSITION, STRING}},
    {"DisableLogging", {CLEAR_ON_MANAGER_START | CLEAR_ON_ONROAD_TRANSITION, BOOL}},
    {"DisablePowerDown", {PERSISTENT, BOOL}},
    {"DisableUpdates", {PERSISTENT, BOOL}},
    {"DisengageOnAccelerator", {PERSISTENT, BOOL, "0"}},
    {"DongleId", {PERSISTENT, STRING}},
    {"DoReboot", {CLEAR_ON_MANAGER_START, BOOL}},
    {"DoShutdown", {CLEAR_ON_MANAGER_START, BOOL}},
    {"DoUninstall", {CLEAR_ON_MANAGER_START, BOOL}},
    {"DriverTooDistracted", {CLEAR_ON_MANAGER_START | CLEAR_ON_IGNITION_ON, BOOL}},
    {"AlphaLongitudinalEnabled", {PERSISTENT | DEVELOPMENT_ONLY, BOOL}},
    {"ExperimentalMode", {PERSISTENT, BOOL}},
    {"ExperimentalModeConfirmed", {PERSISTENT, BOOL}},
    {"FirmwareQueryDone", {CLEAR_ON_MANAGER_START | CLEAR_ON_ONROAD_TRANSITION, BOOL}},
    {"ForcePowerDown", {PERSISTENT, BOOL}},
    {"EOPPowerSaverEntryDuration", {PERSISTENT, INT, "30"}},
    {"GitBranch", {PERSISTENT, STRING}},
    {"GitCommit", {PERSISTENT, STRING}},
    {"GitCommitDate", {PERSISTENT, STRING}},
    {"GitDiff", {PERSISTENT, STRING}},
    {"GithubSshKeys", {PERSISTENT, STRING}},
    {"GithubUsername", {PERSISTENT, STRING}},
    {"GitRemote", {PERSISTENT, STRING}},
    {"GsmApn", {PERSISTENT, STRING}},
    {"GsmMetered", {PERSISTENT, BOOL, "1"}},
    {"GsmRoaming", {PERSISTENT, BOOL}},
    {"HardwareSerial", {PERSISTENT, STRING}},
    {"HasAcceptedTerms", {PERSISTENT, STRING, "0"}},
    {"InstallDate", {PERSISTENT, TIME}},
    {"IsEngaged", {PERSISTENT, BOOL}},
    {"IsLdwEnabled", {PERSISTENT, BOOL}},
    {"IsMetric", {PERSISTENT, BOOL}},
    {"IsOffroad", {CLEAR_ON_MANAGER_START, BOOL}},
    {"IsOnroad", {PERSISTENT, BOOL}},
    {"EOPQuietMode", {PERSISTENT, BOOL, "0"}},
    {"IsReleaseBranch", {CLEAR_ON_MANAGER_START, BOOL}},
    {"IsTakingSnapshot", {CLEAR_ON_MANAGER_START, BOOL}},
    {"IsTestedBranch", {CLEAR_ON_MANAGER_START, BOOL}},
    {"JoystickDebugMode", {CLEAR_ON_MANAGER_START | CLEAR_ON_OFFROAD_TRANSITION, BOOL}},
    {"NavigationEnabled", {PERSISTENT, BOOL, "0"}},  // upstream nav alpha (selfdrive/navigation): navd + mapsd onroad
    {"NavDestination", {CLEAR_ON_MANAGER_START | CLEAR_ON_OFFROAD_TRANSITION, JSON}},  // {"latitude", "longitude"}, set by athena setNavDestination
    {"LanguageSetting", {PERSISTENT, STRING, "main_en"}},
    {"LastAthenaPingTime", {CLEAR_ON_MANAGER_START, INT}},
    {"LastGPSPosition", {PERSISTENT, STRING}},
    {"LastManagerExitReason", {CLEAR_ON_MANAGER_START, STRING}},
    {"LastOffroadStatusPacket", {CLEAR_ON_MANAGER_START | CLEAR_ON_OFFROAD_TRANSITION, JSON}},
    {"LastPowerDropDetected", {CLEAR_ON_MANAGER_START, STRING}},
    {"LastUpdateException", {CLEAR_ON_MANAGER_START, STRING}},
    {"LastUpdateRouteCount", {PERSISTENT, INT, "0"}},
    {"LastUpdateTime", {PERSISTENT, TIME}},
    {"LastUpdateUptimeOnroad", {PERSISTENT, FLOAT, "0.0"}},
    {"LiveDelay", {PERSISTENT, BYTES}},
    {"LiveParameters", {PERSISTENT, JSON}},
    {"LiveParametersV2", {PERSISTENT, BYTES}},
    {"LiveTorqueParameters", {PERSISTENT | DONT_LOG, BYTES}},
    {"LocationFilterInitialState", {PERSISTENT, BYTES}},
    {"LongitudinalManeuverMode", {CLEAR_ON_MANAGER_START | CLEAR_ON_OFFROAD_TRANSITION, BOOL}},
    {"LongitudinalPersonality", {PERSISTENT, INT, std::to_string(static_cast<int>(cereal::LongitudinalPersonality::STANDARD))}},
    {"NetworkMetered", {PERSISTENT, BOOL}},
    {"DashboardEnabled", {PERSISTENT, BOOL, "0"}},
    {"OffroadShutdownMinutes", {PERSISTENT, INT, "-1"}},
    {"LoggerStartDelay", {PERSISTENT, INT, "0"}},
    {"AlertSoundMode", {PERSISTENT, INT, "0"}},
    {"UIHideHudSpeedKph", {PERSISTENT, INT, "0"}},
    {"UIBrightness", {PERSISTENT, INT, "0"}},
    {"DeviceModelSelected", {PERSISTENT, STRING, ""}},
    {"VehicleModelList", {CLEAR_ON_MANAGER_START, STRING, "[]"}},
    {"LatALCC", {PERSISTENT, BOOL, "0"}},
    {"ALCCBrakeMode", {PERSISTENT, INT, "0"}},  // ALCC on a brake press: 0 Maintain, 1 Pause, 2 Disengage
    // DLAT (Dynamic Lateral Profile, exopilot/selfdrive/controls/lib/dlat.py) is a
    // default, always-on behavior of this branch -- automatic Laneful/
    // Laneless confidence arbitration only, no user-selectable mode param.
    // DLP curve assist: pre-emptively force laneless on a predicted tight
    // curve (DLAT.update_model()'s curve_assist_enabled), ahead of the
    // ordinary confidence hysteresis. Matches EOP10's DLPCurvesEnabled,
    // including its default-on.
    {"DLPCurvesEnabled", {PERSISTENT, BOOL, "1"}},
    // Pause lateral assistance with a turn signal on below this speed (mph); 0 = off.
    {"BlinkerPauseLateralSpeed", {PERSISTENT, INT, "0"}},
    // Notice when the car is released from a planner stop (green light). Default off.
    {"GreenLightAlert", {PERSISTENT, BOOL, "0"}},
    // SOC: small slow offset away from a vehicle beside you on the highway (soc.py). Default off.
    {"SOCControllerEnabled", {PERSISTENT, BOOL, "0"}},
    // RED: vision-only curvature nudge away from a close road edge in laneless mode (red.py).
    // ExoPilot adds YOLO/stereo corroboration. Unvalidated on road; default off.
    {"RedControllerEnabled", {PERSISTENT, BOOL, "0"}},
    {"AutoLaneChangeDelay", {PERSISTENT, FLOAT, "0.0"}},
    // Lane turn desire: max speed (mph) at which a turn signal sends a turn desire to the model; 0 = off.
    {"TurnDesireSpeed", {PERSISTENT, INT, "0"}},
    // Opt-in LCA guards from modelV2: adjacent-lane TTC gap, target-lane width (>= 2.5 m).
    {"LCAGapEvalEnabled", {PERSISTENT, BOOL, "0"}},
    {"LCALaneWidthEnabled", {PERSISTENT, BOOL, "0"}},
    // CAT: smoothed and validated steer ratio / stiffness from liveParameters. Default off.
    {"CATEnabled", {PERSISTENT, BOOL, "0"}},
    {"CATManualSR", {PERSISTENT, FLOAT, "0.0"}},          // fixed steer ratio when CATManualSREnabled
    {"CATManualSREnabled", {PERSISTENT, BOOL, "0"}},
    {"CATPersist", {PERSISTENT, STRING, ""}},             // JSON: learned steer ratio / stiffness / angle offset per fingerprint
    {"LatLCASpeed", {PERSISTENT, INT, "20"}},
    {"LatRoadEdgeDetection", {PERSISTENT, BOOL, "0"}},
    // BRSC: Bumpy Road Speed Controller — shared verbatim across EOP10/NGP10/EDP10
    // via exopilot/selfdrive/controls/lib/brsc.py. See docs/eop/03_Software/Controllers/BRSC.md on dev/EOP10.
    // Notice when the lead pulls away while stopped (radarState only). Default off.
    {"LeadDepartAlert", {PERSISTENT, BOOL, "0"}},
    // Driver-activity decay: relaxed/tight; strict retains the deployed relaxed table.
    // Run the big driving model on a phone over the USB cable (NavPilot's NPBM link, NAVPILOT_BIGMODEL.md). 0 off,
    // 1 Android (FunctionFS bulk gadget), 2 iPhone (CDC-NCM network the phone dials), 3 USB tethering (optional). The direct
    // modes 1 and 2 are the default way (NAVPILOT_BIGMODEL.md D11). Default off; the small model stays loaded and drives
    // until the phone model is proven. NGP-origin, so ngp_ on every branch (D12).
    {"BigModelLink", {PERSISTENT, INT, "0"}},
    // Big model state, written by modeld's BigModelHook and read by selfdrived (exopilot/selfdrive/modeld/bigmodel/gate.py).
    {"BigModelActive", {CLEAR_ON_MANAGER_START, BOOL}},
    {"BigModelLoading", {CLEAR_ON_MANAGER_START, BOOL}},
    {"DriverActivityPolicy", {PERSISTENT, STRING, "strict"}},
    // monod (exopilot/selfdrive/monod/monod.py): road-camera YOLO detections, publish-only. Default off; needs a detector model compiled in.
    // Cut-in speed trim (cutin_speed.py): lowers cruise speed for a predicted cut-in seen by monod. Default off; needs MonoDEnabled.
    {"CutInSpeedEnabled", {PERSISTENT, BOOL, "0"}},
    // pathd add-on (exopilot/selfdrive/pathd/pathd.py): publishes pathAdjust (lateral offset + speed factor). Publish-only, default off.
    // pathd add-on consumers (pathd_consumer.py), default off, need PathdEnabled: lateral offset in controlsd, speed cap in the planner.
    {"PathdLateral", {PERSISTENT, BOOL, "0"}},
    {"PathdLongitudinal", {PERSISTENT, BOOL, "0"}},
    // DPP ceiling for the parallel rule channel (0 idle, 1 shadow, 2 supervise, 3 primary long, 4 primary lat, 5 primary both). DPP picks the mode by case inside it. Needs PathdEnabled.
    {"DPPMaxMode", {PERSISTENT, INT, "0"}},
    // pathd: also run the ported EOP10 proposers (LatNudge, LonNudge, speed reduction) from camera-only inputs (exopilot/selfdrive/pathd/nudge_extras.py). Default off; needs PathdEnabled.
    {"PathdNudgesEnabled", {PERSISTENT, BOOL, "0"}},
    // Map / light / distraction speed caps (exopilot/selfdrive/controls/lib/map_speed.py), all default off, they only lower the cruise speed.
    // tripd (exopilot/selfdrive/tripd/tripd.py): trip statistics. Process behind TripdEnabled.
    {"TripdEnabled", {PERSISTENT, BOOL, "0"}},
    {"TripAStartDistance", {PERSISTENT, FLOAT, "0.0"}},
    {"TripAStartTime", {PERSISTENT, FLOAT, "0.0"}},
    {"TripBStartDistance", {PERSISTENT, FLOAT, "0.0"}},
    {"TripBStartTime", {PERSISTENT, FLOAT, "0.0"}},
    {"TripLastDistance", {PERSISTENT, FLOAT, "0.0"}},
    {"TripLastDuration", {PERSISTENT, FLOAT, "0.0"}},
    {"TripLastMaxAccel", {PERSISTENT, FLOAT, "0.0"}},
    {"TripLastOverrideFreeDistance", {PERSISTENT, FLOAT, "0.0"}},
    {"TripLastPersonalityTime", {PERSISTENT, STRING, ""}},
    {"TripLifetimeEngagementRatio", {PERSISTENT, FLOAT, "0.0"}},
    {"TripTotalDistance", {PERSISTENT, FLOAT, "0.0"}},
    {"TripTotalDrives", {PERSISTENT, INT, "0"}},
    {"TripDailyStats", {PERSISTENT, STRING}},  // JSON {date: [distance, onroad_time, engaged_time, drives]}, last 7 days
    {"TripUptimeEngaged", {PERSISTENT, FLOAT, "0.0"}},
    {"TripUptimeOnroad", {PERSISTENT, FLOAT, "0.0"}},
    {"MapdEnabled", {PERSISTENT, BOOL, "0"}},    // offline mapd (third_party/mapd): mapdOut / mapdExtendedOut for MTSC / MSLC / DLON
    {"MapdSettings", {PERSISTENT, JSON}},         // mapd's own settings (third_party/mapd), written by mapd
    {"MapdAreas", {PERSISTENT, STRING}},          // mapd download menu paths to keep offline, comma-separated (nation.KR,us_state.OH)
    {"MapdAutoUpdate", {PERSISTENT, INT, "0"}},   // refresh MapdAreas: 0 off, 1 weekly, 2 monthly (on Wi-Fi / Ethernet, offroad)
    {"MapdLastDownload", {PERSISTENT, INT}},      // unix time MapdAreas last finished downloading; cleared when the areas change
    {"MTSCEnabled", {PERSISTENT, BOOL, "0"}},      // curve speed from OSM curvature (needs mapd)
    {"MSLCEnabled", {PERSISTENT, BOOL, "0"}},      // posted speed limit from OSM (needs mapd)
    {"TLSCEnabled", {PERSISTENT, BOOL, "0"}},      // stop for a red/yellow light on our path (needs monod + gridd)
    {"DDSCEnabled", {PERSISTENT, BOOL, "0"}},      // speed cap while the driver-activity monitor says the driver is not responding
    {"RCSCEnabled", {PERSISTENT, BOOL, "0"}},      // RCSC, Road Condition Speed Controller: speed cap on a wet / icy / debris road (needs a surface source: surfaced or the camera-tier card)
    // Posted speed limit (MSLC map, NSLC navigation): one policy and one set of offsets for both.
    {"SpeedLimitPolicy", {PERSISTENT, INT, "4"}},  // 0 none, 1 car, 2 map, 3 navigation, 4 the lower of all, 5 map/navigation, car as fallback
    {"SLCOffset1", {PERSISTENT, FLOAT, "0.0"}},  // km/h over the limit for posted limits below 29 km/h
    {"SLCOffset2", {PERSISTENT, FLOAT, "0.0"}},  // km/h over the limit for posted limits 29-48 km/h
    {"SLCOffset3", {PERSISTENT, FLOAT, "0.0"}},  // km/h over the limit for posted limits 49-58 km/h
    {"SLCOffset4", {PERSISTENT, FLOAT, "0.0"}},  // km/h over the limit for posted limits 59-78 km/h
    {"SLCOffset5", {PERSISTENT, FLOAT, "0.0"}},  // km/h over the limit for posted limits 79-98 km/h
    {"SLCOffset6", {PERSISTENT, FLOAT, "0.0"}},  // km/h over the limit for posted limits 99-118 km/h
    {"SLCOffset7", {PERSISTENT, FLOAT, "0.0"}},  // km/h over the limit for posted limits 119 km/h and up
    {"SLCConfirmLower", {PERSISTENT, BOOL, "0"}},   // a lower limit waits for the gas pedal (or 30 s) before it applies
    {"SLCConfirmHigher", {PERSISTENT, BOOL, "0"}},  // a higher limit waits for the gas pedal (or 30 s) before it applies
    {"PathdEnabled", {PERSISTENT, BOOL, "0"}},
    {"MonoDEnabled", {PERSISTENT, BOOL, "0"}},
    {"MonoDHz", {PERSISTENT, INT, "5"}},
    // Driving mode: eco / normal / sport set the acceleration profile, following style and adaptive gap together;
    // custom (default) writes nothing. See exopilot/selfdrive/controls/lib/drive_mode.py.
    {"DriveMode", {PERSISTENT, STRING, "custom"}},
    {"AccelerationProfile", {PERSISTENT, STRING, "normal"}},
    // Adaptive follow gap uses only the standard radarState.leadOne observation.
    // Default off; opt-in pending vehicle validation.
    {"AdaptiveGapEnabled", {PERSISTENT, BOOL, "0"}},
    {"BRSCEnabled", {PERSISTENT, BOOL, "1"}},
    // pathd hosts the shared planner (exopilot/selfdrive/pathd/pathd.py: protection layer + rule channel + DPP) and publishes pathAdjust. Default off.
    // monod: range road-camera boxes on the road plane with a radar-anchored scale (exopilot/selfdrive/monod/ground_ranging.py) instead of the class-height prior. Default off until validate_ranging passes on a stereo route.
    {"MonoDGroundRanging", {PERSISTENT, BOOL, "0"}},
    // pathd: use the corrected distance-scale lookup in compute_speed_reduction. EOP10's original lookup never reduced speed (exopilot/selfdrive/controls/lib/speed_reduction.py). Default off = behaviour unchanged.
    {"EOPPathdFixScaleEnabled", {PERSISTENT, BOOL, "0"}},
    {"PathdFixScaleEnabled", {PERSISTENT, BOOL, "0"}},  // legacy persisted alias
    // DPP ceiling (0 idle, 1 shadow, 2 supervise, 3 primary long, 4 primary lat, 5 primary both); DPP picks the mode by case inside it. Needs PathdEnabled.
    // DLON (Dynamic Longitudinal Profile): DlonMode picks rule-based ACC, AI/E2E, or
    // Dynamic (automatic switching, the default). The per-trigger toggles below tune
    // which situational signals the Dynamic switch considers.
    {"DlonMode", {PERSISTENT, INT, "2"}},  // Drive Mode: 0 Highway (rule/ACC), 1 Urban (AI/E2E), 2 Dynamic (automatic)
    {"DLONCurvesEnabled", {PERSISTENT, BOOL, "1"}},
    {"DLONForceStopsEnabled", {PERSISTENT, BOOL, "1"}},
    // Couples DLAT's own Laneful/Laneless arbitration (see DLAT above) into
    // DLON's automatic switch: when DLAT has committed to Laneless, E2E's
    // path-only prediction fits better than lane-line-anchored ACC.
    {"DLONLaneConfidenceEnabled", {PERSISTENT, BOOL, "1"}},
    {"DLONLowSpeedEnabled", {PERSISTENT, BOOL, "1"}},
    {"DLONNavigationEnabled", {PERSISTENT, BOOL, "1"}},
    {"DLONSignalEnabled", {PERSISTENT, BOOL, "1"}},
    {"DLONSlowLeadEnabled", {PERSISTENT, BOOL, "1"}},
    {"DLONSpeedLimitEnabled", {PERSISTENT, BOOL, "1"}},
    {"DLONStopPredictionEnabled", {PERSISTENT, BOOL, "1"}},
    // Lane Change Lead Handoff: pure-camera adjacent-lane lead tracking during
    // laneChangeStarting (exopilot/selfdrive/controls/lib/lc_lead_handoff.py). No panel
    // toggle by design, matching EOP10's LCAdjacentLeadHandoff. Default off.
    {"LCAdjacentLeadHandoff", {PERSISTENT, BOOL, "0"}},
    // NSLC: cap the cruise speed at the navigation route's posted limit (exopilot/selfdrive/controls/lib/nslc.py, through
    // map_speed's speed-limit policy with MSLC). No panel toggle, matching EOP10. Default off.
    {"NSLCEnabled", {PERSISTENT, BOOL, "0"}},
    // Driver preference: constant kph offset applied to the final v_cruise
    // (after all other limits), applied where EOP10's
    // driver_prefs.py::get_speed_with_offset() applies it. Default 0
    // is a no-op, so this needs no separate enable flag. EOP10's
    // driver_prefs.py also has a following_distance/get_time_gap() concept,
    // but it's never actually called anywhere in EOP10's own
    // longitudinal_planner.py -- dead code there too, not ported here.
    {"SpeedLimitOffset", {PERSISTENT, INT, "0"}},
    // VTSC (Vision Turn Speed Control): slow down for upcoming curves (0-250m)
    // using vision-only curvature (exopilot/selfdrive/controls/lib/vtsc.py). Panel
    // toggle, matching EOP10's VTSCEnabled. Default off.
    {"VTSCEnabled", {PERSISTENT, BOOL, "0"}},
    {"ObdMultiplexingChanged", {CLEAR_ON_MANAGER_START | CLEAR_ON_ONROAD_TRANSITION, BOOL}},
    {"ObdMultiplexingEnabled", {CLEAR_ON_MANAGER_START | CLEAR_ON_ONROAD_TRANSITION, BOOL}},
    // The phone-hosted big model's counterparts of upstream's offroad big-model alerts (bigmodel_status.py)
    {"Offroad_BigModelModelError", {CLEAR_ON_MANAGER_START, JSON}},
    {"Offroad_BigModelNotDetected", {CLEAR_ON_MANAGER_START | CLEAR_ON_ONROAD_TRANSITION, JSON}},
    {"Offroad_BigModelUsbSlow", {CLEAR_ON_MANAGER_START, JSON}},
    {"Offroad_CarUnrecognized", {CLEAR_ON_MANAGER_START | CLEAR_ON_ONROAD_TRANSITION, JSON}},
    {"Offroad_ConnectivityNeeded", {CLEAR_ON_MANAGER_START, JSON}},
    {"Offroad_ConnectivityNeededPrompt", {CLEAR_ON_MANAGER_START, JSON}},
    {"Offroad_ExcessiveActuation", {PERSISTENT, JSON}},
    {"Offroad_IsTakingSnapshot", {CLEAR_ON_MANAGER_START, JSON}},
    {"Offroad_NeosUpdate", {CLEAR_ON_MANAGER_START, JSON}},
    {"Offroad_NoFirmware", {CLEAR_ON_MANAGER_START | CLEAR_ON_ONROAD_TRANSITION, JSON}},
    {"Offroad_Recalibration", {CLEAR_ON_MANAGER_START | CLEAR_ON_ONROAD_TRANSITION, JSON}},
    {"Offroad_StorageMissing", {CLEAR_ON_MANAGER_START, JSON}},
    {"Offroad_TemperatureTooHigh", {CLEAR_ON_MANAGER_START, JSON}},
    {"Offroad_UnregisteredHardware", {CLEAR_ON_MANAGER_START, JSON}},
    {"Offroad_UpdateFailed", {CLEAR_ON_MANAGER_START, JSON}},
    {"OnroadCycleRequested", {CLEAR_ON_MANAGER_START, BOOL}},
    {"OpenpilotEnabledToggle", {PERSISTENT, BOOL, "1"}},
    {"PandaHeartbeatLost", {CLEAR_ON_MANAGER_START | CLEAR_ON_OFFROAD_TRANSITION, BOOL}},
    {"PandaSomResetTriggered", {CLEAR_ON_MANAGER_START | CLEAR_ON_OFFROAD_TRANSITION, BOOL}},
    {"PandaSignatures", {CLEAR_ON_MANAGER_START, BYTES}},
    {"PrimeType", {PERSISTENT, INT}},
    {"RecordAudio", {PERSISTENT, BOOL}},
    {"RecordAudioFeedback", {PERSISTENT, BOOL, "0"}},  // for the internal fleet
    {"SecOCKey", {PERSISTENT | DONT_LOG, STRING}},
    {"RouteCount", {PERSISTENT, INT, "0"}},
    {"SnoozeUpdate", {CLEAR_ON_MANAGER_START | CLEAR_ON_OFFROAD_TRANSITION, BOOL}},
    {"SshEnabled", {PERSISTENT, BOOL}},
    {"TermsVersion", {PERSISTENT, STRING}},
    {"TrainingVersion", {PERSISTENT, STRING}},
    {"UbloxAvailable", {PERSISTENT, BOOL}},
    {"UpdateAvailable", {CLEAR_ON_MANAGER_START | CLEAR_ON_ONROAD_TRANSITION, BOOL}},
    {"UpdateFailedCount", {CLEAR_ON_MANAGER_START, INT}},
    {"EOPUpdateStatus", {CLEAR_ON_MANAGER_START, STRING}},
    {"UpdaterAvailableBranches", {PERSISTENT, STRING}},
    {"UpdaterCurrentDescription", {CLEAR_ON_MANAGER_START, STRING}},
    {"UpdaterCurrentReleaseNotes", {CLEAR_ON_MANAGER_START, BYTES}},
    {"UpdaterFetchAvailable", {CLEAR_ON_MANAGER_START, BOOL}},
    {"UpdaterNewDescription", {CLEAR_ON_MANAGER_START, STRING}},
    {"UpdaterNewReleaseNotes", {CLEAR_ON_MANAGER_START, BYTES}},
    {"UpdaterState", {CLEAR_ON_MANAGER_START, STRING}},
    {"UpdaterTargetBranch", {CLEAR_ON_MANAGER_START, STRING}},
    {"UpdaterLastFetchTime", {PERSISTENT, TIME}},
    {"UptimeOffroad", {PERSISTENT, FLOAT, "0.0"}},
    {"UptimeOnroad", {PERSISTENT, FLOAT, "0.0"}},
    {"Version", {PERSISTENT, STRING}},

    // ---- EOP keys (RK3588 port + EOP features) ----
    {"EOPBluetoothPairingActive", {CLEAR_ON_MANAGER_START, STRING, "0"}},
    {"EOPBluetoothPairingAddr", {CLEAR_ON_MANAGER_START, STRING, ""}},
    {"EOPBluetoothPairingPin", {CLEAR_ON_MANAGER_START, STRING, ""}},
    {"EOPCameraCalibrationParams", {PERSISTENT, BYTES}},
    {"EOPAEBEnabled", {PERSISTENT, BOOL, "0"}},
    {"EOPALCCAllowAlways", {PERSISTENT, BOOL, "0"}},
    {"EOPALCCBrakeMode", {PERSISTENT, STRING, "Maintain"}},
    {"EOPALCCHoldAtStandstill", {PERSISTENT, BOOL, "0"}},
    {"EOPALCCSteeringModeOnBrake", {PERSISTENT, INT, "1"}},
    {"EOPALCCUnifiedEngagement", {PERSISTENT, BOOL, "0"}},
    // Driving mode: eco / normal / sport set acceleration profile, following style and adaptive gap together; custom (default) writes nothing.
    {"EOPDriveMode", {PERSISTENT, STRING, "custom"}},
    {"EOPAccelerationProfile", {PERSISTENT, STRING, "normal"}},
    {"EOPAdaptdEnabled", {PERSISTENT, BOOL, "0"}},
    {"EOPAdaptiveGapEnabled", {PERSISTENT, BOOL, "0"}},
    {"EOPAggressiveFollow", {PERSISTENT, FLOAT, "1.25"}},
    {"EOPAggressiveJerk", {PERSISTENT, FLOAT, "0.5"}},
    {"EOPAutoLaneChange", {PERSISTENT, BOOL, "0"}},
    {"EOPAutoTileEnabled", {PERSISTENT, BOOL, "1"}},
    {"EOPAutoTileWifiOnly", {PERSISTENT, BOOL, "1"}},
    {"EOPBlindSpotIndicator", {PERSISTENT, BOOL, "1"}},
    {"EOPBSDChimeEnabled", {PERSISTENT, BOOL, "1"}},
    {"EOPBEVWidgetEnabled", {PERSISTENT, BOOL, "1"}},
    {"EOPBSDEnabled", {PERSISTENT, BOOL, "1"}},
    {"EOPBSDMinSpeed", {PERSISTENT, FLOAT, "5.5"}},
    {"EOPBluetoothCANInterface", {PERSISTENT, STRING, "can0"}},
    {"EOPBluetoothEnabled", {PERSISTENT, BOOL, "1"}},
    {"EOPBluetoothPairWindow", {PERSISTENT, INT, "300"}},
    {"EOPBluetoothRadarEnabled", {PERSISTENT, BOOL, "0"}},
    {"EOPBLECornerPairs", {PERSISTENT, STRING, ""}},
    {"EOPBLERadarPairingOpen", {PERSISTENT, BOOL, "0"}},
    {"EOPBLERadarRoster", {PERSISTENT, STRING, ""}},  // extra ESP32 corner-radar WiFi STA MACs for BLE-only nodes (system/bluetoothd/ble_central.py)
    {"EOPCATEnabled", {PERSISTENT, BOOL, "0"}},
    {"EOPCATManualSR", {PERSISTENT, FLOAT, "0.0"}},
    {"EOPCATManualSREnabled", {PERSISTENT, BOOL, "0"}},
    {"EOPCATPersist", {PERSISTENT, STRING, ""}},
    {"EOPCurrentRegion", {PERSISTENT, STRING, ""}},
    {"EOPCurveSpeedDBPath", {PERSISTENT, STRING, "/data/shared/exopilot/curve.db"}},
    {"EOPCurveSpeedLearnEnabled", {PERSISTENT, BOOL, "1"}},
    // DLAT (Dynamic Lateral Profile, dlat.py) is a default, always-on
    // automatic Laneful/Laneless arbitration -- no user-selectable mode
    // param, matching dev/NGP10's design.
    {"EOPDLONCurvesEnabled", {PERSISTENT, BOOL, "1"}},
    {"EOPDLONForceStopsEnabled", {PERSISTENT, BOOL, "1"}},
    // Couples DLAT's own Laneful/Laneless arbitration (dlat.py) into DLON's
    // automatic switch: when DLAT has committed to Laneless, E2E's path-only
    // prediction fits better than lane-line-anchored ACC.
    {"EOPDLONLaneConfidenceEnabled", {PERSISTENT, BOOL, "1"}},
    {"EOPDLONLowSpeedEnabled", {PERSISTENT, BOOL, "1"}},
    // DLON (Dynamic Longitudinal Profile) is a default, always-on automatic
    // ACC/E2E switch -- no mode param: users cannot force pure E2E
    // (Experimental) or pure ACC directly, matching dev/NGP10's design.
    {"EOPDLONNavigationEnabled", {PERSISTENT, BOOL, "1"}},
    {"EOPDLONSignalEnabled", {PERSISTENT, BOOL, "1"}},
    {"EOPDLONSlowLeadEnabled", {PERSISTENT, BOOL, "1"}},
    {"EOPDLONSpeedLimitEnabled", {PERSISTENT, BOOL, "1"}},
    {"EOPDLONStopPredictionEnabled", {PERSISTENT, BOOL, "1"}},
    {"DLPCurvesEnabled", {PERSISTENT, BOOL, "1"}},
    {"EOPDeviceAudibleAlertMode", {PERSISTENT, STRING, "Std"}},
    {"EOPDeviceAutoShutdownIn", {PERSISTENT, INT, "0"}},
    {"EOPDeviceBeep", {PERSISTENT, BOOL, "1"}},
    {"EOPDeviceGoOffRoad", {PERSISTENT, BOOL, "0"}},
    {"EOPDeviceIsRhd", {PERSISTENT, BOOL, "0"}},
    {"EOPDeviceModelList", {PERSISTENT, STRING, ""}},
    {"EOPDeviceModelSelected", {PERSISTENT, STRING, ""}},
    {"EOPDeviceName", {PERSISTENT, STRING, "EXOPILOT"}},
    {"EOPFactoryCalibrated", {PERSISTENT, BOOL, "0"}},
    {"EOPFeatureAdvancedAnalytics", {CLEAR_ON_MANAGER_START, BOOL, "0"}},
    {"EOPFeatureAiRouting", {CLEAR_ON_MANAGER_START, BOOL, "0"}},
    {"EOPFeatureCloudBackup", {CLEAR_ON_MANAGER_START, BOOL, "0"}},
    {"EOPFeatureCrossDeviceSync", {CLEAR_ON_MANAGER_START, BOOL, "0"}},
    {"EOPFeatureDrivingScore", {CLEAR_ON_MANAGER_START, BOOL, "0"}},
    {"EOPFeatureEmergencyAlert", {CLEAR_ON_MANAGER_START, BOOL, "0"}},
    {"EOPFeatureFleetMode", {CLEAR_ON_MANAGER_START, BOOL, "0"}},
    {"EOPFeatureLiveLocationShare", {CLEAR_ON_MANAGER_START, BOOL, "0"}},
    {"EOPFeatureLiveTraffic", {CLEAR_ON_MANAGER_START, BOOL, "0"}},
    {"EOPFeatureObdDashboard", {CLEAR_ON_MANAGER_START, BOOL, "0"}},
    {"EOPFeaturePriorityTiles", {CLEAR_ON_MANAGER_START, BOOL, "0"}},
    {"EOPFeatureRemoteCamera", {CLEAR_ON_MANAGER_START, BOOL, "0"}},
    {"EOPFeatureRouteReplay", {CLEAR_ON_MANAGER_START, BOOL, "0"}},
    {"EOPFeatureTelemetry", {CLEAR_ON_MANAGER_START, BOOL, "0"}},
    {"EOPFeatureTripSync", {CLEAR_ON_MANAGER_START, BOOL, "0"}},
    {"EOPFollowingDistance", {PERSISTENT, STRING, "normal"}},
    {"EOPGlobaldEnabled", {PERSISTENT, BOOL, "1"}},
    {"EOPGridEnabled", {PERSISTENT, BOOL, "1"}},
    {"EOPGroundEnabled", {PERSISTENT, BOOL, "1"}},
    {"EOPHealthMonitorEnabled", {PERSISTENT, BOOL, "1"}},
    {"EOPHybridPlannerEnabled", {PERSISTENT, BOOL, "0"}},
    {"EOPIgnitionOn", {CLEAR_ON_MANAGER_START, BOOL, "1"}},
    {"EOPLCABSMEnabled", {PERSISTENT, BOOL, "1"}},
    {"EOPLCAConfirmRequired", {PERSISTENT, BOOL, "1"}},
    {"EOPLCAControllerEnabled", {PERSISTENT, BOOL, "1"}},
    {"EOPLCAGapEvalEnabled", {PERSISTENT, BOOL, "1"}},
    {"EOPLCALaneWidthEnabled", {PERSISTENT, BOOL, "1"}},
    {"EOPLCASurroundOverride", {PERSISTENT, BOOL, "0"}},
    {"LCAdjacentLeadHandoff", {PERSISTENT, BOOL, "0"}},
    {"EOPLaneChangeDelay", {PERSISTENT, FLOAT, "1.0"}},
    {"EOPLanguage", {PERSISTENT, STRING, "en"}},
    {"EOPLatALCC", {PERSISTENT, BOOL, "1"}},
    {"EOPLatLCASpeed", {PERSISTENT, INT, "0"}},
    {"EOPLatRoadEdgeDetection", {PERSISTENT, BOOL, "1"}},
    {"EOPLeftCameraEnabled", {PERSISTENT, BOOL, "0"}},
    {"EOPLonExtRadar", {PERSISTENT, BOOL, "0"}},
    {"EOPMSLCAlert", {CLEAR_ON_MANAGER_START, STRING, ""}},
    {"EOPMSLCOffsetFixed", {PERSISTENT, INT, "0"}},
    {"EOPMSLCOffsetPercent", {PERSISTENT, INT, "0"}},
    {"EOPMapdEnabled", {PERSISTENT, BOOL, "0"}},
    {"EOPMicAdaptiveLoudness", {PERSISTENT, BOOL, "1"}},
    {"EOPMinimumLaneWidth", {PERSISTENT, FLOAT, "3.0"}},
    {"EOPMonoDEnabled", {PERSISTENT, BOOL, "1"}},
    {"EOPMonoDMaxTracks", {PERSISTENT, INT, "64"}},
    {"EOPMonoDSceneSegEnabled", {PERSISTENT, BOOL, "1"}},
    {"EOPMonoDTeleEnabled", {PERSISTENT, BOOL, "1"}},
    {"EOPMonoDWideEnabled", {PERSISTENT, BOOL, "1"}},
    {"EOPMonoDYoloConf", {PERSISTENT, FLOAT, "0.5"}},
    {"EOPMultiCameraCalibEnabled", {PERSISTENT, BOOL, "0"}},
    {"NSLCEnabled", {PERSISTENT, BOOL, "1"}},
    {"EOPNTRIPEnabled", {PERSISTENT, BOOL, "0"}},
    {"EOPNavBleEnabled", {PERSISTENT, BOOL, "1"}},
    {"EOPNavEnabled", {PERSISTENT, BOOL, "0"}},
    {"EOPNavPilotAddress", {PERSISTENT, STRING, ""}},
    {"EOPNavPilotPaired", {PERSISTENT, BOOL, "0"}},
    {"EOPNavVoiceEnabled", {PERSISTENT, BOOL, "1"}},
    {"EOPNudgeEnabled", {PERSISTENT, BOOL, "1"}},
    {"EOPOneLaneChange", {PERSISTENT, BOOL, "0"}},
    {"EOPOsmLocalizerEnabled", {PERSISTENT, BOOL, "0"}},
    {"EOPParkingMode", {CLEAR_ON_MANAGER_START, BOOL, "0"}},
    {"EOPPointcloudEnabled", {PERSISTENT, BOOL, "0"}},
    {"EOPPointcloudMaxGB", {PERSISTENT, FLOAT, "4.0"}},
    {"EOPPointcloudRateHz", {PERSISTENT, INT, "5"}},
    {"EOPPointcloudUseGPU", {PERSISTENT, BOOL, "1"}},
    {"EOPPredictEnabled", {PERSISTENT, BOOL, "1"}},
    {"EOPRTKEnabled", {PERSISTENT, BOOL, "0"}},
    {"EOPRearCameraEnabled", {PERSISTENT, BOOL, "0"}},
    {"EOPRearCameraHeight", {PERSISTENT, INT, "720"}},
    {"EOPRearCameraWidth", {PERSISTENT, INT, "1280"}},
    {"EOPRecordEnabled", {PERSISTENT, BOOL, "1"}},
    {"EOPRecordSnap", {CLEAR_ON_MANAGER_START, BOOL, "0"}},
    {"EOPRecorddImpactSensitivity", {PERSISTENT, INT, "50"}},
    {"EOPRecorddParkingEnabled", {PERSISTENT, BOOL, "1"}},
    {"EOPRecorddParkingHours", {PERSISTENT, FLOAT, "12.0"}},
    {"EOPRecorddPostBufferSec", {PERSISTENT, FLOAT, "30.0"}},
    {"EOPRecorddPreBufferSec", {PERSISTENT, FLOAT, "10.0"}},
    {"EOPRecorddQuality", {PERSISTENT, STRING, "medium"}},
    {"EOPRecorddSnapAutoG", {PERSISTENT, FLOAT, "2.0"}},
    {"EOPRecorddSnapImuMs", {PERSISTENT, INT, "500"}},
    // Pause lateral assistance with a turn signal on below this speed (mph); 0 = off. Shared with NGP10 (ngp_ prefix).
    {"EOPRedControllerEnabled", {PERSISTENT, BOOL, "1"}},
    {"EOPRelaxedFollow", {PERSISTENT, FLOAT, "1.75"}},
    {"EOPRelaxedJerk", {PERSISTENT, FLOAT, "1.0"}},
    {"EOPRightCameraEnabled", {PERSISTENT, BOOL, "0"}},
    {"EOPRoadHDR", {PERSISTENT, STRING, "hdr4"}},
    {"EOPSGMConfidenceThreshold", {PERSISTENT, FLOAT, "0.7"}},
    {"EOPSGMLocalizerEnabled", {PERSISTENT, BOOL, "0"}},
    {"EOPSGMMapPath", {PERSISTENT, STRING, "/data/maps/sgm"}},
    {"EOPSGMMaxRange", {PERSISTENT, FLOAT, "100.0"}},
    {"EOPSGMMode", {PERSISTENT, STRING, "live"}},
    {"EOPSLCConfirmHigher", {PERSISTENT, BOOL, "1"}},
    {"EOPSLCConfirmLower", {PERSISTENT, BOOL, "1"}},
    {"SLCOffset1", {PERSISTENT, FLOAT, "0.0"}},
    {"EOPSLCOffset2", {PERSISTENT, FLOAT, "0.0"}},
    {"EOPSLCOffset3", {PERSISTENT, FLOAT, "0.0"}},
    {"EOPSLCOffset4", {PERSISTENT, FLOAT, "0.0"}},
    {"EOPSLCOffset5", {PERSISTENT, FLOAT, "0.0"}},
    {"EOPSLCOffset6", {PERSISTENT, FLOAT, "0.0"}},
    {"EOPSLCOffset7", {PERSISTENT, FLOAT, "0.0"}},
    {"EOPSOCControllerEnabled", {PERSISTENT, BOOL, "1"}},
    {"EOPSPPAutoReconnect", {PERSISTENT, BOOL, "1"}},
    {"EOPSPPChannel", {PERSISTENT, INT, "1"}},
    {"EOPSPPDeviceName", {PERSISTENT, STRING, ""}},
    {"EOPSPPEnabled", {PERSISTENT, BOOL, "0"}},
    {"EOPSPPMaxClients", {PERSISTENT, INT, "3"}},
    {"EOPSPPPairedDevice", {PERSISTENT, STRING, ""}},
    {"EOPSPPRpcRequest", {CLEAR_ON_MANAGER_START, STRING, ""}},
    {"EOPSPPRpcResponse", {CLEAR_ON_MANAGER_START, STRING, ""}},
    {"EOPSQSCEnabled", {PERSISTENT, BOOL, "1"}},
    {"EOPSQSCLookaheadEnabled", {PERSISTENT, BOOL, "1"}},
    {"EOPSQSCProfile", {PERSISTENT, STRING, "balanced"}},
    {"EOPSQSCRecordMinLeadM", {PERSISTENT, FLOAT, "40.0"}},
    {"EOPSQSCRecordMinQuality", {PERSISTENT, FLOAT, "0.25"}},
    {"EOPSQSCRecordMinSpeedKph", {PERSISTENT, FLOAT, "18.0"}},
    {"EOPSQSCSensitivity", {PERSISTENT, FLOAT, "1.0"}},
    {"EOPSQSCSmoothing", {PERSISTENT, FLOAT, "2.0"}},
    {"EOPSQSCUseLearnedSpeed", {PERSISTENT, BOOL, "1"}},
    {"EOPSafetyDriverTorqueThreshold", {PERSISTENT, INT, "200"}},
    {"EOPSafetyEventLogEnabled", {PERSISTENT, BOOL, "1"}},
    {"EOPSafetyEventLogPath", {PERSISTENT, STRING, "/data/safety_violations.log"}},
    {"EOPSafetyHeartbeatTimeout", {PERSISTENT, INT, "200"}},
    {"EOPSafetyMaxAccel", {PERSISTENT, INT, "340"}},
    {"EOPSafetyMaxAngleError", {PERSISTENT, INT, "240"}},
    {"EOPSafetyMaxSteeringAngle", {PERSISTENT, INT, "2700"}},
    {"EOPSafetyMaxSteeringRate", {PERSISTENT, INT, "200"}},
    {"EOPSafetyMessageTimeout", {PERSISTENT, INT, "1200"}},
    {"EOPSafetyMinAccel", {PERSISTENT, INT, "312"}},
    {"EOPShockDepthM", {PERSISTENT, FLOAT, "0.08"}},
    {"EOPShockDetection", {PERSISTENT, BOOL, "1"}},
    {"EOPShockDuration", {PERSISTENT, INT, "3"}},
    {"EOPShockHeightM", {PERSISTENT, FLOAT, "0.05"}},
    {"EOPShockSpeedLimit", {PERSISTENT, INT, "15"}},
    {"EOPShockThreshold", {PERSISTENT, FLOAT, "2.5"}},
    {"EOPSideCameraHeight", {PERSISTENT, INT, "720"}},
    {"EOPSideCameraWidth", {PERSISTENT, INT, "1280"}},
    {"EOPSideCamerasEnabled", {PERSISTENT, BOOL, "0"}},
    {"EOPSideCamerasSwapped", {PERSISTENT, BOOL, "0"}},
    // One-shot: UI sets it, camera_calibrationd.py resets side/rear
    // calibration and clears it back to false. Not PERSISTENT -- a stuck
    // "true" across a manager restart would silently reset calibration on
    // every boot, same reasoning as DoReboot/DoShutdown.
    {"EOPSideRearCalibReset", {CLEAR_ON_MANAGER_START, BOOL, "0"}},
    // Empty means "the board that is running" -- the sim bridge falls back
    // to HARDWARE.get_device_type(). A literal here was one board's name
    // baked into a branch-independent default.
    {"EOPSimPlatform", {PERSISTENT, STRING, ""}},
    {"EOPSimScenario", {PERSISTENT, STRING, ""}},
    {"EOPSimWeather", {PERSISTENT, STRING, ""}},
    {"EOPSpeedLimitOffset", {PERSISTENT, INT, "0"}},
    {"EOPStandardFollow", {PERSISTENT, FLOAT, "1.45"}},
    {"EOPStandardJerk", {PERSISTENT, FLOAT, "1.0"}},
    {"EOPStereoEnabled", {PERSISTENT, BOOL, "1"}},
    {"EOPSubscriptionCheckInterval", {PERSISTENT, INT, "30"}},
    {"EOPSubscriptionDeviceId", {PERSISTENT, STRING, ""}},
    {"EOPSubscriptionExpiry", {PERSISTENT, STRING, ""}},
    {"EOPSubscriptionSignature", {PERSISTENT | DONT_LOG, STRING, ""}},
    {"EOPSubscriptionSkipVerify", {PERSISTENT, BOOL, "0"}},
    {"EOPSubscriptionTier", {PERSISTENT, STRING, "none"}},
    {"EOPSubscriptionToken", {PERSISTENT | DONT_LOG, STRING, ""}},
    {"EOPSurfaceEnabled", {PERSISTENT, BOOL, "1"}},
    {"EOPSurfaceGridRange", {PERSISTENT, FLOAT, "60.0"}},
    {"EOPSurfaceGridResolution", {PERSISTENT, FLOAT, "0.25"}},
    {"EOPSurfaceGridWidth", {PERSISTENT, FLOAT, "30.0"}},
    {"EOPSurfaceLongHorizon", {PERSISTENT, BOOL, "1"}},
    {"EOPSurfaceLongRange", {PERSISTENT, FLOAT, "100.0"}},
    {"EOPTJAEnabled", {PERSISTENT, BOOL, "0"}},
    {"EOPTJAMaxHoldMinutes", {PERSISTENT, INT, "10"}},
    {"EOPTSCTargetLatAccel", {PERSISTENT, FLOAT, "1.8"}},
    {"EOPTSCTargetLatAccelMedium", {PERSISTENT, FLOAT, "1.8"}},
    {"EOPTSCTargetLatAccelMild", {PERSISTENT, FLOAT, "2.0"}},
    {"EOPTSCTargetLatAccelSharp", {PERSISTENT, FLOAT, "1.5"}},
    {"EOPTTSAlertsEnabled", {PERSISTENT, BOOL, "1"}},
    {"EOPTTSVoice", {PERSISTENT, STRING, "auto"}},
    {"EOPTTSVolume", {PERSISTENT, FLOAT, "1.0"}},
    {"EOPTeleEnabled", {PERSISTENT, BOOL, "0"}},
    {"EOPTeleHDR", {PERSISTENT, STRING, "hdr4"}},
    {"EOPTileLastUpdate", {PERSISTENT, INT, "0"}},
    {"EOPTouchEventNode", {PERSISTENT, STRING, ""}},
    {"EOPTrackEnabled", {PERSISTENT, BOOL, "1"}},
    {"EOPTrafficFollow", {PERSISTENT, FLOAT, "1.35"}},
    {"EOPTrafficJerk", {PERSISTENT, FLOAT, "0.8"}},
    {"EOPTripAStartDistance", {PERSISTENT, FLOAT, "0.0"}},
    {"EOPTripAStartTime", {PERSISTENT, FLOAT, "0.0"}},
    {"EOPTripBStartDistance", {PERSISTENT, FLOAT, "0.0"}},
    {"EOPTripBStartTime", {PERSISTENT, FLOAT, "0.0"}},
    {"EOPTripCurrentEngagementRatio", {PERSISTENT, FLOAT, "0.0"}},
    {"EOPTripLastDistance", {PERSISTENT, FLOAT, "0.0"}},
    {"EOPTripLastDuration", {PERSISTENT, FLOAT, "0.0"}},
    {"EOPTripLastMaxAccel", {PERSISTENT, FLOAT, "0.0"}},
    {"EOPTripLastOverrideFreeDistance", {PERSISTENT, FLOAT, "0.0"}},
    {"EOPTripLastPersonalityTime", {PERSISTENT, STRING, ""}},
    {"EOPTripLifetimeEngagementRatio", {PERSISTENT, FLOAT, "0.0"}},
    {"EOPTripMode", {PERSISTENT, INT, "0"}},
    {"EOPTripTotalDistance", {PERSISTENT, FLOAT, "0.0"}},
    {"EOPTripTotalDrives", {PERSISTENT, INT, "0"}},
    {"EOPTripDailyStats", {PERSISTENT, STRING}},  // JSON {date: [distance, onroad_time, engaged_time, drives]}, last 7 days
    {"EOPTripUptimeEngaged", {PERSISTENT, FLOAT, "0.0"}},
    {"EOPTripUptimeOnroad", {PERSISTENT, FLOAT, "0.0"}},
    {"EOPUIBrightness", {PERSISTENT, INT, "0"}},
    {"EOPUIDisplayMode", {PERSISTENT, BOOL, "0"}},
    {"EOPUIHideHudSpeedKph", {PERSISTENT, INT, "0"}},
    {"EOPUIRadarTracks", {PERSISTENT, BOOL, "0"}},
    {"EOPUIRainbow", {PERSISTENT, BOOL, "0"}},
    {"EOPUploadCurrentHeaders", {CLEAR_ON_MANAGER_START, STRING, ""}},
    {"EOPUploadCurrentId", {CLEAR_ON_MANAGER_START, STRING, ""}},
    {"EOPUploadCurrentPath", {CLEAR_ON_MANAGER_START, STRING, ""}},
    {"EOPUploadCurrentProgress", {CLEAR_ON_MANAGER_START, STRING, ""}},
    {"EOPUploadCurrentSize", {CLEAR_ON_MANAGER_START, STRING, ""}},
    {"EOPUploadCurrentUrl", {CLEAR_ON_MANAGER_START, STRING, ""}},
    {"VTSCEnabled", {PERSISTENT, BOOL, "0"}},
    {"EOPValhallaUrl", {PERSISTENT, STRING, ""}},
    {"EOPVehicleType", {PERSISTENT, STRING, "SUV_C"}},
    {"EOPWideHDR", {PERSISTENT, STRING, "hdr4"}},
    {"EOPFactoryCalibrationParams", {PERSISTENT, BYTES}},
    {"EOPCarMake", {PERSISTENT, STRING}},
    {"EOPCarType", {PERSISTENT, STRING}},
    {"EOPCarVin", {PERSISTENT, STRING}},
    {"EOPCurrentMCAPRoute", {CLEAR_ON_MANAGER_START, STRING}},
    {"EOPWakeEnabled", {PERSISTENT, BOOL, "0"}},
    {"EOPVoiceWakeTrigger", {CLEAR_ON_MANAGER_START, BOOL, "0"}},
    {"EOPCloudVoiceEnabled", {PERSISTENT, BOOL, "0"}},
    {"EOPVoiceProcessing", {CLEAR_ON_MANAGER_START, BOOL, "0"}},
    {"EOPVoiceRecording", {CLEAR_ON_MANAGER_START, BOOL, "0"}},
    {"EOPVoiceEnabled", {PERSISTENT, BOOL}},
    {"EOPLastGPSAltitude", {PERSISTENT, STRING}},
    {"EOPLastGPSLatitude", {PERSISTENT, STRING}},
    {"EOPLastGPSLongitude", {PERSISTENT, STRING}},
    // BRSC: Bumpy Road Speed Controller — shared across EOP10/NGP10/EDP10 via
    // exopilot/selfdrive/controls/lib/brsc.py; NGP prefix (not EOP) marks features
    // ported verbatim across all three branches. See docs/eop/03_Software/Controllers/BRSC.md
    {"EOPNavPilotOAuthEmail", {PERSISTENT | DONT_LOG, STRING}},
    {"EOPNavPilotOAuthToken", {PERSISTENT | DONT_LOG, STRING}},
    {"EOPOpenBLTFirmwareVersion", {PERSISTENT, STRING}},
    {"EOPOpenBLTState", {CLEAR_ON_MANAGER_START, STRING, "idle"}},
    {"EOPOpenBLTUpdateAvailable", {CLEAR_ON_MANAGER_START, BOOL, "0"}},
    {"EOPUbloxAssistNowToken", {PERSISTENT | DONT_LOG, STRING}},
    {"EOPNavDestination", {CLEAR_ON_MANAGER_START, STRING, ""}},
    {"EOPNavDestinationWaypoints", {CLEAR_ON_MANAGER_START, STRING, ""}},
    {"EOPNeuralNetworkLateralControl", {PERSISTENT, BOOL, "0"}},
    {"EOPSpeedLimitPolicy", {PERSISTENT, INT, "4"}},
    {"EOPSteamDAuthToken", {PERSISTENT | DONT_LOG, STRING, ""}},
    {"EOPSteamDControlRequest", {CLEAR_ON_MANAGER_START, STRING, ""}},
    {"EOPSteamDEnabled", {PERSISTENT, BOOL, "0"}},
    {"EOPSteamDGeofencePolygon", {PERSISTENT, STRING, ""}},
    {"EOPSteamDJoystickInput", {PERSISTENT, BOOL, "1"}},
    {"EOPSteamDRemoteControl", {CLEAR_ON_MANAGER_START | CLEAR_ON_OFFROAD_TRANSITION, BOOL, "0"}},
};

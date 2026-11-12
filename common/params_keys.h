#pragma once

#include <string>
#include <unordered_map>

#include "cereal/gen/cpp/log.capnp.h"

inline static std::unordered_map<std::string, ParamKeyAttributes> keys = {
    {"AccessToken", {CLEAR_ON_MANAGER_START | DONT_LOG, STRING}},
    {"AdbEnabled", {PERSISTENT, BOOL}},
    {"AlwaysOnDM", {PERSISTENT, BOOL}},
    {"ApiCache_Device", {PERSISTENT, STRING}},
    {"ApiCache_FirehoseStats", {PERSISTENT, JSON}},
    {"AssistNowToken", {PERSISTENT, STRING}},
    {"AthenadPid", {PERSISTENT, INT}},
    {"AthenadUploadQueue", {PERSISTENT, JSON}},
    {"AthenadRecentlyViewedRoutes", {PERSISTENT, STRING}},
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
    {"IsDriverViewEnabled", {CLEAR_ON_MANAGER_START, BOOL}},
    {"IsEngaged", {PERSISTENT, BOOL}},
    {"IsLdwEnabled", {PERSISTENT, BOOL}},
    {"IsMetric", {PERSISTENT, BOOL}},
    {"IsOffroad", {CLEAR_ON_MANAGER_START, BOOL}},
    {"IsOnroad", {PERSISTENT, BOOL}},
    {"IsRhdDetected", {PERSISTENT, BOOL}},
    {"IsReleaseBranch", {CLEAR_ON_MANAGER_START, BOOL}},
    {"IsTakingSnapshot", {CLEAR_ON_MANAGER_START, BOOL}},
    {"IsTestedBranch", {CLEAR_ON_MANAGER_START, BOOL}},
    {"JoystickDebugMode", {CLEAR_ON_MANAGER_START | CLEAR_ON_OFFROAD_TRANSITION, BOOL}},
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
    // DLAT (Dynamic Lateral Profile, exopilot/controls/dlat.py) is a
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
    {"LatLCASpeed", {PERSISTENT, INT, "20"}},
    {"LatRoadEdgeDetection", {PERSISTENT, BOOL, "0"}},
    // BRSC: Bumpy Road Speed Controller — shared verbatim across EOP10/NGP10/EDP10
    // via exopilot/controls/brsc.py. See docs/eop/03_Software/Controllers/BRSC.md on dev/EOP10.
    // Notice when the lead pulls away while stopped (radarState only). Default off.
    {"LeadDepartAlert", {PERSISTENT, BOOL, "0"}},
    // Run the big driving model on a phone over the USB cable (NavPilot's NPBM link, NAVPILOT_BIGMODEL.md). 0 off,
    // 1 Android (FunctionFS bulk gadget), 2 iPhone (CDC-NCM network the phone dials), 3 USB tethering (optional). The direct
    // modes 1 and 2 are the default way (NAVPILOT_BIGMODEL.md D11). Default off; the small model stays loaded and drives
    // until the phone model is proven.
    {"BigModelLink", {PERSISTENT, INT, "0"}},
    // Big model state, written by modeld's BigModelHook and read by selfdrived (exopilot/controls/bigmodel_gate.py):
    // loading/proving blocks engagement; active is false once the big model handed back or failed.
    {"BigModelActive", {CLEAR_ON_MANAGER_START, BOOL}},
    {"BigModelLoading", {CLEAR_ON_MANAGER_START, BOOL}},
    // Driver-activity decay: relaxed/tight; strict retains the deployed relaxed table.
    {"DriverActivityPolicy", {PERSISTENT, STRING, "strict"}},
    // monod (exopilot/runtime/monod.py): road-camera YOLO detections, publish-only. Default off; needs a detector model compiled in.
    // Cut-in speed trim (cutin_speed.py): lowers cruise speed for a predicted cut-in seen by monod. Default off; needs MonoDEnabled.
    {"CutInSpeedEnabled", {PERSISTENT, BOOL, "0"}},
    // pathd add-on (exopilot/runtime/pathd.py): publishes pathAdjust (lateral offset + speed factor). Publish-only, default off.
    // pathd add-on consumers (pathd_consumer.py), default off, need PathdEnabled: lateral offset in controlsd, speed cap in the planner.
    {"PathdLateral", {PERSISTENT, BOOL, "0"}},
    {"PathdLongitudinal", {PERSISTENT, BOOL, "0"}},
    // DPP ceiling for the parallel rule channel (0 idle, 1 shadow, 2 supervise, 3 primary long, 4 primary lat, 5 primary both). DPP picks the mode by case inside it. Needs PathdEnabled.
    {"DPPMaxMode", {PERSISTENT, INT, "0"}},
    // pathd: also run the ported EOP10 proposers (LatNudge, LonNudge, speed reduction) from camera-only inputs (exopilot/runtime/nudge_extras.py). Default off; needs PathdEnabled.
    {"PathdNudgesEnabled", {PERSISTENT, BOOL, "0"}},
    // Map / light / distraction speed caps (exopilot/runtime/map_speed.py), all default off, they only lower the cruise speed.
    // tripd (exopilot/runtime/tripd.py): trip statistics. Process behind TripdEnabled.
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
    {"MapdEnabled", {PERSISTENT, BOOL, "0"}},    // mapd: OSM tiles for MTSC/MSLC (uses the public Overpass servers; see docs)
    {"MTSCEnabled", {PERSISTENT, BOOL, "0"}},      // curve speed from OSM curvature (needs mapd)
    {"MSLCEnabled", {PERSISTENT, BOOL, "0"}},      // posted speed limit from OSM (needs mapd)
    {"TLSCEnabled", {PERSISTENT, BOOL, "0"}},      // stop for a red/yellow light on our path (needs monod + gridd)
    {"DDSCEnabled", {PERSISTENT, BOOL, "0"}},      // speed cap while the driver-activity monitor says the driver is not responding
    {"RCDEnabled", {PERSISTENT, BOOL, "0"}},       // speed cap on a wet / icy / debris road (needs a surface source: surfaced or the camera-tier card)
    {"SharedSLCOffsets", {PERSISTENT, STRING, ""}},   // MSLC offsets in km/h per posted-limit bucket, e.g. "5,5,5,5,5,5,5"
    {"PathdEnabled", {PERSISTENT, BOOL, "0"}},
    {"MonoDEnabled", {PERSISTENT, BOOL, "0"}},
    {"MonoDHz", {PERSISTENT, INT, "5"}},
    // Driving mode: eco / normal / sport set the acceleration profile, following style and adaptive gap together;
    // custom (default) writes nothing. See exopilot/controls/drive_mode.py.
    {"DriveMode", {PERSISTENT, STRING, "custom"}},
    {"AccelerationProfile", {PERSISTENT, STRING, "normal"}},
    // Adaptive follow gap uses only the standard radarState.leadOne observation.
    // Default off; opt-in pending vehicle validation.
    {"AdaptiveGapEnabled", {PERSISTENT, BOOL, "0"}},
    {"BRSCEnabled", {PERSISTENT, BOOL, "1"}},
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
    // laneChangeStarting (exopilot/controls/lc_lead_handoff.py). No panel
    // toggle by design, matching EOP10's LCAdjacentLeadHandoff. Default off.
    {"LCAdjacentLeadHandoff", {PERSISTENT, BOOL, "0"}},
    // NSLC-equivalent: clamp cruise speed to the posted navigation speed
    // limit (exopilot/controls/speed_policy.py, NAVIGATION policy).
    // Nav-only -- NGP10 has no map-data source (see
    // exopilot/docs/EOP10_PARITY_CANDIDATES.md's Tier 2.5). No panel
    // toggle, matching EOP10's NSLCEnabled. Default off.
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
    // using vision-only curvature (exopilot/controls/vtsc.py). Panel
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
    {"RecordAudioFeedback", {PERSISTENT, BOOL, "0"}},
    {"RecordFront", {PERSISTENT, BOOL}},
    {"RecordFrontLock", {PERSISTENT, BOOL}},  // for the internal fleet
    {"SecOCKey", {PERSISTENT | DONT_LOG, STRING}},
    {"RouteCount", {PERSISTENT, INT, "0"}},
    {"SnoozeUpdate", {CLEAR_ON_MANAGER_START | CLEAR_ON_OFFROAD_TRANSITION, BOOL}},
    {"SshEnabled", {PERSISTENT, BOOL}},
    {"TermsVersion", {PERSISTENT, STRING}},
    {"TrainingVersion", {PERSISTENT, STRING}},
    {"UbloxAvailable", {PERSISTENT, BOOL}},
    {"UpdateAvailable", {CLEAR_ON_MANAGER_START | CLEAR_ON_ONROAD_TRANSITION, BOOL}},
    {"UpdateFailedCount", {CLEAR_ON_MANAGER_START, INT}},
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
};

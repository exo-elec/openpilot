# Names without a product prefix

Owner decision 2026-11-12: no `ngp_` / `eop_` / `NGP` / `EOP` prefix anywhere, and no
replacement prefix or suffix. Each name says what the thing does, short and plain, and
param keys use the openpilot / sunnypilot style (`CamelCase`, e.g. `GreenLightAlert`).

How it is applied, one branch at a time, from the base up:

1. `dev/NGP10` (base) gets one rename commit for every name it owns (this table).
2. `dev/EOP10` is rebased onto it: each of its commits is replayed with the NGP10 names
   translated, so its history and lineage (`exopilot/lineage.py`) carry over. Then one
   EOP10 rename commit covers the names EOP10 adds.
3. Same for `dev/01M` on the new EOP10, then `dev/02M` on the new 01M.

Saved settings move to the new key once at manager start (old value kept if the new key
is already set). Capnp fields keep their ordinals, so old logs still read.

Branch names (`dev/NGP10`, `dev/EOP10`, ...) and the product name in prose stay.

## 1. NGP10: param keys

Where EOP10 already has a key for the same feature with the same type and default, NGP10
takes that name, so the two branches share one key and EOP10's legacy-alias layer
(`EOP_LEGACY_KEYS`, `OriginParams`, `NGP_KEYS`/`EOP_KEYS`) is no longer needed.

| Old | New | Note |
|---|---|---|
| `ngp_lon_drive_mode` | `DriveMode` | = EOP10 |
| `ngp_lon_accel_profile` | `AccelerationProfile` | = EOP10 |
| `ngp_lon_adaptive_gap` | `AdaptiveGapEnabled` | = EOP10 |
| `ngp_lon_dlon_curves` … `_stop_prediction` (9) | `DLONCurvesEnabled`, `DLONForceStopsEnabled`, `DLONLaneConfidenceEnabled`, `DLONLowSpeedEnabled`, `DLONNavigationEnabled`, `DLONSignalEnabled`, `DLONSlowLeadEnabled`, `DLONSpeedLimitEnabled`, `DLONStopPredictionEnabled` | = EOP10 |
| `ngp_lon_mtsc` / `_mslc` / `_tlsc` / `_ddsc` / `_rcd` | `MTSCEnabled` / `MSLCEnabled` / `TLSCEnabled` / `DDSCEnabled` / `RCDEnabled` | = EOP10; NGP10's own `EOPMTSCEnabled`… merge into the same keys |
| `ngp_lon_slc_offsets` | `SharedSLCOffsets` | = EOP10 |
| `ngp_pathd_nudges` | `PathdNudgesEnabled` | = EOP10 |
| `ngp_lon_vtsc` | `VTSCEnabled` | = EOP10 |
| `ngp_lon_lc_lead_handoff` | `LCAdjacentLeadHandoff` | = EOP10 |
| `ngp_lon_speed_offset_kph` | `SpeedLimitOffset` | = EOP10 |
| `ngp_lat_cat` | `CATEnabled` | = EOP10 |
| `ngp_lat_dlp_curves` | `DLPCurvesEnabled` | = EOP10 |
| `ngp_map_enabled` | `MapdEnabled` | = EOP10 |
| `ngp_ui_brightness` | `UIBrightness` | = EOP10 |
| `ngp_ui_hide_hud_speed_kph` | `UIHideHudSpeedKph` | = EOP10 |
| `ngp_device_vehicle_selected` | `DeviceModelSelected` | = EOP10 |
| `ngp_trip_*` (15) | `TripAStartDistance`, `TripAStartTime`, `TripBStartDistance`, `TripBStartTime`, `TripLastDistance`, `TripLastDuration`, `TripLastMaxAccel`, `TripLastOverrideFreeDistance`, `TripLastPersonalityTime`, `TripLifetimeEngagementRatio`, `TripTotalDistance`, `TripTotalDrives`, `TripDailyStats`, `TripUptimeEngaged`, `TripUptimeOnroad` | = EOP10 |
| `ngp_lat_alcc` | `LatALCC` | same feature as EOP10; default stays 0 on NGP10, EOP10 keeps its 1 **(merged)** |
| `ngp_lat_lca_speed` | `LatLCASpeed` | same; NGP10 default 20, EOP10 0 **(merged)** |
| `ngp_lat_lca_gap_eval` / `_lane_width` | `LCAGapEvalEnabled` / `LCALaneWidthEnabled` | same; defaults 0 vs 1 **(merged)** |
| `ngp_lat_road_edge_detection` | `LatRoadEdgeDetection` | same; 0 vs 1 **(merged)** |
| `ngp_lat_edge_guard` | `RedControllerEnabled` | RED switch; 0 vs 1 **(merged)** |
| `ngp_lat_soc` | `SOCControllerEnabled` | SOC switch; 0 vs 1 **(merged)** |
| `ngp_lon_nslc` | `NSLCEnabled` | 0 vs 1 **(merged)** |
| `ngp_monod_enabled` | `MonoDEnabled` | 0 vs 1 **(merged)** |
| `ngp_monod_hz` | `MonoDHz` | new |
| `ngp_device_shutdown_minutes` | `OffroadShutdownMinutes` | not merged: -1 = stock policy, unlike EOP10's `DeviceAutoShutdownIn` |
| `ngp_device_audible_mode` | `AlertSoundMode` | not merged: INT here, STRING `DeviceAudibleAlertMode` on EOP10 |
| `ngp_device_vehicle_list` | `VehicleModelList` | not merged: cleared each start here, persistent on EOP10 |
| `ngp_device_logger_delay_seconds` | `LoggerStartDelay` | new |
| `ngp_lat_lca_auto_sec` | `AutoLaneChangeDelay` | not merged: 0 = off, EOP10's `LaneChangeDelay` is a different timer |
| `ngp_lat_blinker_pause_mph` | `BlinkerPauseLateralSpeed` | sunnypilot-style |
| `ngp_lat_turn_desire_mph` | `TurnDesireSpeed` | |
| `ngp_lon_green_light` | `GreenLightAlert` | sunnypilot name |
| `ngp_lon_lead_departure` | `LeadDepartAlert` | sunnypilot name |
| `ngp_lon_brsc` | `BRSCEnabled` | |
| `ngp_lon_cutin` | `CutInSpeedEnabled` | |
| `ngp_lat_pathd` / `ngp_lon_pathd` | `PathdLateral` / `PathdLongitudinal` | |
| `ngp_pathd_enabled` | `PathdEnabled` | |
| `ngp_dpp_max_mode` | `DPPMaxMode` | |
| `ngp_dm_policy` | `DriverActivityPolicy` | it sets the driver-activity monitor, not camera DM |
| `ngp_tripd_enabled` | `TripdEnabled` | |
| `ngp_dashboard_enabled` | `DashboardEnabled` | |
| `ngp_bigmodel_link` / `_active` / `_loading` | `BigModelLink` / `BigModelActive` / `BigModelLoading` | |

Rows in **bold**: same feature, different default per branch; merged (owner, 2026-11-12: one
name, do not separate). Each branch keeps its own default in its own `params_keys.h`, so NGP10
behaves exactly as before; on EOP10 the shared code reads EOP10's key and EOP10's default.

## 2. NGP10: modules and classes

One implementation per feature, owned by NGP10 (owner, 2026-11-12: implement DLON on NGP10,
DLAT as well, every other feature a comma 3 can handle, and monod's YOLOv8 now that the driver
camera budget is free). So the NGP10 class takes the feature's name, and EOP10's separate copy
(`selfdrive/controls/lib/dlon.py`, ...) is folded into it when EOP10 is rebased; only inputs that
need ExoPilot hardware (side/rear YOLO, stereo, radar) stay on EOP10.

| Old file / class | New file / class |
|---|---|
| `ngp_dlon.py` / `NGPDLON`, `NGPDLONMode`, `NGPDriveMode`, `NGPModeTransitionManager` | `dlon.py` / `DLON`, `DLONMode`, `DriveMode`, `ModeTransitionManager` (EOP10's names) |
| `ngp_dlat.py` / `NGPDLAT` | `dlat.py` / `DLAT` |
| `ngp_vtsc.py` / `NGPVTSC` | `vtsc.py` / `VTSC` |
| `ngp_alcc.py` / `NGPALCC` | `alcc.py` / `ALCC` |
| `ngp_cat.py` / `NGPCAT` | `cat.py` / `CAT` |
| `ngp_red.py` / `NGPRED` | `red.py` / `RED` |
| `ngp_soc.py` / `NGPSOC` | `soc.py` / `SOC` |
| `ngp_brsc.py` / `NGPBRSC` | `brsc.py` / `BRSC` |
| `ngp_lane_change.py` | `lane_gap.py` (gap + lane-width gates; EOP10's `eop_lane_change.py` becomes `lane_change.py`) |
| `ngp_bigmodel_gate.py` / `NGPBigModelGate` | `bigmodel_gate.py` / `BigModelGate` |
| `ngp_blinker_pause.py` / `NGPBlinkerPause` | `blinker_pause.py` / `BlinkerPause` |
| `ngp_green_light.py` / `NGPGreenLight` | `green_light.py` / `GreenLightNotice` |
| `ngp_lead_departure.py` / `NGPLeadDeparture` | `lead_departure.py` / `LeadDepartureNotice` |
| `ngp_lc_lead_handoff.py` / `NGPLeadHandoff` | `lc_lead_handoff.py` / `LeadHandoff` |
| `ngp_speed_policy.py` / `NGPSpeedPolicy` | `speed_policy.py` / `SpeedPolicy` |
| `ngp_turn_desire.py` / `NGPTurnDesire` | `turn_desire.py` / `TurnDesire` |
| `NGPFlags` / `ngp_flags` (planner feature bits) | `FeatureFlags` / `feature_flags` |
| `runtime/bigmodel.py` `NgpSmallModel` | `LocalModel` (the comma 3's own model while the phone model is off) |
| `runtime/bigmodel_warp.py` `NgpWarp` | `ModelWarp` |
| `eop_mtsc.py`, `eop_mslc.py`, `eop_tlsc.py`, `eop_ddsc.py`, `eop_rcd.py`, `eop_lat_nudge.py`, `eop_lon_nudge.py`, `eop_predict.py`, `eop_speed_reduction.py`, `eop_curve_speed.py`, `eop_curvature.py` (already on NGP10) | `mtsc.py`, `mslc.py`, ... |
| all other `ngp_X.py` (arbiter, bigmodel_*, cutin_speed, detect, dpp, drive_mode, driver_activity, object_tracker, path_selector, pathd_consumer, policy_arbiter, radar2d, ranging, road_edge, rule_planner, tja, vehicle_model) | `X.py`, classes unchanged |
| `ngp_curve_speed`, `ngp_ddsc`, `ngp_lat_nudge`, `ngp_lon_nudge`, `ngp_mslc`, `ngp_mtsc`, `ngp_predict`, `ngp_rcd`, `ngp_speed_reduction`, `ngp_tlsc`, `ngp_longitudinal_policy` | deleted: 5-line aliases |
| `exopilot/tests/test_ngp_X.py` / `test_eop_X.py` | `test_X.py` |
| `selfdrive/ui/qt/offroad/ngp_panel.*` / `NGPPanel`, tab "NGP" | `features_panel.*` / `FeaturesPanel`, tab "Features" |
| `selfdrive/ui/qt/offroad/ngp_controls.h` | `param_controls.h` |
| `bigmodel/scripts/ngp-bigmodel-root.sh`, `/dev/ffs-ngpbigmodel`, `NGP_CONFIGFS`, `NGP_UDC_DIR`, `NGP_RUN_DIR`, `NGP_TEST`, `NGP_FFS_MOUNT`, `NGP_USER` | `bigmodel-root.sh`, `/dev/ffs-bigmodel`, `BIGMODEL_CONFIGFS`, ... (test hooks named after the feature) |
| `validate_ranging.py` report fields `ngp_by_class`, `ngp_median_*`, `ngp_p90_*` | `ground_by_class`, `ground_median_*`, `ground_p90_*` (the ground-plane ranger) |
| `docs/integration/SHARED_NGP_EOP_PIPELINE.md` | `SHARED_BRANCH_PIPELINE.md` |
| `feature_keys.py` `NGP_KEYS` / `EOP_KEYS` / `EOP_MAP_KEYS` / `EOP_LEGACY_KEYS` / `OriginParams` | `LONGITUDINAL_KEYS` / (same) / `MAP_SPEED_KEYS` / removed: one key per feature, saved values moved by `exopilot/param_migration.py` |

`exopilot/tests/test_boundaries.py` fails on any `ngp_` / `eop_` / `NGP` / `EOP` prefix in a file
name or in code; only the saved-param migration spells the old names.

## 3. NGP10: capnp fields (ordinals unchanged)

| Old | New |
|---|---|
| `ngpAlccActive` | `alccActive` |
| `ngpBrscActive` / `ngpBrscSpeed` / `ngpBrscRoughness` | `brscActive` / `brscSpeed` / `brscRoughness` |
| `ngpDlatLaneConfidence`, `ngpDlatUseLaneless` | `dlatLaneConfidence`, `dlatUseLaneless` |
| `ngpDlonE2EEnabled`, `ngpDlonForceStop`, `ngpDlonMode`, `ngpDlonModeKind` (`NgpDlonModeKind`) | `dlonE2EEnabled`, `dlonForceStop`, `dlonMode`, `dlonModeKind` (`DlonModeKind`) |
| `ngpTjaActive`, `ngpTjaCutIn`, `ngpTjaDesiredGap` | `tjaActive`, `tjaCutIn`, `tjaDesiredGap` |

EOP10 already has fields with some of these names (`tjaActive`, `dlatUseLaneless`,
`dlonMode`, …) at other ordinals. Those are resolved in the EOP10 step: same meaning →
one field, the other ordinal kept as a deprecated placeholder; different meaning → named
for what it carries.

## 4. Later layers (EOP10, 01M, 02M), to be filled in at each step

`eop_` modules (`eop_mtsc.py` → `mtsc.py`, `eop_utils.py`, `eop_panel.*` / `EopPanel`, …),
`EOPCapabilities`, `EOPTripStats`, `EopControlsState` / `EopThermalStatus` (clash with
upstream structs, named by content), local variables such as `eop_params` / `eop_keys`,
and the `EOP_*` environment variables (each checked: drop where nothing outside reads it,
otherwise named after the feature, e.g. `EOP_CLOUD_URL` → `CLOUDD_URL`).

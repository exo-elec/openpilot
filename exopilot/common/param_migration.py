"""One-time move of saved params to their unprefixed names (owner decision 2026-11-12: no product prefix).

Params are plain files under Params().get_param_path(); the old names are no longer registered, so they cannot be
read through the Params API. The stored files are renamed before the manager reads anything. A value already saved
under the new name wins and the stale old file is dropped. Several old names map to one key where two settings of
the same feature were merged (ngp_lon_mtsc and EOPMTSCEnabled -> MTSCEnabled); the first one found is kept.
"""
import os

RENAMED = {
  "EOPDDSCEnabled": "DDSCEnabled",
  "EOPMSLCEnabled": "MSLCEnabled",
  "EOPMTSCEnabled": "MTSCEnabled",
  "EOPPathdNudgesEnabled": "PathdNudgesEnabled",
  "EOPRCDEnabled": "RCDEnabled",
  "EOPSharedSLCOffsets": "SharedSLCOffsets",
  "EOPTLSCEnabled": "TLSCEnabled",
  "ngp_bigmodel_active": "BigModelActive",
  "ngp_bigmodel_link": "BigModelLink",
  "ngp_bigmodel_loading": "BigModelLoading",
  "ngp_dashboard_enabled": "DashboardEnabled",
  "ngp_device_audible_mode": "AlertSoundMode",
  "ngp_device_logger_delay_seconds": "LoggerStartDelay",
  "ngp_device_shutdown_minutes": "OffroadShutdownMinutes",
  "ngp_device_vehicle_list": "VehicleModelList",
  "ngp_device_vehicle_selected": "DeviceModelSelected",
  "ngp_dm_policy": "DriverActivityPolicy",
  "ngp_dpp_max_mode": "DPPMaxMode",
  "ngp_lat_alcc": "LatALCC",
  "ngp_lat_blinker_pause_mph": "BlinkerPauseLateralSpeed",
  "ngp_lat_cat": "CATEnabled",
  "ngp_lat_dlp_curves": "DLPCurvesEnabled",
  "ngp_lat_edge_guard": "RedControllerEnabled",
  "ngp_lat_lca_auto_sec": "AutoLaneChangeDelay",
  "ngp_lat_lca_gap_eval": "LCAGapEvalEnabled",
  "ngp_lat_lca_lane_width": "LCALaneWidthEnabled",
  "ngp_lat_lca_speed": "LatLCASpeed",
  "ngp_lat_pathd": "PathdLateral",
  "ngp_lat_road_edge_detection": "LatRoadEdgeDetection",
  "ngp_lat_soc": "SOCControllerEnabled",
  "ngp_lat_turn_desire_mph": "TurnDesireSpeed",
  "ngp_lon_accel_profile": "AccelerationProfile",
  "ngp_lon_adaptive_gap": "AdaptiveGapEnabled",
  "ngp_lon_brsc": "BRSCEnabled",
  "ngp_lon_cutin": "CutInSpeedEnabled",
  "ngp_lon_ddsc": "DDSCEnabled",
  "ngp_lon_dlon_curves": "DLONCurvesEnabled",
  "ngp_lon_dlon_force_stops": "DLONForceStopsEnabled",
  "ngp_lon_dlon_lane_confidence": "DLONLaneConfidenceEnabled",
  "ngp_lon_dlon_low_speed": "DLONLowSpeedEnabled",
  "ngp_lon_dlon_navigation": "DLONNavigationEnabled",
  "ngp_lon_dlon_signal": "DLONSignalEnabled",
  "ngp_lon_dlon_slow_lead": "DLONSlowLeadEnabled",
  "ngp_lon_dlon_speed_limit": "DLONSpeedLimitEnabled",
  "ngp_lon_dlon_stop_prediction": "DLONStopPredictionEnabled",
  "ngp_lon_drive_mode": "DriveMode",
  "ngp_lon_green_light": "GreenLightAlert",
  "ngp_lon_lc_lead_handoff": "LCAdjacentLeadHandoff",
  "ngp_lon_lead_departure": "LeadDepartAlert",
  "ngp_lon_mslc": "MSLCEnabled",
  "ngp_lon_mtsc": "MTSCEnabled",
  "ngp_lon_nslc": "NSLCEnabled",
  "ngp_lon_pathd": "PathdLongitudinal",
  "ngp_lon_rcd": "RCDEnabled",
  "ngp_lon_slc_offsets": "SharedSLCOffsets",
  "ngp_lon_speed_offset_kph": "SpeedLimitOffset",
  "ngp_lon_tlsc": "TLSCEnabled",
  "ngp_lon_vtsc": "VTSCEnabled",
  "ngp_map_enabled": "MapdEnabled",
  "ngp_monod_enabled": "MonoDEnabled",
  "ngp_monod_hz": "MonoDHz",
  "ngp_pathd_enabled": "PathdEnabled",
  "ngp_pathd_nudges": "PathdNudgesEnabled",
  "ngp_trip_a_start_distance": "TripAStartDistance",
  "ngp_trip_a_start_time": "TripAStartTime",
  "ngp_trip_b_start_distance": "TripBStartDistance",
  "ngp_trip_b_start_time": "TripBStartTime",
  "ngp_trip_daily_stats": "TripDailyStats",
  "ngp_trip_last_distance": "TripLastDistance",
  "ngp_trip_last_duration": "TripLastDuration",
  "ngp_trip_last_max_accel": "TripLastMaxAccel",
  "ngp_trip_last_override_free_distance": "TripLastOverrideFreeDistance",
  "ngp_trip_last_personality_time": "TripLastPersonalityTime",
  "ngp_trip_lifetime_engagement_ratio": "TripLifetimeEngagementRatio",
  "ngp_trip_total_distance": "TripTotalDistance",
  "ngp_trip_total_drives": "TripTotalDrives",
  "ngp_trip_uptime_engaged": "TripUptimeEngaged",
  "ngp_trip_uptime_onroad": "TripUptimeOnroad",
  "ngp_tripd_enabled": "TripdEnabled",
  "ngp_ui_brightness": "UIBrightness",
  "ngp_ui_hide_hud_speed_kph": "UIHideHudSpeedKph",
}


def migrate_renamed_params(param_dir: str) -> list[str]:
  """Rename stored old-name params to their new names. Returns the old names that were moved."""
  migrated = []
  for old, new in sorted(RENAMED.items()):
    src, dst = os.path.join(param_dir, old), os.path.join(param_dir, new)
    if not os.path.lexists(src):
      continue
    if os.path.lexists(dst):
      os.remove(src)
    else:
      os.replace(src, dst)
      migrated.append(old)
  return migrated

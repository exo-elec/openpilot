# NagasPilot Migration (Concise)

**Scope**: Features/controls/UI only; no car porting or gateway.

## Completed
- Params: all `np_*` (includes `np_cat_status`, `np_cat_persist`); dp_* removed.
- Processes: `np_beep_controller`, `np_trip_controller`, `np_mapd_manager`.
- Controllers: DLP, TSC (map+vision), DEM, CAT (adaptive geometry), LCA, RED.
- Telemetry: `npControlsState` exposes DLP/CAT/TSC/DEM; HUD debug toggles available.
- UI: NP panel toggles, HUD debug overlays, spinner/branding aligned to NagasPilot.

## Pending (optional)
- Enrich map curvature ingestion if more `liveMapDataSP` fields are available.
- Add richer DEM telemetry if desired.

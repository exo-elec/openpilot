# NagasPilot Integration Summary (Concise)

**Status**: Controls + UI integrated; telemetry live. No car porting or gateway work included.

## Core Features
- Controllers: DLP, TSC (vision + map with stale flag), DEM, CAT (adaptive geometry), LCA, RED.
- Telemetry: `npControlsState` publishes DLP/CAT/TSC/DEM fields; HUD debug toggles (`np_cat_debug_onroad`, `np_stack_debug_onroad`) surface live status.
- Params: all `np_*` plus `np_cat_status` (status JSON) and `np_cat_persist` (seed).
- Processes: `np_beep_controller`, `np_trip_controller`, `np_mapd_manager`.

## Key Files
- Controls: `selfdrive/controls/controlsd.py`, `selfdrive/controls/lib/longitudinal_planner.py`, `nagaspilot/selfdrive/controls/lib/np_*_controller.py`.
- Telemetry schema: `cereal/custom.capnp`.
- UI: `selfdrive/ui/qt/offroad/np_panel.*`, `selfdrive/ui/qt/onroad/hud.*`, `selfdrive/ui/ui.*`, `system/ui/spinner.py`.
- Docs: this file, `docs/nagaspilot_features_migration.md`, `docs/NEXT_STEPS.md`.

## Ready Toggles
- CAT: `np_cat_enable` (+ manual steer ratio + HUD debug).
- DLP/ALCC/RED/LCA: `np_dlp_*`, `np_alcc_*`, `np_red_enable`, `np_lca_*`.
- TSC: `np_tsc_enable` (map stale flagged).
- HUD debug: `np_cat_debug_onroad`, `np_stack_debug_onroad`.

## Notes
- Map curvature ingestion is basic; enrich if more fields are available in `liveMapDataSP`.
- DEM telemetry is minimal (health score only); extend if needed.

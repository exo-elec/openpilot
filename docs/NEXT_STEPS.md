# Next Steps (Concise)

**Status**: Core integration done. Optional refinements below.

## Optional Work
- Map data: improve curvature ingestion/staleness handling if more `liveMapDataSP` fields exist.
- DEM: expose more telemetry (confidence/scenario) if needed.
- Developer UI: add a small panel for DLP/TSC/DEM telemetry if desired.

## Verification
- Toggles: ensure `np_cat_enable`, `np_tsc_enable`, `np_dlp_enable`, `np_alcc_enable`, `np_red_enable` are set as intended.
- HUD debug: enable `np_cat_debug_onroad` or `np_stack_debug_onroad` to view live CAT/DLP/TSC/DEM status.

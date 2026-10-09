# Added-Files Audit — 685 additions (step 2.4) + binary assets (step 2.5)

## Reachability screen (added .py modules)

Method: every added non-test library module under `common/`, `selfdrive/`, `system/` checked for
importers, `process_config` module-path strings, and references from `.sh`/`.service`/udev/SConscript.
`scripts/`, `tools/`, `site_scons/` treated as manual entry points. Daemon mains verified via
`process_config.py` strings. Result: all reachable **except 13 confirmed orphans** → **D16**.

### D16 — unwired modules — RESOLVED 2026-06-11: **KEEP ALL** (user decision)

User confirmed these are intentional work-in-progress for the EOP multi-camera daemons and the
rule-based pipeline that runs parallel to the original openpilot models — not abandoned code.
They stay in tree, unwired for now. No deletions. (Original orphan-screen evidence kept below
so a future pass can re-check which have since been wired.)

| file | note |
|---|---|
| `selfdrive/controls/lib/ddsc.py` | not imported anywhere |
| `selfdrive/gridd/depth_map.py` | gridd imports costmap/tracker but not this |
| `selfdrive/gridd/traffic_light_classifier.py` | |
| `selfdrive/locationd/calibration_monitor.py` | |
| `selfdrive/monod/calibration_fusion.py` | also has D14 import-time NameError — never imported, so latent; if deleted, that D14 entry is moot |
| `selfdrive/navd/set_destination.py` | possibly intended as manual helper — wire or move to tools/ |
| `selfdrive/navd/tile_auto_manager.py` | |
| `selfdrive/pathd/long_horizon_planner.py` | |
| `selfdrive/pathd/osm_pcd_fusion.py` | |
| `selfdrive/steamd/video_utils.py` | |
| `selfdrive/stereod/depth_pipeline.py` | |
| `system/hardware/tune_udev_usb_cameras.py` | name suggests udev hook; no udev rule references it |
| `system/v4l2d/move_detector.py` | |

(Reachable near-misses, kept: `selfdrive/gridd/yolo_objdet.py` ← `scripts/validate_eop.sh`;
`selfdrive/modeld/vision/models/download_models.py` ← `models/download_models.sh`.)

Non-Python additions (capnp, .cc/.h, models, docs, scripts) are exercised via SConscript builds,
manifests, or are documentation; no orphan screen applied (build will catch dead C++).

## Binary assets (step 2.5)

109 modified files were upstream LFS pointers. sha256(EOP content) vs pointer oid:

- **102 byte-exact** upstream content → pure no-LFS materialization. **KEEP** (verdict for the whole class).
- **7 mismatches**:
  - `third_party/bootstrap/bootstrap-icons.svg` → **D17 (MED)**: 67-byte EMPTY svg stub; `selfdrive/ui/qt/util.cc::bootstrapPixmap()` loads it → all bootstrap UI icons silently blank. Restore real content (upstream LFS oid known from pointer).
  - `docs/assets/icon-star-{empty,full,half}.svg`, `icon-youtube.svg`, `three-back.svg`, `selfdrive/ui/installer/inter-ascii.ttf` → real content but from a different upstream generation (LOW). Restore alongside D17 from LFS oids for byte-parity.

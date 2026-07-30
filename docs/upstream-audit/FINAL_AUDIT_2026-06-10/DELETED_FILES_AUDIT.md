# Deleted-Files Audit — 316 deletions vs `c085b8af1` (step 2.3)

All 316 deletions grouped; every group maps to a documented EOP architecture decision.
**No restorations required.** Defect D15 (dangling imports into deleted modules) logged below.

| group (files) | justification |
|---|---|
| `tools/cabana` (74) | DELTA_AUDIT step 14 — CANape + fixed Tesla CAN protocol |
| `system/camerad` (31) | Replaced by `system/v4l2d` (RK MIPI/USB capture) |
| `third_party/linux` (23), `third_party/opencl` (11), qt5/qrcode/bootstrap (6) | Qualcomm/VENUS headers + comma-specific vendored libs; replay/loggerd rewritten to standard NV12 (verified `tools/replay/camera.cc`) |
| `selfdrive/debug/car/*` + fw/can debug (19) | opendbc/panda tooling — DELTA_AUDIT step 28 |
| `selfdrive/test/process_replay` + longitudinal_maneuvers + CI routes (14) | Depend on comma routes/car stack; `vision_meta.py` kept, verified imports OK |
| `selfdrive/ui` DM views/firehose/translations (13) | DM + comma-cloud features removed |
| `selfdrive/car` (11) | step 28 (vehicled replaces) |
| `tools/bodyteleop` (9), `system/webrtc` (7) | step 11 — steamd supersedes |
| `selfdrive/modeld` ONNX blobs + dmonitoring + tinygrad runner (9) | models/ dir + no DM + no tinygrad |
| `tools/car_porting` (8) | step 14 |
| `system/updated` (8) | No OTA self-update on EOP (offline) |
| `system/hardware` agnos/amplifier/tici-extras (8) | comma-device-specific; tici core HAL preserved |
| `system/sensord` (7) | Replaced by `imud` |
| `system/proclogd` C++ (6), `logcatd` (3), `qcomgpsd` (5) | RK platform replacements / not applicable |
| `selfdrive/monitoring` (4) | DM removed (deliberate product decision) |
| controls/locationd/selfdrived tests (7) | Tests of removed comma-route fixtures |
| `tools/webcam`, `plotjuggler`, `profiling`, misc (1–3 ea) | Superseded by v4l2d / unused on edge target |
| submodules: `panda`, `opendbc*`, `tinygrad*`, `teleoprtc*` (6) | [INFRA] heavy-submodule removal |
| `.lfsconfig`, `.github/workflows/badges.yaml` | no-LFS constraint; comma-infra CI |

## D15 — dangling imports into deleted modules (found by automated screen)

| importer | imports (deleted) | fix direction (Phase 3) |
|---|---|---|
| `selfdrive/ui/tests/test_soundd.py` | `selfdrive.ui.soundd` (moved to `selfdrive/soundd/soundd.py`) | update import path |
| `system/athena/athenad.py` | `system.camerad.snapshot` | athenad dormant BUT `system/athena` is in pytest `testpaths` → collection ImportError; remove snapshot feature import or drop athena from testpaths |
| `system/ui/feedback/feedbackd.py` | `system.micd` (moved to `system/micd/micd.py`) | dormant file; fix path or delete file (feedbackd not in process_config) |
| `system/hardware/tici/agnos.py` | `system.updated.casync.casync` | tici-only, never imported on EOP; verify pytest `system/hardware` doesn't collect it, else guard |

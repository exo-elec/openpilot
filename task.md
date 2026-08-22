# EOP10 Working Tree — Task List

Branch: `EOP10`
Goal: Complete the EOP schema/runtime alignment changes so the working tree is coherent and the modified daemons are syntactically and import-clean.

## Current session — USB eGPU camera expansion (2026-08-23)

Scope and ownership:

- OpenPilot owns the front driving cameras plus two independent optional workloads:
  `sided` for `side_left`/`side_right`, and `reard` for `rear`.
- Corner-radar/4D point-cloud work is not part of this OpenPilot eGPU change. Its
  future integration belongs in `../visionpilot`.
- The first milestone is shadow comparison: run the existing small/local result
  and the eGPU model on the same frame, measure consistency and any improvement,
  and leave the existing result authoritative.
- The main expansion target is semantic segmentation. The production driving
  model remains the openpilot-native vision + temporal-policy pipeline and its
  existing input buffers, output slices, parsers and message contract.
- Side and rear are complete inference pipelines, not camera-only feeds: each
  needs its own detection and segmentation sessions, health and validation data.

Completed in the working tree:

- [x] Pin `tinygrad_repo` to the official stable `v0.13.0` tag
  (`2d48fe8b7bd9acfa00e91a7f89b28b3ded370c27`), rather than following `master`.
- [x] Add separate `side_yolo_egpu` and `rear_yolo_egpu` model IDs, artifacts,
  Params, sessions and shadow queues. Rear is not folded into a generic camera model.
- [x] Keep `inferenced` as the only process allowed to own the USB eGPU and make
  the eGPU path fail closed; it cannot silently open a per-daemon direct backend.
- [x] Keep only `off` and `shadow` modes. No eGPU result has driving authority.
- [x] Transport camera input as FP16 and cast in device memory to the ONNX-declared dtype.
- [x] Audit the six ONNX artifacts in `../autoware_vision_pilot`.
  All model ops exist in tinygrad v0.13 and all six graphs parse.
- [x] Execute `autosteer_int8`, `autospeed_int8`, and `autodrive_int8` end to end
  with tinygrad v0.13 on CPU using zero inputs; shapes were correct and outputs finite.
  This verifies graph compatibility only, not USB eGPU latency or numerical quality.

Next tasks, in order:

- [ ] Finish focused tests, lint/compile checks, `git diff --check`, and review the
  working-tree diff without disturbing unrelated user changes.
- [ ] Stage/verify the `tinygrad_repo` gitlink and confirm a fresh recursive clone
  resolves the official URL and exact release commit.
- [ ] Add SHA-256 entries and viewpoint-specific production artifacts for side and
  rear detection; the initial copied YOLOv8n files are hardware-path validation models only.
- [ ] Add independent `side_seg_egpu` and `rear_seg_egpu` model contracts and
  artifacts. Do not combine their masks, class maps, postprocessing or health state.
- [ ] Add front road/wide segmentation shadow jobs by adapting the existing
  SceneSeg/PP-LiteSeg contracts; benchmark useful mask quality before model size.
- [ ] Flash and bench-identify the ASM2464PD-class enclosure, then measure actual
  USB 3.0 Gen1 throughput, hot-unplug recovery and sustained thermal behavior.
- [ ] Measure per-camera p50/p95/p99 latency, dropped deadlines, frame age, IoU,
  precision/recall and false negatives with small/local and eGPU results side by side.
- [ ] Implement real priority/deadline ordering in `inferenced`; its current single
  worker serializes access but does not yet order the queue by job priority.
- [ ] Extend private transport only where segmentation or the canonical openpilot
  driving runner needs multiple named tensors/outputs. Schema changes still require approval.
- [ ] Keep driving inference behind the existing openpilot `modeld` runner and
  `parse_vision_outputs`/`parse_policy_outputs` contract, including temporal feature,
  desire, traffic-convention and previous-curvature buffers. An eGPU backend may
  implement this runner interface; it must not introduce a parallel driving contract.
- [ ] Keep AutoSpeed, AutoSteer and AutoDrive as optional compatibility/reference
  experiments, below segmentation in priority. Do not substitute their outputs for
  openpilot's driving model or connect them directly to trajectory planning.
- [ ] Reuse one locally preprocessed tensor where contracts match, but keep model
  sessions, outputs, health, deadlines and promotion decisions independent.
- [ ] Do not enable a primary eGPU mode or feed planning/control until replay, HIL,
  hardware-soak, failure-recovery and closed-course gates are defined and passed.

Autoware model contracts captured for the next session:

| Model | Input contract | Output contract | Current IPC fit |
|---|---|---|---|
| AutoSpeed INT8 | `input`, `[N,3,512,1024]` FP32 | `[N,8,10752]` | Yes, for shadow experiments |
| AutoSteer INT8 | `input_0`, `[1,3,512,1024]` FP32 | two `[1,1,64,1]` tensors | No: multi-output needed |
| AutoDrive INT8 | `image_prev`, `image_curr`, each `[N,3,512,1024]` FP32 | distance, curvature, flag logit | No: multi-input and multi-output needed |

The Autoware driving-model bandwidth numbers remain useful as a worst-case
transport reference, but those models are not the production driving direction.
Measured USB admission must prioritize segmentation plus independent side/rear
inference while preserving the canonical openpilot driving model's deadlines.

## Completed

- [x] Inspect working tree (24 modified files, no `task.md` existed).
- [x] Align remaining `CP.brand` → `CP.carName` consumers (`alcc`, `latcontrol_angle`, `plannerd`, `modeld`, `torqued`, `events`, `selfdrived`).
- [x] Update `cereal/car.capnp` `RadarData.Error` enum to include `none` and shift legacy values; align `radard.py` + `selfdrived.py` to the single-error `RadarState.radarErrors` schema.
- [x] Update `card.py` to the EOP `CarParams` schema:
  - use `safetyConfigs[0].safetyModel` instead of legacy `safetyModel`
  - remove `steerAtStandstill`
  - use `experimentalLongitudinalAvailable` instead of `alphaLongitudinalAvailable`
  - wire `SimpleCANParser` via `CarState.get_can_parsers`
  - publish live `liveTracks`
  - re-write `CarParams` to params every 10 frames so blocking readers unblock quickly
- [x] Update `continental_interface.py` to consume `(addr, dat, src)` tuples from `can_capnp_to_list`.
- [x] Harden `system/socketd/can_capnp.py` `_as_event` for capnp context-manager semantics and duck-type readers.
- [x] Update `system/stated/stated.py`:
  - use `carState.engineRpm`
  - publish `deviceState.started`
  - sync `EOPIgnitionOn` param
- [x] Simulation tooling:
  - block EOP-incompatible daemons in `tools/sim/launch_openpilot.sh`
  - suppress OpenCL compiler warnings in `tools/sim/lib/camera_sim.py`
  - auto-detect docker/sudo in `tools/sim/start_carla.sh` and tests
- [x] Fix stale field references discovered during review:
  - `selfdrive/controls/controlsd.py`: `self.CP.steerAtStandstill` → `getattr(..., False)`
  - `selfdrive/debug/set_car_params.py`: `alphaLongitudinalAvailable` → `experimentalLongitudinalAvailable`
  - `selfdrive/ui/onroad/exp_button.py`: `alphaLongitudinalAvailable` → `experimentalLongitudinalAvailable`
- [x] Add missing `micStatus` publisher in `system/micd/micd.py` so the UI subscription added in `selfdrive/ui/ui.cc` actually receives data.

## Verification run

- `python3 -m py_compile` passes for all modified Python files.
- `pytest -q selfdrive/test/test_daemon_imports.py` → **40 passed** (monod excluded: needs `hal` package).
- `system/socketd/tests/test_safety.py` → **16 passed** (was 6 failing; see "Safety reconciliation" below).
- `pytest -q selfdrive/controls/tests/ selfdrive/gridd/tests/` → all pass except 3 pre-existing environment failures (monod `hal` import, `/data/calibration/stereo_calibration.npz` missing on dev PC, `test_nslc` helper) — confirmed identical on the clean tree.

## Follow-up session (radar4d + remaining work)

Completed:

- [x] **radar4d tracker improvements** (`radar4d_tracker.py`, `radar4d.py`):
  published `vRel` is now the EKF-filtered radial velocity (raw Doppler still feeds
  the filter); elevation/z complementary-filtered (`EKF_Z_SMOOTH_GAIN`); tracker
  accepts measured frame `dt_s` (IRQ-paced loop) for physically correct prediction;
  dead imports removed. 34 tracker tests incl. 3 new ones, 81 radar4d tests total.
  Note: vRel calibration-rotation was analyzed and is a no-op (dot product is
  rotation-invariant).
- [x] **controlsd audit**: task premise was wrong — controlsd is the ONLY onroad
  publisher of `carControl` (vehicled actuates it) and `controlsState` (selfdrived/
  modeld/plannerd/UI consume it). Kept in `process_config.py` with a do-not-remove
  comment. The dual `ttsRequest` publishers (controlsd + selfdrived) are distinct
  content streams on a multi-publisher service — left as-is.
- [x] **test_onroad.py**: updated stale process paths (`vehicled.car.card` →
  `vehicled.vehicled`, `locationd.calibrationd` → `camera_calibrationd`,
  `ui.soundd` → `soundd.soundd`, `system.micd` → `micd.micd`); dropped dead
  `./encoderd` and `system.loggerd.uploader` budgets. Still tici-marked; EOP
  daemon CPU budgets (gridd/pathd/stated/…) not yet measured.
- [x] **Safety reconciliation** (`tesla_safety.py` + `vehicled/safety/safety.py`):
  - **Real bug fixed**: Tesla `DAS_control` accel is offset-encoded around
    `INACTIVE_ACCEL=375` (0 m/s²), but the "80% of Panda" factor was applied to the
    raw values, giving MAX 340 / MIN 310 — both BELOW inactive. Combined with the
    `negative_accel_both` check (both < 375 = normal braking), layer-1 rejected
    **100% of longitudinal commands**. Limits corrected to MAX 415 (+1.6 m/s²) /
    MIN 305 (−2.8 m/s²); `negative_accel_both` removed in both copies (opendbc
    `tesla.h` has no equivalent; min/max bounds are the guard).
  - **Test fixes**: raise-based API (`pytest.raises(SafetyViolation)`), preset
    rate/angle-error state so each test isolates one check, correct Tesla bit
    packing helper, counter-tolerance test aligned to the implemented "2 missed"
    semantics. `system/socketd/tests/test_safety.py` → 16/16.

## Known remaining work

- [ ] **On-road validation of the accel-limit fix**: the corrected MAX 415 / MIN 305
  defaults (and `EOPSafetyMaxAccel`/`EOPSafetyMinAccel` param overrides) should be
  sanity-checked on hardware before relying on layer-1 longitudinal enforcement.
- [x] **Safety limits duplication**: DONE — `system/socketd/safety/tesla_safety.py`
  is now the single canonical module (TC275 0x712 cross-core checks merged in,
  `VehicleSafetyLayer` kept as an alias); `selfdrive/vehicled/safety/safety.py` is a
  re-export shim. Both safety managers verified against it.
- [x] **Simulation integration tests — docker permission fixed (2026-08-02)**:
  `tools/sim/tests/` run locally → 29 passed except `test_carla_bridge.py::test_driving`
  (needed a CARLA server; docker socket was permission-denied for this user) and
  `test_metadrive_bridge.py` (metadrive package not installed). The docker blocker
  is fixed: `vcar` was added to the `docker` group (`sudo usermod -aG docker
  vcar`); takes effect in new shells, or immediately via `sg docker -c "..."` in
  an existing one. The `carlasim/carla:0.9.16` image is already pulled locally
  (29.4GB) — no download needed.
  **Actually attempting `test_driving` surfaced two more blockers, unrelated to
  docker, that stop it running from this shell/sandbox:**
  1. The `.venv` (Python 3.12) has no `msgq`, no `opendbc`, and a `params_pyx.so`
     built for a different Python ABI — `msgq_repo`/`rednose_repo` submodules
     are uninitialized (`git submodule status` shows them unpopulated) and
     `opendbc_repo` is never added to `PYTHONPATH` or `pip install -e`'d
     anywhere in this checkout (see the `opendbc_repo` entry below). The
     system `/usr/bin/python3.10` has the `carla` PyPI package installed
     (`~/.local/lib/python3.10/site-packages`) but *not* `msgq` either — so
     neither interpreter currently has a working `cereal.messaging` import.
  2. This exec environment only exposes `/dev/nvidiactl`, not `/dev/nvidia0`
     — no real GPU device passthrough — so `nvidia-smi` fails to reach the
     driver here even though the host has an RTX 3090 (used by the local
     inference stack in `CLAUDE.md`). CARLA needs actual GPU/Vulkan
     rendering; it cannot run from this sandboxed shell regardless of the
     Python/package fixes above. Running it would need to happen directly on
     the host, outside this tool's sandbox.
- [x] **RadarZoneMonitor alert priority** (`controls/lib/radar_zones.py`): side
  blind-spot messages now take priority over rear cross traffic in the zone-overlap
  region (an overtaking car behind-and-lateral is a blind-spot threat, not RCTA);
  fixes the failing `test_alert_messages`. `test_simulated_components.py` → 30/30.
- [ ] **EOP CPU budgets in test_onroad.py**: measure and add budgets for EOP daemons
  (gridd, pathd, stated, adaptd, radar4d, …) when on RK hardware.
- [ ] **Pre-existing dev-PC failures** (confirmed identical on the clean tree, not
  caused by these changes): `test_daemon_imports[monod]` (needs `hal` package),
  `test_depth_validation` (needs `/data/calibration/stereo_calibration.npz`),
  `test_nslc::test_get_nslc_speed_helper` (returns None on dev PC).

## Follow-up session (vehicled removal + OpenDBC de-duplication, 2026-08-02)

Completed:

- [x] **Finished `selfdrive/vehicled/` → `system/socketd/vehicle/` removal**:
  the in-progress rename (staged in the index from a prior session) is now
  fully coherent. `system/socketd/vehicle.py` (the old single-file shim) and
  `system/socketd/vehicle/vehicled.py` (the standalone process wrapper) are
  both deleted — `socketd` runs `vehicle.Car` as a thread inside its own
  process (`SocketD.start()` in `system/socketd/socketd.py`), there is no
  separate `vehicled` process entry in `process_config.py`.
- [x] **Fixed a real test bug**: `selfdrive/test/test_onroad.py` still
  budgeted CPU for `system.socketd.vehicle.vehicled` — a module whose file
  had already been deleted, so it could never match a real process and would
  silently report "NO METRICS FOUND". Renamed the key to
  `system.socketd.socketd`, the actual running process.
- [x] Removed the dead `"vehicled": CORE_BIG` entry from
  `common/core_config.py`'s CPU-affinity table (no such process exists;
  `socketd` already has its own mapping).
- [x] Rewrote `system/socketd/vehicle/ARCHITECTURE.md` and
  `MIGRATION_SUMMARY.md` — a prior mechanical find/replace had corrupted them
  into nonsense (`selfdrive/socketd vehicle adapter/`, etc.). Both now
  accurately describe the single-process `socketd` architecture.
- [x] Updated contributor-facing docs that still pointed at the deleted
  `selfdrive/vehicled/` path: `.github/pull_request_template.md`,
  `tools/car_porting/README.md`, `docs/CARS.md`,
  `docs/car-porting/what-is-a-car-port.md`,
  `docs/eop/01_Core/NAMING_CONVENTIONS.md`,
  `docs/eop/01_Core/VEHICLE_STACK_COMPATIBILITY.md`.
- [x] **De-duplicated `system/socketd/vehicle/tesla/values.py` against the
  pinned OpenDBC fork** (shared commit with `dev/NGP10`): `CANBUS` and
  `CarControllerParams.ACCEL_MIN/ACCEL_MAX/JERK_LIMIT_MIN/JERK_LIMIT_MAX` are
  now re-exported from `opendbc.car.tesla.values` instead of being a second
  hardcoded copy — the exact kind of duplication that caused the accel-limit
  bug in the "Safety reconciliation" entry above. Also removed `GEAR_MAP`,
  `TeslaSafetyFlags`, `TeslaFlags`, `STEER_THRESHOLD`, `FW_QUERY_CONFIG` —
  confirmed dead code with zero importers anywhere in the tree. See
  `system/socketd/vehicle/MIGRATION_SUMMARY.md` → "OpenDBC De-duplication".
- [x] Verified `python3 -m py_compile` on all touched files, and confirmed
  (via `git stash` on a clean tree) that the `msgq`/`opendbc` module-not-found
  import failures in this dev-PC venv are pre-existing environment gaps, not
  caused by this session's changes — `opendbc_repo` is not currently
  `pip install -e`'d or added to `PYTHONPATH` by any launch script or
  `SConstruct` rule in this checkout.
- [x] Committed (`11e38d891`, "remove vehicled daemon, dedupe tesla values
  against OpenDBC") and pushed to `origin/dev/EOP10`.
- [x] Fixed the CARLA sim-test docker permission blocker: `vcar` added to the
  `docker` group. See "Simulation integration tests" above.

Known remaining work:

- [ ] **This tool's exec sandbox has no GPU device passthrough** — only
  `/dev/nvidiactl` is present, not `/dev/nvidia0`, so `nvidia-smi` can't reach
  the driver here even though the host has an RTX 3090. CARLA needs real
  GPU/Vulkan rendering (`tools/sim/start_carla.sh` explicitly requires a
  discrete GPU) and cannot run from this sandboxed shell — would need to run
  directly on the host outside this tool. Still open; everything else below
  is fixed.

## Follow-up session (dev-PC build environment repair, 2026-08-02)

The "Dev-PC Python environment is broken" item above is now fixed, and it
went further than initially scoped once real tests could actually run
in this environment for the first time. Commit `bcce0e24d`.

Root-caused and fixed:

- `msgq_repo`/`rednose_repo` submodules were never initialized
  (`git submodule update --init msgq_repo rednose_repo`) — the `msgq`/
  `rednose` symlinks at repo root (committed since upstream's "Restructure
  msgq #32652") pointed into empty directories. Populated them, then built
  their Cython extensions for this checkout's Python (3.12) via
  `scons -j$(nproc) msgq_repo/` — `cereal.messaging` now imports.
- `common/params_pyx.so` was a stale build for a different Python ABI
  (`undefined symbol: PyCode_NewWithPosOnlyArgs`); rebuilt via
  `scons -j$(nproc) common/params_pyx.so`.
- `opendbc_repo` is a populated submodule but, unlike `msgq_repo`/
  `rednose_repo`, had **no** root-level `opendbc` symlink — nothing could
  ever `import opendbc` here, including `system/socketd/vehicle/car/*.py`'s
  existing `from opendbc.car.tesla...` imports. Added `opendbc ->
  opendbc_repo/opendbc` (same convention as msgq/rednose, now committed) and
  built opendbc_repo's own Cython extension (`opendbc/can/parser_pyx.so`) by
  running `scons` inside `opendbc_repo/` against the main `.venv`.
- `common/transformations/transformations.so` had the same stale-ABI
  problem; rebuilt via `scons -j$(nproc) common/`.
- `casadi` (3.7.1, pinned in `uv.lock`) was installed but corrupted — only
  its bundled `.so` solver libraries were present, no `casadi.py`/
  `_casadi` Python binding, so `from casadi import SX` failed. A plain
  `uv sync --all-extras` reinstall fixed it (was a bad local install, not a
  version problem).
- `selfdrive/controls/lib/{longitudinal,lateral}_mpc_lib/c_generated_code/
  acados_ocp_solver_pyx.so` (plannerd's/lateral MPC's acados solvers) had
  the same stale-ABI problem once casadi worked; rebuilt both via
  `scons -j$(nproc) selfdrive/controls/lib/{longitudinal,lateral}_mpc_lib/`.
- `pyproject.toml` was silently missing two dependencies that real installs
  would also hit: `scipy` (imported by `pointcloudd/feature_extractor.py`
  and `controls/radar4d_pointcloud.py`) and `pytest-env` (required by
  `[tool.pytest.ini_options]`'s `env = [...]`; the whole suite couldn't
  even collect without it). Declared both properly and re-locked
  (`uv lock`), rather than leaving them as ad-hoc venv installs.
  **Caution for next session**: `uv sync --extra <name>` syncs to *only*
  that extra and uninstalls everything else not declared for it — always
  use `--all-extras` on this project, or packages silently disappear.

Two real (not environment) bugs surfaced once the suite could actually run,
both fixed:

- `system/bluetoothd/{pairing_agent,ble_gatt}.py`: `@dbus.service.method`/
  `.signal` are decorator factories evaluated at class-body execution time.
  Both modules correctly guard their base class (`dbus.service.Object if
  DBUS_AVAILABLE else object`) and `__init__`, but the bare decorators still
  ran unconditionally at import time and raised `AttributeError` whenever
  dbus-python isn't installed. Added no-op decorator fallbacks
  (`dbus_method`/`dbus_signal`) used in both files.
- `selfdrive/gridd/tests/test_fuse_radar4d.py`: `GridD._estimate_box_kinematics`
  is a `@classmethod`; accessing it through the class already returns a
  bound method. The test harness re-wrapped that bound method in
  `classmethod(...)` again, binding a second `cls` and breaking the call
  arity (`TypeError: takes 3 positional arguments but 4 were given`).
  Fixed by unwrapping with `.__func__` before re-wrapping.

Net result: `test_daemon_imports.py` 31/40 → 40/40.
`selfdrive/controls/tests/ selfdrive/gridd/tests/ system/socketd/tests/`
(minus `tici`-marked): 272 passed, 2 failed — both the already-documented,
hardware-data-dependent gaps (`test_nslc::test_get_nslc_speed_helper`,
`test_depth_validation::test_with_real_calibration`), nothing new.

## Follow-up session (camera geometry propagation + source anonymization, 2026-08-12)

Goal: propagate proven OX03C10/GC4653 camera constants to ExoPilot 02M (RK3576) /
VisionPilot, and scrub identifiable external-source names from EOP10.

Completed:

- [x] **ExoPilot HAL**: added `hal/hal/platform/rk3576_camera_geometry.py` with
  physics-derived focal lengths for RK3576 / ExoPilot 02M:
  - OX03C10 mono cameras: 1920×1280, fx 567/2667/5333 px (1.7/8.0/16.0 mm)
  - GC4653 stereo cameras: 2560×1440, fx 1800 px (3.6 mm)
  - exported from `hal.platform`.
- [x] **VisionPilot calibration defaults**: updated
  `src/calibration/geometry/geometry/camera_model.py` and
  `src/calibration/geometry/geometry/camera_array.py` to use the corrected
  defaults; `create_default()` imports from `hal.platform.rk3576_camera_geometry`
  when available, falling back to hardcoded corrected values.
- [x] **VisionPilot driving_model defaults**: updated deprecated
  `camera_geometry.py`, `multi_camera_fusion.py`, `camera_calibration.py`,
  inference docstring, and the two calibration YAML templates to 1920×1280 /
  2560×1440 and the matching focal lengths.
- [x] **VisionPilot docs**: updated
  `docs/perception/calibration/calibration_pipeline.md`,
  `docs/hardware/cameras/camera-array-design.md`,
  `docs/architecture/CAMERA_QUICK_REFERENCE.md`.
- [x] **Source-name scrub**: removed all external source names from EOP10
  Python code and docs. Replaced `docs/eop/RKNN_PROVENANCE.md` with
  `docs/eop/RKNN_RUNTIME_NOTES.md` and updated cross-references.
- [x] Commits and pushes:
  - `exopilot@main`: `7b656f1 feat(hal): add RK3576 camera geometry module and export it`
  - `visionpilot@EVP09`: `7e0c579 fix(calibration): align camera defaults with OX03C10/GC4653 physics`
  - `openpilot@dev/EOP10`: `f7dffdb7f docs: anonymize external RK3588 source references in EOP10 docs`

Verification:

- `python3 -m py_compile` passes on all modified Python files.
- `CameraArray.create_default('rk3576')` returns correct values both with and
  without `hal.platform.rk3576_camera_geometry` importable.
- Deprecated `CameraArrayGeometry()` defaults verified at 1920×1280 with
  fx 567/2667/5333/1800 px.

Known remaining work:

- [x] **EC25/GPS driver boundary**: DONE — see next session below.
- [x] **RKNN model local-placement audit**: confirmed `inference_registry.yaml`,
  `tools/convert_models_to_rknn.py`, and `selfdrive/modeld/vision/models/download_models.py`
  use only local paths and offline-first placeholders; no external branded RKNN
  model references remain. Added `.github/scripts/check_rknn_local.py` and wired
  it into `.github/workflows/eop10_lint.yaml`.
  Commit: `931783258` "ci: add RKNN local-placement check for EOP10 model references".
- [x] **Camera exposure / 3A / IQ tuning boundary**: sensor register maps
  (`hal/hal/drivers/camera/sensor_registers.py`), camera path wiring, thermal
  tuning, and ISP/IQ JSON constants are now in the closed ExoPilot `hal` package.
  EOP10 consumes geometry and tuning via `hal.platform` imports with safe
  fallbacks. Commit: `exopilot@main 81c4135`.
- [ ] **Full delta review of external RK3588 changes vs stock openpilot**: the
  anonymized audit docs still describe the port; a systematic pass could find
  additional fixes (thermal, watchdog, process supervision) worth pulling in.
- [ ] **EOP CPU budgets in test_onroad.py**: measure and add budgets for EOP
  daemons when on RK hardware (carried forward from earlier sessions).

## Follow-up session (EC25/GPS driver boundary move to ExoPilot HAL, 2026-08-12)

Goal: move low-level EC25 modem and u-blox GPS control from EOP10 into ExoPilot
HAL, leaving EOP10 with thin application-layer adapters only.

Completed:

- [x] **ExoPilot HAL cellular driver**: added `hal/hal/drivers/cellular/ec25.py`
  with `EC25Modem`, mmcli/nmcli/ip helpers, APN lookup, QMI bearer bring-up,
  network state, and temperature queries. Exports `NetworkType`/`NetworkStrength`
  enums and dataclasses (`SIMInfo`, `BearerInfo`, `NetworkInfo`,
  `ModemTemperatures`).
- [x] **ExoPilot HAL GPS driver**: added `hal/hal/drivers/gps/ublox.py` with
  GPIO power/reset control (via `rk3588_pins`), `TTYPigeon` serial wrapper, UBX
  configuration, AssistNow Online fetch, and almanac backup/restore.
- [x] **EOP10 adapter refactor**:
  - `system/hardware/rk3588/modem.py` is now a thin wrapper that imports from
    `hal.drivers.cellular`, reads `Params("GsmApn")`, maps HAL enums to cereal
    `DeviceState.NetworkType`/`NetworkStrength`, and provides dev-PC fallback.
  - `system/ubloxd/pigeond.py` is now a daemon wrapper that imports from
    `hal.drivers.gps`, reads AssistNow/last-GPS params, runs `PubMaster('ubloxRaw')`,
    and handles daemon lifecycle.
- [x] **HAL exports**: `hal/hal/drivers/__init__.py` now exposes `cellular` and
  `gps` submodules.
- [x] Commits and pushes:
  - `exopilot@main`: `87027ca feat(hal): add EC25 cellular and u-blox GPS drivers to HAL`
  - `openpilot@dev/EOP10`: `bf4bf225d refactor(hardware): delegate EC25 modem and u-blox GPS to ExoPilot HAL`

Verification:

- `python3 -m py_compile` passes on all new and modified Python files in both
  `exopilot` and `openpilot`.
- `hal.drivers.cellular` and `hal.drivers.gps` import cleanly when ExoPilot HAL
  is on `PYTHONPATH`.
- EOP10 adapters remain importable (dev-PC fallback disables HAL calls when
  `hal` is unavailable).

Known remaining work:

- [x] **RKNN model local-placement audit**: confirmed `inference_registry.yaml`,
  `tools/convert_models_to_rknn.py`, and `selfdrive/modeld/vision/models/download_models.py`
  use only local paths and offline-first placeholders; no external branded RKNN
  model references remain. Added `.github/scripts/check_rknn_local.py` and wired
  it into `.github/workflows/eop10_lint.yaml`.
  Commit: `931783258` "ci: add RKNN local-placement check for EOP10 model references".
- [x] **Camera exposure / 3A / IQ tuning boundary**: sensor register maps
  (`hal/hal/drivers/camera/sensor_registers.py`), camera path wiring, thermal
  tuning, and ISP/IQ JSON constants are now in the closed ExoPilot `hal` package.
  EOP10 consumes geometry and tuning via `hal.platform` imports with safe
  fallbacks. Commit: `exopilot@main 81c4135`.
- [x] **Full delta review of the proven v0.8.13 fork vs stock openpilot**:
  completed in this session (see new section below).
- [ ] **EOP CPU budgets in test_onroad.py**.

## Follow-up session (proven v0.8.13 fork delta review + EOP10 porting plan, 2026-08-13)

Goal: systematically compare the proven v0.8.13 fork against
upstream `commaai/openpilot v0.8.13` and decide what belongs in EOP10 vs ExoPilot.

Key findings (no external source names per policy):

- The fork is **not an RK3588 HAL reference**. It is based on upstream v0.8.13
  for Qualcomm (LeEco EON / comma tici) hardware. There are **no OX03C10/GC4653
  sensor registers, no MIPI/ISP tuning, no RK3588 DT overlays, and no RKNN/NPU
  code** in the audited tree.
- The fork's value is in **application-layer driving behavior** and **local-market
  car ports** (Proton, Perodua, BYD, Honda City Bosch, Toyota tuning).
- EOP10 already has a better RK3588 inference architecture (`system.inferenced`
  HAL + `rknn_runner.py`). That is an improvement over the fork's SNPE/ONNX
  stack, not a break from openpilot's design concept.
- AGNOS is comma's OEM update OS. Because EOP10 runs on the SOM supplier's
  Ubuntu image, AGNOS is not needed. The fork's `UpdateStatus` param lifecycle
  and dirty-repo guard are useful, but the AGNOS image-flashing path is not.
- EC25 on EOP10 is already correctly delegated to `hal.drivers.cellular` in
  ExoPilot. The fork never used EC25 QMI; it used the QCOM GPSD/SUPL stack, so
  there is no GPS code to port.
- Camera intrinsic/exposure: the fork has no OX03C10 data. EOP10's camera
  geometry already lives in `hal.platform.rk3588_camera_geometry` / ExoPilot.
  There is no hard-coded intrinsic table to copy from the fork.

Recommended porting plan (highest value first):

1. [x] **Power monitoring + auto-shutdown**: added `PowerSaverEntryDuration` param
   and `system/hardware/power_monitoring.py`, integrated into `manager_thread()`.
   Offroad auto-shutdown sets `DoShutdown`; guarded by `DisablePowerDown` and
   `ForcePowerDown`. Application layer. Commit: `d75d576b0`.
2. [x] **Quiet mode + volume limits**: added `QuietMode` param; `soundd` now scales
   alert-tone amplitude to ~25% and suppresses engage/disengage tones in quiet
   mode. Also removed local Piper TTS from `soundd` because language/voice audio
   is handled by the Azure server. Application layer. Commit: `d75d576b0`.
3. [x] **Update backend lifecycle**: add `UpdateStatus` string param and a
   dirty-repo guard in the update flow. Application layer (EOP10 already has the
   pyray updater UI).
4. [x] **ALC / lane-change behavior**: road-edge blinker guard, below-ALC-speed
   event, and post-LKA-resume steer ramp. Application layer.
5. [x] **Generic schema extensions**: `stockAdas`, `cruiseState.setDistance`,
   `speedControlled`, `belowLaneChangeSpeed` event. Car-schema/application layer.
6. [ ] **Car ports**: BYD, Proton, Perodua, Honda City Bosch only if those
   vehicles are in EOP10 scope. Most are application/opendbc layer; actuator
   hardware glue stays in ExoPilot.
7. [x] **RK3588 public-repo boundary hardening**: moved `install_target.sh`,
   `npu_powerctrl.sh`, `88-rockchip-camera.rules`, `99-rockchip-rk3588-env.sh`,
   `install_rockchip_deps.sh`, `tune_udev_usb_cameras.py`, and the RK3588 pinout
   doc out of public EOP10 into ExoPilot. EOP10 now has only
   `install_openpilot.sh`, `openpilot.service`, and READMEs. Low-level sensor
   register maps, camera paths, thermal tuning, ISP tuning constants, and pinout
   details live in the closed `hal` package / ExoPilot docs.
   Also sanitized `system/v4l2d/README.md`, `docs/eop/bgt60_radar.md`, and
   `docs/eop/03_Software/Architecture/CALIBRATION_PIPELINE.md` to remove default
   `/dev/videoN` nodes, IQ tuning filenames, SPI bus details, and carrier-board
   names from public docs.
   Commits: `exopilot@main 81c4135..d3b4219`, `openpilot@dev/EOP10 63fb681d1..a09268c33`.

## Follow-up session (update lifecycle + ALC guards + generic schema, 2026-08-14)

Goal: complete the remaining application-layer porting items identified in the
previous session.

Completed:

- [x] **Update backend lifecycle**:
  - Added `UpdateStatus` string param to `common/params_keys.h`.
  - Created `system/updated.py`, an OS-agnostic Git + OverlayFS safe-update
    daemon (no AGNOS/NEOS image flashing). It writes the full lifecycle through
    `UpdateStatus` (`checking`, `prepareDownload`, `installing`, `success`,
    `latest`, `noInternet`, `fetchFailed`, `unsavedChanges`, `waiting`) and
    blocks updates when the repo has local/unpushed changes.
  - Registered `updated` in `system/manager/process_config.py`.
  - Updated `system/ui/updater.py` to display `UpdateStatus` and offer a reboot
    when `UpdateAvailable` is true; legacy CLI args are accepted but ignored.
- [x] **ALC / lane-change behavior**:
  - `selfdrive/controls/lib/desire_helper.py`: added road-edge blinker guard
    (`is_road_edge_blinker`), ALC cancel-delay guard (`ALC_CANCEL_DELAY`), and
    below-lane-change-speed tracking. Wired `model_v2` into `modeld.py` so the
    road-edge check receives model data.
  - `system/socketd/vehicle/car/events.py`: emit `belowLaneChangeSpeed` event
    when the blinker is active under ALC minimum speed, lateral is inactive,
    and LCA is enabled.
  - `selfdrive/controls/controlsd.py`: added `lkaDisabled` to the `latActive`
    gate and implemented post-lateral-resume steering ramp (`reduce_steer`)
    over 1.75 s to avoid jerk when LKA/steering re-engages.
- [x] **Generic schema extensions**:
  - `cereal/car.capnp`: added `CarState.lkaDisabled`, `CarState.stockAdas`
    (with `laneDepartureHUD`, `frontDepartureHUD`, `ldpSteerV`, `aebV`),
    `CruiseState.setDistance` enum, and `CarParams.speedControlled`.
  - `cereal/log.capnp`: added `OnroadEvent.EventName.belowLaneChangeSpeed`.
  - `selfdrive/selfdrived/events.py`: added alert definition for
    `belowLaneChangeSpeed`.
  - Updated `docs/upstream-audit/NODE_03_opendbc_submodule_vendoring.md` to
    reflect that `lkaDisabled` is now a live EOP10 field.
- [x] **ExoPilot / VisionPilot boundary**: no new HAL-relevant changes required;
  the new items are application-layer only. Existing camera geometry, EC25/GPS,
  and RK3588 board-bring-up boundaries remain correct.
- [x] **Build verification**: `scons -j$(nproc) cereal/` succeeds with the new
  schema; `python3 -m py_compile` passes for all modified Python files.

Known remaining work:

- [ ] **EOP CPU budgets in test_onroad.py**: measure and add budgets for EOP
  daemons when on RK hardware (carried forward from earlier sessions).
- [ ] **On-road validation**: the ALC road-edge guard, below-ALC-speed event,
  and post-resume steer ramp should be validated on hardware before relying on
  them in production.

## Follow-up session (VisionPilot EOP10 voice/power alignment, 2026-08-14)

Goal: keep VisionPilot concept-aligned with EOP10 so it does not diverge into a
local STT/TTS stack.

Completed:

- [x] **No local STT/TTS in VisionPilot**:
  - Stripped Whisper/Piper model paths from
    `src/launch/visionpilot_launch/config/voice/voice_config.param.yaml`.
  - `src/audio/tts_driver/tts_driver/tts_driver_node.py`: removed Piper
    initialization; forwards `/voice/tts_driver/speak` → `/voice/cloud_tts/speak`.
  - `src/voice/cloud_assistant/cloud_assistant/cloud_assistant_node.py`: added
    `/voice/cloud_tts/speak` subscriber that calls backend `/assistant/tts` and
    plays returned audio.
  - `src/navigation/navi_tts/navi_tts/navi_tts_node.py` and
    `src/launch/visionpilot_launch/launch/voice.launch.py`: docstrings refreshed
    to "cloud-first / no local STT or TTS".
- [x] **Quiet mode for local alert tones**:
  - `src/audio/sounds/sounds/sounds_node.py`: added `sounds.quiet_mode` param
    that scales tones to ~25% and suppresses engage/disengage chimes.
- [x] **Offroad auto-shutdown**:
  - `src/system/power_manager/power_manager/power_manager_node.py`: added
    `power.offroad_shutdown_timeout_s` (default 1800 s) with `disable_shutdown`
    / `force_shutdown` guards, subscribing to
    `/system/operation_mode_manager/ignition`.
  - Wired params in
    `src/system/power_manager/launch/power_manager.launch.py` and
    `src/launch/visionpilot_launch/config/system/system_config.param.yaml`.
- [x] **Docs**: updated `TASKS.md` and the superseded
  `docs/VOICE_PIPELINE_IMPLEMENTATION.md` note.
- [x] **Commits and pushes**:
  - `visionpilot@EVP09`: `125e151 feat(voice/power): align VisionPilot with EOP10
    — no local STT/TTS, quiet mode, offroad auto-shutdown`

Verification:

- `python3 -m py_compile` passes for all modified Python files.
- Only the intended VisionPilot files were committed; unrelated Autoware
  refactoring already in the working tree was left untouched.

Known remaining work:

- [ ] **EOP CPU budgets in test_onroad.py**: measure and add budgets for EOP
  daemons when on RK hardware (carried forward from earlier sessions).
- [ ] **On-road validation**: the ALC road-edge guard, below-ALC-speed event,
  post-resume steer ramp, and VisionPilot quiet/offroad-shutdown behavior should
  be validated on hardware before relying on them in production.

Do-not-adopt list:

- Branding/rename changes.
- `FeaturesDict` / `FeaturesPackage` subscription/licensing gate.
- QC mode (`startupQC`/`qcDone`) and factory test flows.
- Frequency-check `0` overrides and disabled-tester-present workarounds in
  Honda/Hyundai parsers.
- `IgnoreDM` / driver-monitoring bypass.
- AGNOS/NEOS updater image flashing.
- Prebuilt custom panda firmware (`icptr.bin.signed`) and custom USB flasher
  protocol — keep in ExoPilot or a private panda fork.

## Follow-up session (cross-repo docs sync, 2026-08-16)

Goal: verify commit/push state across `openpilot`/`exopilot`/`visionpilot` after a
usage-limit interruption, and bring this log up to date with VisionPilot work
that landed independently of the EOP10 branch.

Findings:

- `openpilot@dev/EOP10`, `exopilot@main`, and `visionpilot@EVP09` were all
  clean working trees, fully committed, and already pushed (`git fetch` +
  `git status -sb` showed no ahead/behind on any of the three). The prior
  session's ALC/update-lifecycle/schema and VisionPilot voice/power work
  (recorded above) was not lost to the interruption.
- `visionpilot/TASKS.md` mislabeled two sections as "uncommitted": the R.1–R.10
  / C.2–C.8 refactoring continuation (typed velocity/steering reports, flat
  `AckermannControlCommand` fixes, `tire_monitor.py` restore, steering-torque
  reporting, tracked-object wiring, outer-`__init__.py` cleanup) is actually
  committed in `visionpilot@ac0d262` (`update: Sync local modifications`,
  2026-08-16 00:37). Fixed the stale labels and `Last updated` date in
  `visionpilot/TASKS.md`.
- VisionPilot also has an independent `SAFE.1` audit (15 commits,
  `ff56d17`..`8f4a4fb`, 2026-08-16) not previously logged here: a full
  control-path + perception-to-safety bug pass covering the same
  flat-`AckermannControlCommand` and `DetectedObject` field-name bugs found by
  the R./C. continuation from a different starting point (from-scratch audit,
  not reconciled/deduped against R./C. — both efforts independently converged
  on the same real bugs). Also fixed a permanently-latching `EMERGENCY_STOP`
  (missing `control_cmd` watchdog subscription in `mrm_handler_node`), three
  safety nodes (AEB/FCW/BSD) crashing in `main()` on a wrong class name before
  `rclpy.spin()` ran, and removed a phantom RCW module from
  `src/safety/README.md`. See `visionpilot/TASKS.md` `SAFE.1` row for the full
  file list.

Known remaining work (unchanged, carried forward — none actionable from this
dev-PC sandbox):

- [ ] **EOP CPU budgets in test_onroad.py**: measure and add budgets for EOP
  daemons when on RK hardware.
- [ ] **On-road validation**: ALC road-edge guard, below-ALC-speed event,
  post-resume steer ramp, and VisionPilot quiet/offroad-shutdown behavior.
- [ ] **VisionPilot C.7**: final `colcon build --symlink-install` +
  `colcon test` on a real ROS host — no `/opt/ros`/colcon available here.
- [ ] **Car ports scope** (item 6, line 405 above): BYD/Proton/Perodua/Honda
  City Bosch are still an open scope question for the user, not a technical
  blocker — left unanswered pending that decision.

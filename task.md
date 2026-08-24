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
- [x] Extend `InferencedStatus` with capability-discovery fields (`availableBackends`,
  `availableModels`, `backendHealth`) and publish them from HAL in `inferenced`.
- [x] Add `InferenceClient` capability methods (`get_available_backends`,
  `get_available_models`, `can_run_model`, `wait_for_backend`) with IPC + direct-HAL fallback.
- [x] Gate side/rear eGPU detection and front road/wide eGPU segmentation shadow jobs
  on advertised `EGPU` backend availability.
- [x] Add independent `side_seg_egpu`, `rear_seg_egpu`, `front_road_seg_egpu`, and
  `front_wide_seg_egpu` model contracts, registry entries, Params, and SHA-256 placeholders.
- [x] Add `EgpuSegmentationShadowRunner` with preprocessing, postprocessing, and IoU
  comparison against the authoritative mask, plus unit tests.
- [x] Add side/rear camera-alive health tracking (`CameraHealthTracker`) and publish
  `camera_disconnected` faults through the existing `sideStatus`/`rearStatus` fields.
- [x] Fix `system/inferenced/tests/test_daemon_execution.py` so its `cereal` mock is
  scoped to test execution and no longer poisons other test modules in the same process.

Next tasks, in order:

- [x] Stage/verify the `tinygrad_repo` gitlink and confirm a fresh recursive clone
  resolves the official URL and exact release commit.
- [ ] Add SHA-256 entries and viewpoint-specific production artifacts for side and
  rear detection; the initial copied YOLOv8n files are hardware-path validation models only.
- [x] Add independent `side_seg_egpu` and `rear_seg_egpu` model contracts and
  artifacts. Do not combine their masks, class maps, postprocessing or health state.
- [x] Add front road/wide segmentation shadow jobs by adapting the existing
  SceneSeg/PP-LiteSeg contracts; benchmark useful mask quality before model size.
- [ ] Flash and bench-identify the ASM2464PD-class enclosure, then measure actual
  USB 3.0 Gen1 throughput, hot-unplug recovery and sustained thermal behavior.
- [ ] Measure per-camera p50/p95/p99 latency, dropped deadlines, frame age, IoU,
  precision/recall and false negatives with small/local and eGPU results side by side.
- [x] Implement real priority/deadline ordering in `inferenced`; its current single
  worker serializes access but does not yet order the queue by job priority.
- [ ] Extend private transport only where segmentation or the canonical openpilot
  driving runner needs multiple named tensors/outputs. Schema changes still require approval.
- [ ] Keep driving inference behind the existing openpilot `modeld` runner and
  `parse_vision_outputs`/`parse_policy_outputs` contract, including temporal feature,
  desire, traffic-convention and previous-curvature buffers. An eGPU backend may
  implement this runner interface; it must not introduce a parallel driving contract.
- [x] Audit latest upstream openpilot Chestnut behavior at master commit
  `084747c75d2cbd23af65ab7a9e770bbd7b98bac9` and document its build, firmware,
  runtime, telemetry and one-way fallback patterns in
  `docs/eop/05_Features/CHESTNUT_EGPU_ADOPTION.md`.
- [x] Refactor driving execution behind one openpilot-compatible runner contract
  before enabling eGPU driving. Preserve modeld's temporal state and parsed outputs.
  - Added `selfdrive/modeld/runners/driving_runner.py` with `DrivingRunner` ABC,
    `DrivingModelSpec`, and `DrivingRunnerResult`.
  - Added `selfdrive/modeld/runners/rknn_driving_runner.py` implementing the local
    fallback path with vision + policy inference and ONNX/RKNN desire-name normalization.
  - Added `selfdrive/modeld/runners/factory.py` with `create_driving_runner()`:
    `use_chestnut=True` returns the monolithic `ChestnutDrivingRunner` (currently
    fail-closed), default returns the split RKNN runner (bukapilot KA2 architecture).
  - Wired `selfdrive/modeld/modeld.py::ModelState` to use the runner factory; temporal
    state, preprocessing and `Parser` ownership remain in modeld.
  - Added `selfdrive/modeld/tests/test_driving_runner.py` covering load, infer,
    failure, backend injection and factory selection.
- [x] Keep a local RKNN driving runner loaded, warmed and temporally current whenever
  an external driving model is active.
  - `modeld.main()` now loads the external runner first (when enabled), then always
    constructs and keeps the RKNN fallback runner warm.
  - The active runner is swapped to RKNN on the first external failure; temporal state
    (desire, feature history) remains in `ModelState` so the fallback resumes cleanly.
- [x] On eGPU exception, timeout, non-finite output, hot-unplug or stale model stream:
  discard the failed frame, switch once to RKNN, soft-disable if engaged, and prohibit
  onroad eGPU retry until the next offroad/ignition restart.
  - Mirrored upstream Chestnut behavior from `openpilot/selfdrive/modeld/modeld.py`
    (load big model in a bounded background thread, keep small model warm, catch
    exceptions in the main loop, set `UsbGpuActive` False and switch runner).
  - Added `ChestnutDrivingEnabled`, `ChestnutDrivingLoading`, `ChestnutDrivingActive`
    Params mirroring upstream's `UsbGpuLoading`/`UsbGpuActive` contract. The names
    diverge from upstream deliberately: EOP's `inferenced` owns the USB GPU device,
    so the state machine is named after the Chestnut model class, not the transport.
  - `ModelState.run()` raises on external-runner inference failure and on non-finite
    outputs; `modeld.main()` catches this, sets the active Param to
    False, calls `model.set_runner(rknn_runner)`, resets `run_count`, and continues.
  - Added `ChestnutDrivingRunner`, a monolithic (`big_driving_supercombo`
    tinygrad JIT) runner that fails closed so the failover path is exercised
    before the real compiled artifact and private multi-tensor transport are ready.
  - On-road retry is prohibited because the active Param is only cleared at the next
    offroad/ignition restart (`params.remove(ChestnutDrivingActive)` at startup).
  - Soft-disable if engaged is delegated to selfdrived via the `ChestnutDrivingActive`
    Param, matching upstream's `bigModelFailed` event pattern.
- [ ] Port upstream's compiled tinygrad JIT artifact identity and deterministic
  compile/replay checks. Dynamic `OnnxRunner` remains shadow-only.
- [ ] Verify whether stable tinygrad v0.13.0 can compile/run the Chestnut path.
  Upstream currently pins commit `138fb4a783d82f4e877ad2fe3692aaf8d1de2e46`,
  948 commits after v0.13.0; do not move EOP off a stable tag without approval.
- [x] Audit `../bukapilot` driving models. Two generations exist:
  - Old monolithic supercombo (v10.0.5 era, `origin/harmeet`): one nine-input
    `supercombo.rknn` with a single 6,504-float output; RK3588-only binary.
  - Current KA2 (RK3588) branch `byd_sng_ka2`: **split** `driving_vision.rknn` +
    `driving_policy.rknn` (default, `USE_RKNN=1`), plus `dmonitoring_model.rknn`
    as a separate non-driving model; `USBGPU=1` switches to tinygrad/AMD with
    the big driving models. EOP's RKNN path follows the KA2 split architecture;
    the eGPU path follows upstream Chestnut's monolithic big model.
- [ ] Materialize and verify Bukapilot's exact source ONNX
  (`d21daa542227ecc5972da45df4e26f018ba113c0461f270e367d57e3ad89221a`,
  51,461,700 bytes) from its LFS history.
  Note (2026-08-23): this is distinct from — and still open after — the KA2
  `driving_vision.onnx`/`driving_policy.onnx` pair (46,271,942 / 13,926,324
  bytes) copied and hash-verified into `models/onnx/` this session; see the
  follow-up session below.
- [ ] Package separate Bukapilot fallback conversions for RK3588 and RK3576.
  Record source/converter/toolkit/calibration/target/output hashes; never load the
  RK3588 binary on RK3576.
- [ ] First run the exact Bukapilot ONNX through tinygrad on eGPU and compare it to
  local RKNN on identical prepared tensors (`egpu_parity`). This validates backend
  and failover mechanics without changing the model graph.
- [ ] Then compile the official upstream Chestnut big model for `egpu_big`:
  `big_driving_supercombo.onnx` LFS SHA-256
  `a501760a9d1d5fef0eab2b8c5d122d06124fc26dc8e0782e0aa94b82a208f0ff`
  (1,757,355,221 bytes at the audited commit).
- [ ] Treat upstream-big → Bukapilot-RKNN as a model-generation transition, not
  numerical fallback parity. Require parsed-contract/replay validation and always
  soft-disable if that transition is triggered while engaged.
- [ ] Design offroad-only, versioned ASM firmware flashing and recovery separately.
  Do not modify RK3588 BSP configuration without approval.
- [ ] Keep AutoSpeed, AutoSteer and AutoDrive as optional compatibility/reference
  experiments, below segmentation in priority. Do not substitute their outputs for
  openpilot's driving model or connect them directly to trajectory planning.
- [ ] Reuse one locally preprocessed tensor where contracts match, but keep model
  sessions, outputs, health, deadlines and promotion decisions independent.
- [ ] Do not enable a primary eGPU mode or feed planning/control until replay, HIL,
  hardware-soak, failure-recovery and closed-course gates are defined and passed.

Deferred to hardware-only / cross-repo follow-up:

- [ ] Flash and bench-identify the ASM2464PD USB enclosure; measure sustained
  throughput, thermal behavior and hot-unplug recovery.
- [ ] Cross-repo HAL-level accelerator/service discovery for Hailo, AXCL and eGPU
  in `../exopilot` so OpenPilot sees a single capability surface regardless of SoC.
- [ ] Extend the device-health concept to front road/wide cameras, WiFi/BLE modems,
  and other peripherals without introducing a new daemon in this pass.
- [ ] On-road validation of side/rear camera detection, eGPU segmentation shadow
  quality, and camera-disconnect fault handling.

Verification run (dev-PC):

- `python3 -m py_compile` passes for all modified Python files.
- `pytest -q -n0 system/inferenced/tests/ selfdrive/sided/tests/ selfdrive/gridd/tests/ selfdrive/controls/tests/ selfdrive/modeld/tests/test_driving_runner.py selfdrive/modeld/tests/test_modeld_failover.py`
  → **235 passed**, 7 skipped, **1 failed** — `test_nslc::test_get_nslc_speed_helper` is a
  documented pre-existing dev-PC environment gap (returns None on dev PC), not a regression.
  `test_depth_validation::test_with_real_calibration` now skips cleanly when the hardware
  calibration file is absent.
- `selfdrive/modeld/test_modeld_integration.py` continues to pass.
- `ruff check` passes on the new/changed modeld runner and test files.
- `git diff --check` is clean.

## Cross-repo boundary audit (ExoPilot HAL ↔ OpenPilot ↔ VisionPilot ↔ HumRobot/ExoRobot)

Goal: confirm that capability discovery, accelerator detection, and camera-health
concepts are owned by the right layer and are consistent across the pilot stacks.

### What belongs where

| Layer | Responsibility | Current state |
|---|---|---|
| **ExoPilot HAL** (`../exopilot/hal/`) | BSP bring-up only: board identity, pin maps, camera geometry, thermal constants, init scripts, NPU tuning tables. | No accelerator-query or camera-alive APIs. Correct boundary. |
| **OpenPilot `system/inferenced`** | Owns runtime accelerator detection, scheduling, health, and IPC publication. | `InferencedStatus` now exposes `availableBackends`, `availableModels`, `backendHealth`. Backends probe RKNN/ACL/RGA/MPP/Hailo/DX-M1/eGPU/USB. |
| **OpenPilot daemons** | Consume `inferencedStatus` / `inferenceJobResult`. | `sided`/`reard`/`gridd`/`monod` gate bonus models on `EGPU`; `CameraHealthTracker` publishes camera-disconnect faults via existing status fields. |
| **VisionPilot** (`../visionpilot/`) | ROS 2 stack with its own `src/system/inference/` HAL and `/system/inference/query_backends` service. | Has `BackendStatus` per-backend list + JSON health, but no aggregate `availableBackends`/`availableModels` message matching openpilot. eGPU detection identical. No side/rear/front segmentation contracts yet. No camera frame-age watchdog. |
| **HumRobot/ExoRobot** (`../robot/humrobot`, `../robot/exorobot`) | Humanoid/robot stacks with the same tiered-accelerator concept. | Already implements workload-class routing: safety → RKNN, camera → Hailo/DEEPX, voice/policy → AXCL/Hailo-10H. eGPU is presence-only/additive. |

### Findings

1. **Accelerator detection should stay in OpenPilot `system/inferenced`, not move to ExoPilot HAL.**
   ExoPilot HAL has no detection APIs and should remain BSP-only. OpenPilot's backends already probe the right devices (`/dev/hailo0`, `lspci -d 1ff4:`, USB VID/PID for eGPU, etc.).

2. **One cross-boundary compute import exists and should be reviewed:**
   `system/inferenced/arm_acl.py:131` imports `from hal.drivers.radar import dsp_gpu_kernel`. This is a radar DSP kernel reaching into ExoPilot HAL. Consider exposing this kernel through `inferenced` as an ACL operation instead, so the HAL does not leak into the inference scheduler.

3. **VisionPilot naming does not match OpenPilot `BackendType.name`.**
   - OpenPilot: `NPU`, `ACL`, `RGA`, `MPP`, `HAILO_8`, `ONNX`, `DX_M1`, `EGPU`.
   - VisionPilot: `npu_rockchip`, `cpu_acl`, `dmu_rga`, `vpu_mpp`, `npu_hailo_8`, `egpu`, etc.
   Any future bridge between the two repos should carry a canonical mapping; do not change either enum into the other.

4. **VisionPilot needs an aggregate capability-discovery message to stay aligned.**
   OpenPilot publishes a single `InferencedStatus` with capability lists and JSON health. VisionPilot publishes per-backend `BackendStatus` and a separate JSON health topic. Add an aggregate `InferenceStatus.msg` (or extend `BackendStatus.msg`) with `available_backends`, `available_models`, and `backend_health` fields so both stacks expose the same contract.

5. **eGPU segmentation contracts exist only in OpenPilot.**
   `side_seg_egpu`, `rear_seg_egpu`, `front_road_seg_egpu`, `front_wide_seg_egpu` are in OpenPilot's `MODEL_REGISTRY`. VisionPilot and HumRobot have no equivalent entries. If those stacks will run eGPU segmentation, add matching registry entries; otherwise document the omission.

6. **Camera-alive health is inconsistent across stacks.**
   - OpenPilot: new `CameraHealthTracker` in `selfdrive/sided/camera_health.py` with frame-timeout fault reporting.
   - VisionPilot: camera node publishes quality scores but no frame-age watchdog; `health_monitor` fault tree can react to diagnostics but has no camera stall input.
   - HumRobot/ExoRobot: rely on V4L2 open and udev rules; no runtime frame-age monitor found.
   Recommendation: adopt the same frame-timeout watchdog pattern in VisionPilot/HumRobot and feed it into each stack's health/fault path.

7. **HumRobot already implements the "minimum model on attached accelerator" concept via workload classes.**
   `SAFETY_INFERENCE` is pinned to RKNN; optional cards run `CAMERA_INFERENCE` and `VOICE_INFERENCE`. This matches the EOP10 principle that RKNN local driving is authoritative and extra accelerators only run additive/shadow workloads.

### Boundary recommendations

- Keep ExoPilot HAL BSP-only; do not add `has_hailo` / `has_egpu` APIs there.
- Keep OpenPilot `system/inferenced` as the single source of truth for accelerator capability and health on EOP10 hardware.
- Add a canonical OpenPilot ↔ VisionPilot backend-name mapping when building a bridge; do not rename either stack's enums.
- Add an aggregate capability message in VisionPilot mirroring `InferencedStatus`.
- Add side/rear/front segmentation model contracts in VisionPilot only if that stack will actually run them.
- Add camera frame-age watchdogs in VisionPilot and HumRobot/ExoRobot; reuse the `CameraHealthTracker` pattern where ROS/cereal constraints allow.
- [x] Reviewed `system/inferenced/arm_acl.py` HAL cross-import. The `radar_cfar`
  ACL operation was unused (corner ESP32_RADAR nodes handle CFAR onboard) and has
  been removed, eliminating the `from hal.drivers.radar import dsp_gpu_kernel` leak.
  The remaining VisionPilot/HumRobot alignment items (aggregate capability message,
  camera frame-age watchdogs, segmentation registry entries) are deferred to their
  respective repo follow-ups.

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

## Follow-up session (bukapilot KA2 proven driving-model adoption, 2026-08-23)

Goal: replace EOP's placeholder driving_vision/driving_policy model entries
with bukapilot's proven KA2 (RK3588) pair, and port the input-handling methods
that pair depends on, since EOP's own metadata pkls were found to already
match bukapilot KA2 exactly (same input/output shapes, same output slices).

- [x] Compared bukapilot KA2's stack (`driving_rknn.py`, `modeld.py`) against
  EOP's split RKNN/ONNX driving path. Confirmed: identical metadata contract;
  fp16 casting already ported (`rockchip_npu.py`'s `_FP16_MODELS`); NHWC
  layout, big_img affine mitigation, and the blip guard were not yet ported.
- [x] Copied `driving_vision.rknn`/`driving_policy.rknn`/`.onnx` pair from a
  local `../bukapilot` checkout into `models/rknn/`/`models/onnx/`; hashes
  verified exactly against bukapilot's git-LFS objects. Recorded in
  `models/MODEL_MANIFEST.md` with sizes, output shapes, and the coupled input
  method. Removed the stale dragonpilot `pre-build`-branch fallback from
  `download_models.sh`'s dev-pc ONNX section — an unverified, possibly
  different-generation export that would have silently defeated the new
  hash-verification if `BUKAPILOT_DIR` were absent. Updated
  `docs/eop/DEV_PC_GUIDE.md` to match.
- [x] Fixed a real input-slot-ordering bug: `modeld.py`'s split path builds
  `policy_inputs` as `{traffic_convention, features_buffer, desire}`, but the
  compiled RKNN policy graph's slot order (from the metadata pkl) is
  `{desire, traffic_convention, features_buffer}` — `rockchip_npu.py`'s
  `infer()` feeds RKNN positionally in dict-insertion order, so this was
  silently swapping which tensor landed in which policy input slot. Fixed by
  adding `RKNNDrivingRunner._ordered()` (`rknn_driving_runner.py`), which
  reorders any inputs dict to `spec.input_shapes`' key order (from metadata)
  before dispatch, in both `run_vision` and `run_policy` — the runner is the
  only layer that knows both the model contract and the raw dict a caller
  handed it, so it's the natural fix point regardless of what order modeld.py
  happens to construct the dict in. The ONNX `desire`→`desire_pulse` rename
  now happens after reordering (order-preserving), so it can't drop the input.
- [x] Ported bukapilot's NHWC vision layout + big_img affine mitigation into
  `rockchip_npu.py` (`_NHWC_VISION_MODELS`, scoped to `driving_vision` only,
  same env var names as bukapilot: `RKNN_PY_VISION_LAYOUT`,
  `RKNN_ENFORCE_VISION_NCHW`, `RKNN_NHWC_BIGIMG_AFFINE_ENABLE/SCALE/BIAS`).
  Design choice: this lives in the HAL backend, not the runner or modeld.py —
  it's a hardware-execution concern (how the compiled RKNN graph expects its
  tensors laid out), matching the existing `_FP16_MODELS` precedent and
  bukapilot's own layering (their `driving_rknn.py`, the HAL-equivalent, owns
  it too). `RKNNDrivingRunner` stays responsible only for input *contract*
  concerns (naming/ordering); `inferenced` stays the sole owner of hardware
  execution quirks — kept centralized, not scattered across the driving path.
  Did not port bukapilot's explicit-format/`inputs_pass_through` RKNNLite
  retry logic (defensive workaround for buggy RKNNLite builds) — out of scope
  for this pass.
- [x] Ported the blip guard (`_apply_blip_guard`/`_y_at_distance_from_plan` in
  `modeld.py`'s `ModelState`) — suppresses one-frame "straight" plan blips
  that follow a sustained same-side curve, gated by `RKNN_BLIP_GUARD` (default
  on). Applied only to the split RKNN/ONNX path, not the Chestnut monolithic
  path (bukapilot has no monolithic path; the eGPU big model is a different
  generation and CLAUDE.md requires it preserve upstream's own modelV2
  semantics rather than inherit RKNN-specific mitigations).
- [x] Did not port bukapilot's stage-capture debug feature (`RKNN_STAGE_CAPTURE_*`,
  `.npz` frame dumps) — a debugging tool, not part of the proven inference path.
- [x] Added tests: `system/inferenced/tests/test_rockchip_npu.py` (fp16 cast,
  NHWC layout swap + enforce-NCHW override, big_img affine + clipping +
  disable, other RKNN models unaffected), `selfdrive/modeld/tests/test_driving_runner.py`
  (new `test_run_{vision,policy}_reorders_inputs_to_slot_order`, updated the
  ONNX-rename test to assert order is preserved through the rename),
  `selfdrive/modeld/tests/test_blip_guard.py` (interpolation edge cases,
  suppress-after-sustained-curve, no-guard-below-context-window,
  no-guard-on-mixed-side-curve).
- [x] Fixed a second real bug this model-file addition exposed:
  `modeld.py`'s `_resolve_model()` claims to prefer `.rknn` on ARM / `.onnx`
  on dev PC (`exts` tuple, docstring), but its search loop checked the
  `rknn/` subdir before `onnx/` *unconditionally*, only consulting `exts` for
  a subdir-less fallback that's unreachable once both subdirs exist. Before
  this session, `models/rknn/driving_vision.rknn` didn't exist on a fresh
  checkout, so the bug was masked (fell through to `onnx/`); now that both
  `models/rknn/` and `models/onnx/` hold real bukapilot KA2 files,
  `VISION_MODEL_PATH`/`POLICY_MODEL_PATH` resolved to `.rknn` even on this
  x86 dev PC — verified directly (`is_arm=False` but path was `.rknn`). If
  the dev-PC path selects the ONNX backend (`EOP_BACKEND=onnx`, or once
  `onnxruntime` inference actually succeeds instead of falling back to mock),
  `RKNNDrivingRunner.load()` would hand it a `.rknn` binary it can't parse.
  Fixed by making the subdir/bare-name lookup respect `exts`' platform order
  for every base, not just as an unreachable last resort. Added
  `selfdrive/modeld/tests/test_resolve_model.py` (prefers onnx on dev-pc /
  rknn on arm when both present, falls back to whichever exists, returns
  None when neither does) — verified both platform branches directly.
- [x] Noted: `selfdrive/modeld/tests/` is silently excluded from
  directory-based pytest collection repo-wide by root `conftest.py`'s
  `collect_ignore_glob = ["selfdrive/modeld/*.py", ...]` — `fnmatch`'s `*`
  matches across `/`, so `"selfdrive/modeld/*.py"` also matches
  `selfdrive/modeld/tests/test_*.py`. Pre-existing, not introduced this
  session (the file list in the verification run below, and the prior
  session's, both list modeld test files individually for this reason).
  Running `pytest selfdrive/modeld/tests/` as a bare directory silently
  collects 0 items with no error — always pass modeld test files explicitly.

Verification run (dev-PC):

- `ruff check` passes on all new/changed files.
- `pytest -q system/inferenced/tests/ selfdrive/test/test_inference_pipeline.py
  selfdrive/modeld/tests/test_driving_runner.py selfdrive/modeld/tests/test_modeld_failover.py
  selfdrive/modeld/tests/test_blip_guard.py selfdrive/modeld/tests/test_resolve_model.py`
  → **119 passed**, 9 skipped, **1 failed** — `test_inference_backend_selection`
  is the same pre-existing, environment-dependent failure noted earlier in
  this file (this host's `inferenced` falls back to mock RKNN instead of
  ONNX on dev PC); unrelated to this session's changes.
- `./test.sh` (focused gate) is green.

## Follow-up session (Chestnut ONNX + Autoware reference models, 2026-08-23)

Goal: finish porting the remaining `models/` placeholders and pre-position
Chestnut's raw ONNX for the future gated compile step (task.md's existing
`egpu_big` roadmap item), per user request.

- [x] Audited `../autoware_universe` (the real, unrelated ROS 2 Autoware.Universe
  stack — no models there) and confirmed `../autoware_vision_pilot`'s six ONNX
  artifacts were already audited in a prior session (see the earlier "Audit the
  six ONNX artifacts" line above). Found that `../visionpilot/models/onnx/` and
  `models/hef/` already carry hash-stable copies of five of our manifest's
  long-standing placeholder entries (`egolanes_lite_int8`, `scene3d_lite_int8`,
  `sceneseg_lite_int8`, `autosteer_full_int8`, `autospeed_full_int8`) plus two
  of the three placeholder `hef/` files (`yolov8n.hef`, `scrfd_2.5g.hef`) —
  same Autoware lineage. Copied and hash-verified all seven into `models/`,
  recorded real sha256/size/shape in `MODEL_MANIFEST.md`. The third `hef/`
  placeholder, `whisper_base_5s_encoder.hef`, was initially copied in too but
  then deliberately removed on user correction: whisper's target tier is
  `VOICE_INFERENCE` (destined for `axmodel/` once an AX-M1 backend + real
  `.axmodel` build exist), not `hef/`'s `CAMERA_INFERENCE` purpose — a
  mismatched-tier `.hef` build isn't worth storing just because it's the only
  compiled artifact available today; left unfetched with a note in both
  `MODEL_MANIFEST.md` and `download_models.sh` instead. Confirmed via
  `onnx.load()` that `autosteer_full_int8`'s
  real contract (`input (1,6,80,160)` → two `(61,)` outputs) differs from what
  an earlier session recorded for `../autoware_vision_pilot`'s plain
  `autosteer_int8` (`input_0 [1,3,512,1024]`) — different variant/pipeline,
  not re-verified against planning; recorded as reference/compatibility only
  per `CLAUDE.md`, not connected to anything.
  `domainseg_full_int8.onnx`/`dmonitoring_model*.onnx` also exist in
  `../visionpilot` but have no EOP consumer — deliberately not pulled in.
- [x] Found and stored the upstream Chestnut big model
  (`big_driving_supercombo.onnx`) from two independent local sibling checkouts
  — `../ext_gpu/openpilot-upstream` (literal `commaai/openpilot@master`,
  `b7c333cf3fee117779515c9ebfd7b2beb164fa81`) and `../sunnypilot@master`
  (`bf74ce544738189693dbd07266a46e63465710c1`) — both hash-identical
  (`10926f2c...`, 1,753,235,978 bytes). This **differs** from the hash/size
  task.md previously recorded from an earlier audit (`a501760a9d1...`,
  1,757,355,221 bytes); sunnypilot's commit touching the same file is titled
  "Be Right Here Model 🏃 (big)" (2026-08-01), so this reads as comma shipping
  a genuine model update since that audit, not corruption — flagged clearly
  in `MODEL_MANIFEST.md` rather than silently overwritten. Confirmed with the
  user this is storage-only, pre-positioning for the still-gated `egpu_big`
  compile step — `ChestnutDrivingRunner`/`factory.py` remains untouched and
  deliberately fail-closed; nothing reads this file today.
- [x] Reorganized `models/` after back-and-forth with the user on naming axis
  (brand vs. format — settled on **format**, consistently, not mixed):
  `rknn/` (.rknn), `hef/` (.hef), `onnx/` (.onnx — dev-PC RKNN substitute,
  Chestnut's big model, and reference-only models all share this since they're
  all `.onnx`), plus two new reserved-but-empty folders for backends that have
  no models yet: `axmodel/` (.axmodel, future AX-M1/AXCL) and `dxnn/` (.dxnn,
  `BackendType.DX_M1`/DeepX — the backend class already existed
  pre-session with `MODEL_ZOO_SUBDIR = 'deepx'`, fixed to `'dxnn'` for
  consistency with the format-naming rule; `hailo_hef.py`'s
  `MODEL_ZOO_SUBDIR` and `inferenced.py`'s `yolo_side` registry path were
  touched and reverted back to `'hef'`/`models/hef/...` in the same session —
  net no-op there). Per user clarification: `axmodel/`'s intended workload
  tier is `VOICE_INFERENCE` (local LLM + whisper voice encoder — nothing
  stored there yet), not camera inference; `hef/`+`dxnn/` are
  `CAMERA_INFERENCE` (side/rear/etc.), interchangeable with each other, both
  cheaper/earlier in the pipeline than Chestnut's `onnx/`-tier big model.
  Documented the full scheme (table + priority ordering) in
  `MODEL_MANIFEST.md`'s new "Folder naming" section and `models/README.md`'s
  directory tree.
- [x] Updated `download_models.sh`: added hash-verified `copy_verified` blocks
  for the five Autoware ONNX files (sourced from `../visionpilot`,
  `VISIONPILOT_DIR` override) and for `big_driving_supercombo.onnx` (primary
  `../ext_gpu/openpilot-upstream`, fallback `../sunnypilot`, both
  env-overridable) with an explicit runtime warning that a hash mismatch
  there needs re-verification against a fresh upstream checkout rather than
  being trusted as an error. Left a NOTE comment in place of a whisper block
  explaining why it's deliberately not fetched.

Verification run (dev-PC):

- `bash -n models/download_models.sh`, `ruff check` on touched Python files,
  `git diff --check` all clean.
- `pytest -q system/inferenced/tests/ selfdrive/test/test_inference_pipeline.py
  selfdrive/modeld/tests/test_driving_runner.py selfdrive/modeld/tests/test_modeld_failover.py
  selfdrive/modeld/tests/test_blip_guard.py selfdrive/modeld/tests/test_resolve_model.py`
  → **119 passed**, 9 skipped, same 1 pre-existing unrelated failure as above.
- `./test.sh` is green.
- `models/` is now 1.9G on disk (dev-PC only, gitignored — includes the 1.7GB
  Chestnut ONNX); nothing under `models/` is git-tracked except the four
  top-level meta files (`.gitignore`, `README.md`, `MODEL_MANIFEST.md`,
  `download_models.sh`), unchanged from before this session.

Pruned on further user direction (same session): `onnx/` now stores only
Chestnut's big model — removed `driving_vision.onnx`/`driving_policy.onnx`
(this dev PC doesn't run the driving model via ONNX Runtime) and the five
Autoware reference models (still available from `../visionpilot`/
`../bukapilot` if needed again, see git history for hashes). Removed
`hef/scrfd_2.5g.hef` — no driver-facing camera on this hardware, and
`driverd`'s face-DMS pipeline it existed for is VisionPilot-only (verified:
no `driverd`, `AttentionTracker`, or `facePoseState` anywhere in this repo).
Deleted the now-stale "Face DMS pipeline" section from `models/README.md`
(it was documenting a VisionPilot feature that was never implemented here).
Researched official upstream sources for the two reserved backends and
recorded them in `MODEL_MANIFEST.md`/`models/README.md`/`download_models.sh`:
DeepX's `dxnn/` → [github.com/DEEPX-AI/dx-modelzoo](https://github.com/DEEPX-AI/dx-modelzoo)
(354 pre-compiled models); Axera's `axmodel/` → [github.com/AXERA-TECH](https://github.com/AXERA-TECH),
specifically [ax-llm](https://github.com/AXERA-TECH/ax-llm) for the intended
`VOICE_INFERENCE`/local-LLM use — no models fetched yet (no backend code for
either exists), left as pointers for a future session. Re-verified
`_resolve_model()` and the full test suite after the prune — falls through
correctly to `rknn/driving_vision.rknn` on this x86 dev PC now that no `.onnx`
substitute exists; same 119 passed/9 skipped/1 pre-existing-unrelated-failure
result, `./test.sh` green.

## Follow-up session (tinygrad pin alignment, workspace re-establishment, 2026-08-24)

- [x] Bumped `tinygrad_repo` to `8611fe22a` (v0.13.0+882, matching real
  upstream `commaai/openpilot@master`'s own pin) to investigate real Chestnut
  support, then reverted back to plain `v0.13.0` on user direction — Chestnut
  isn't in any official openpilot release tag yet (`v0.11.1`, 2026-05-29,
  predates it by ~2 months; only exists on unreleased `master`), and the
  fail-closed `ChestnutDrivingRunner` stub doesn't need the newer pin for
  anything reachable today. Both `dev/EOP10` and `dev/NGP10` now track the
  identical `v0.13.0` commit deliberately, for consistency.
- [x] Session was interrupted (usage limit) and resumed in a **fresh clone**
  (confirmed via `git reflog` showing only the clone event) — not the same
  working directory as the interrupted session. `origin/dev/EOP10` had moved
  to a new "WIP" tip (`32a791ce7c`) from a commit made outside this session
  (`d436ce20`, "unify EGPU naming and add dual firmware detection for
  ASM2464PD and Chestnut", author `EXO-ELEC`) — reviewed it carefully before
  building on it (see `docs/eop/05_Features/CHESTNUT_EGPU_ADOPTION.md`'s
  "Implementation status (2026-08-23)" section for the full review and two
  regressions found/fixed: a deleted `CarVin` params_keys.h entry, and three
  submodule pins accidentally cross-contaminated from `dev/NGP10`). Fixed in
  `ae37ac0de4`.
- [x] Fixed the repo's `origin` remote from HTTPS to SSH
  (`git@github.com:exo-elec/openpilot.git`) — SSH auth was already confirmed
  working (`ssh -T git@github.com` succeeds as `exo-elec`) while the fresh
  clone had defaulted to HTTPS; this is very likely what was causing GitHub
  Desktop's intermittent branch-switch errors too. `gh` CLI's stored token is
  expired and needs an interactive browser re-auth (`gh auth login`) that
  can't be completed non-interactively — flagged for the user, not blocking
  since git push/pull only need SSH.

## Follow-up session (eGPU scope reconciliation + bandwidth confirmation, 2026-08-24)

Goal: answer "what else can use eGPU idle capacity" honestly, after an
earlier draft answer (local voice/LLM) turned out to conflict with an
existing decision — investigate before recommending, don't build on an
unverified assumption.

- [x] Found and surfaced a real doc tension before acting on it: the
  `axmodel/` folder's `VOICE_INFERENCE` (local LLM + whisper) tier, tagged
  2026-08-23 in `MODEL_MANIFEST.md`, looked like it contradicted
  `docs/eop/03_Software/Daemons/Enhanced/VOICE_PIPELINE.md`'s deliberate,
  cross-repo "no local STT/TTS" decision from 2026-08-14 (which was pushed
  as a real VisionPilot commit, `visionpilot@125e151`, specifically to keep
  VisionPilot aligned with EOP10). User clarified: AX-M1/AXCL is a separate
  PCIe-attached NPU with its own on-device DRAM, sharing neither the eGPU's
  USB link nor the RK3588 driving NPU — so the "no NPU/GPU contention" reason
  behind the cloud-voice decision doesn't block `axmodel/`'s tier. Documented
  the reconciliation directly in `MODEL_MANIFEST.md` (Folder naming section)
  so a future session doesn't re-flag this as a conflict, and noted that
  actually building local voice on AX-M1 would still need its own explicit
  `VOICE_PIPELINE.md` Safety Design update (the other three reasons —
  attack surface, auditability, centralized updates — are independent of
  the hardware-contention argument).
- [x] Checked `../autoware_vision_pilot/VisionPilot` for eGPU-workload
  candidates beyond the three already-documented AutoDrive/AutoSpeed/
  AutoSteer ONNX pairs — found none. Its `safety_guardian` module
  (`fusion/{lateral,longitudinal}_fusion`, `planning/{lateral,longitudinal}_
  planning`) confirmed the whole stack is itself a redundant advisory
  safety-guardian layer over those three models' outputs, not a source of
  new production workloads; it also runs on TensorRT (Nvidia-desktop
  oriented), reinforcing its existing "compatibility reference only" status.
  Recorded in `EGPU_CAMERA_SHADOW.md`'s Autoware section.
- [x] Code-verified (not just budget-estimated) that a Chestnut/Egpu driving
  runner needs both road and wide camera uploads: `selfdrive/modeld/
  modeld.py` opens a second `VisionIpcClient` for `VISION_STREAM_WIDE_ROAD`
  whenever both it and `VISION_STREAM_ROAD` are available
  (`use_extra_client`), and `CHESTNUT_EGPU_ADOPTION.md`'s "must preserve
  those inputs" rule binds any Egpu runner to the same contract — so the
  ≥126 MB/s road+wide figure in `EGPU_3D_RECONSTRUCTION_BANDWIDTH.md` is a
  hard requirement, not a conservative estimate. Recorded in that doc.
- [x] Answered "can all 5 cameras (road/wide/side_left/side_right/rear) run
  through the eGPU together": worst-case combined load is ~224 MB/s, under
  the ~300–350 MB/s realistic Gen1 ceiling but with little margin — and more
  fundamentally gated by the still-missing priority/deadline scheduler
  (`EGPU_CAMERA_SHADOW.md` already flagged this as a prerequisite before
  this session; added the concrete arithmetic backing that statement).

No code changes — this session was documentation reconciliation and
bandwidth/architecture confirmation only; nothing was found broken.

# EOP10 Commit-by-Commit Corrections Audit

**Branch:** `EOP10`  
**Base:** `c085b8af1` (commaai/openpilot — "feedbackd: remove lkas toggle")  
**Audited:** 2026-05-24  
**Auditor:** Claude Sonnet 4.6 + Jinshi Chen

---

## Summary of Findings

| Commit | Hash | Status | Issues |
|--------|------|--------|--------|
| [INFRA] Remove heavy submodules | `1f35f3e56` | ✅ Clean | — |
| [INFRA] Convert third_party to submodules | `ba8e8edf6` | ✅ Clean | — |
| [BUILD] Root build system | `6ee0c15ae` | ❌ **Fix needed** | 13 dev-machine files leaked in |
| [CEREAL] Messaging schema | `a3a6eab44` | ✅ Acceptable | Minor service reordering only |
| [COMMON] Core utilities | `aeb31efa9` | ⚠️ Watch | `perf_monitor.py` justified; MCAP gone |
| [THIRD_PARTY] Drop legacy headers | `70b1b7c5c` | ✅ Clean | — |
| [MODELS] Model binaries | `bd02d3daa` | ⚠️ Deferred | Blobs still in git (user-deferred) |
| [SYSTEM] Rework daemons | `93374f4c7` | ✅ Justified | `system/webrtc/` deleted — steamd is the replacement |
| [SELFDRIVE/ASSETS] Asset bundle | `0e33b2959` | ✅ Clean | — |
| [SELFDRIVE] Daemons/controls/UI | `9e5b84ed2` | ⚠️ Watch | controls/ changes need functional review |
| [TOOLS] Developer tooling | `d11239cc8` | ✅ Justified | cabana/longitudinal_maneuvers/car_porting deleted — CANape + fixed Tesla protocol |
| [DOCS] Architecture docs | `d718c4b11` | ✅ Clean | — |

---

## Commit 1 — `1f35f3e56` [INFRA] Remove heavy submodules

**Files changed:** 7 (`.gitmodules`, `.lfsconfig`, `opendbc`, `opendbc_repo`, `panda`, `teleoprtc`, `teleoprtc_repo`)

### Findings

| Change | Status | Note |
|--------|--------|------|
| Remove `panda` submodule | ✅ Keep | EOP uses socketd CAN gateway; pandad is disabled in manager |
| Remove `opendbc`/`opendbc_repo` | ✅ Keep | vehicled replaces opendbc for EOP vehicle interface |
| Remove `teleoprtc`/`teleoprtc_repo` | ✅ Keep | steamd replaces WebRTC teleop |
| Remove `.lfsconfig` | ✅ Keep | No LFS; assets and models are in-tree or via download_models.sh |
| `.gitmodules` cleanup | ✅ Clean | Upstream entries removed, EOP entries retained correctly |

### Action Required
None.

---

## Commit 2 — `ba8e8edf6` [INFRA] Convert third_party to submodules

**Files changed:** 28 (+49,787 / −29)

### Findings

| Change | Status | Note |
|--------|--------|------|
| Submodule: `arm_compute` (v53.0.0) | ✅ Keep | ARM Compute Library for NEON CV on RK3588 |
| Submodule: `clblast` | ✅ Keep | OpenCL BLAS for stereod GPU kernels |
| Submodule: `rockchip_mpp`, `rockchip_rga` | ✅ Keep | Rockchip video/graphics HW acceleration |
| Submodule: `rknpu2` | ✅ Keep | RKNN NPU runtime |
| Submodule: `hailort` (v5.3.0) | ✅ Keep | Hailo NPU runtime for monod |
| Submodule: `python-udsoncan`, `python-can-isotp` | ✅ Keep | Used by obd2d; no pip equivalent with same API |
| Submodule: `pygnssutils` | ✅ Keep | Used by ubloxd |
| Submodule: `carla` (update=none, shallow) | ✅ Keep | Simulation only; never checked out |
| Submodule: `valhalla` (3.6.3) | ✅ Keep | Offline routing engine for navd |
| `third_party/valhalla.json.template` | ✅ Keep | Valhalla config template |
| `third_party/SConscript_hailo`, `SConscript_rockchip` | ✅ Keep | EOP hardware build rules |
| `third_party/rkaiq/` IQ files | ✅ Keep | Rockchip ISP tuning files for GC4653/OX03C10 |

### Action Required
None. All submodule additions are justified for EOP hardware.

---

## Commit 3 — `6ee0c15ae` [BUILD] Root build system + IDE/repo meta

**Files changed:** 27 (+2,728 / −482)

### ❌ PROBLEM: Personal development machine files committed

The following 13 files are from the developer's **RTX3090 local AI inference stack**, not from openpilot or EOP. They describe the developer's personal coding environment and should **not** be in a product fork.

| File | Why it's wrong |
|------|---------------|
| `.claude/settings.json` | Personal Claude Code config (local API paths, tool permissions) |
| `ARCHITECTURE.md` | RTX3090 AI stack architecture — "GPU 0 (GTX TITAN X, 12GB)…" |
| `SYSTEM_CONFIG.md` | RTX3090 hardware spec — "i5-13600K, 128GB DDR5…" |
| `Makefile.local-first` | RTX3090 make targets for local LLM services |
| `TASKS.md` | Stale AI-assisted task tracking doc |
| `VISIONPILOT_CROSSCHECK.md` | 29k-byte VisionPilot analysis dump — stale |
| `bin/gpu-health-check.sh` | RTX3090 GPU health script |
| `bin/init-local-first.sh` | Starts local LLM services on dev machine |
| `bin/local-first` | LLM routing wrapper |
| `bin/start_coder_gpu1.sh` | Starts Qwen3-Coder on RTX3090 |
| `bin/start_reasoner_cpu.sh` | Starts DeepSeek-R1 on CPU |
| `bin/start_router_gpu0.sh` | Starts Llama router on TITAN X |
| `bin/start_vllm.sh` | vLLM start script |

### ⚠️ ACCEPTABLE: Build system changes

| Change | Status | Note |
|--------|--------|------|
| `SConstruct` (+136/−123) | ⚠️ Review | Mix of cosmetic (spacing/comments) and functional (RK arch detection) changes. Cosmetic churn adds audit surface but functional changes are needed. |
| `Jenkinsfile` (+11/−1) | ✅ Clean | Additive only: added `rk3588` stage; upstream stages preserved |
| `pyproject.toml` | ✅ Keep | EOP Python deps |
| `uv.lock` | ✅ Keep | Lock file |
| `.gitattributes` | ✅ Keep | Removes LFS filter rules (correct for no-LFS policy) |
| `.gitignore` | ✅ Keep | Adds EOP build output ignores |
| `README.md` | ✅ Keep | EOP fork banner |
| `CLAUDE.md` | ✅ Keep | Project AI assistant context (useful for contributors) |
| `RELEASES.md` | ✅ Keep | Upstream release notes (not RTX3090 content) |
| `launch_openpilot.sh` | ✅ Keep | EOP launch script |
| `site_scons/valhalla_build.py` | ✅ Keep | Valhalla offline build rules |
| `.github/` workflows | ✅ Keep | CI workflow updates |

### Action Required

**Delete from repo:**
```bash
git rm -r .claude/settings.json ARCHITECTURE.md SYSTEM_CONFIG.md TASKS.md \
  VISIONPILOT_CROSSCHECK.md Makefile.local-first bin/
```

---

## Commit 4 — `a3a6eab44` [CEREAL] Messaging schema + submodule pins

**Files changed:** 10 (+2,825 / −67)

### Findings

| Change | Status | Note |
|--------|--------|------|
| `cereal/custom.capnp` — 18 `CustomReserved` stubs (not 80, already trimmed) | ✅ Acceptable | Phase 1 reverts already cleaned the original 80-stub excess down to 18 |
| `cereal/log.capnp` — 7 new fault events (`stereoFault`, `inferenceFault`, etc.) | ✅ Keep | Tied to EOP daemons |
| `cereal/log.capnp` — `DeviceType`: `rk3588 @8`, `rk3576 @9` added | ✅ Keep | Additive; upstream types preserved |
| `cereal/log.capnp` — `OnroadEvent`: `userBookmark @95`, `excessiveActuation @96`, `audioFeedback @97` | ✅ Keep | Restored correctly in Phase 1 reverts |
| `cereal/log.capnp` — `FrameData.ImageSensor`: `gc4653 @4` | ✅ Keep | New camera sensor |
| `cereal/log.capnp` — `DeviceState` external storage fields | ✅ Keep | RK board external storage |
| `cereal/log.capnp` — New structs (CameraObject, stereo, grid, BEV, voice, BSD, OBD, SPP) | ✅ Keep | Core EOP feature messages |
| `cereal/services.py` — 78 new services added | ✅ Keep | Matches new EOP daemons |
| `cereal/services.py` — Upstream services preserved in "compat" block | ✅ Keep | `pandaStates`, `navInstruction`, `navRoute`, `userBookmark`, etc. all present |
| `cereal/services.py` — `customReservedRawData0/1/2` removed | ✅ Acceptable | No EOP producer; safe to drop |
| `cereal/services.py` — Driver monitoring services removed from main dict | ⚠️ Watch | `driverEncodeData`, `livestreamDriverEncodeData/Idx` removed; acceptable since monitoring not used on EOP, but they're in the compat block so upstream log replay works |
| `tinygrad`/`tinygrad_repo` pointers removed | ✅ Keep | Tinygrad not used on RK (RKNN runner instead) |
| `msgq_repo`, `rednose_repo` pointer updates | ✅ Keep | Upstream-sync bumps |

### Action Required
None. Cereal schema is in good shape after Phase 1 reverts.

---

## Commit 5 — `aeb31efa9` [COMMON] Core utilities (GPU, params, vehicle config)

**Files changed:** 17 (+1,950 / −184)

### Findings

| Change | Status | Note |
|--------|--------|------|
| `common/gpu_utils.py` (new) | ✅ Keep | RK GPU/NPU capability detection — hardware-required |
| `common/vehicle_config.py` (new) | ✅ Keep | EOP vehicle configuration abstraction |
| `common/params_keys.h` (+271 keys) | ✅ Keep | New param keys for EOP features (voiced, coordinationd, etc.) |
| `common/transformations/camera.py` (+43 net) | ✅ Keep | GC4653/OX03C10 stereo camera transform entries |
| `common/realtime.py` (+36 net) | ✅ Keep | RK-specific scheduling additions |
| `common/util.{cc,h}` (+11 each) | ✅ Keep | Small utility additions |
| `common/perf_monitor.py` (473 lines) | ✅ Keep | Active importer: `selfdrive/stereod/stereod.py:29` imports `PerformanceMonitor`, `LatencyTimer` |
| MCAP stack (`mcap_foundation.py`, etc.) | ✅ Already removed | Cleaned in Phase 1; `mcapd.py` uses pip `mcap` package directly |

### Action Required
None.

---

## Commit 6 — `70b1b7c5c` [THIRD_PARTY] Drop legacy comma-specific headers and libs

**Files changed:** 39 (+1 / −15,280)

### Findings

| Change | Status | Note |
|--------|--------|------|
| Delete `third_party/linux/include/` (Qualcomm MSM headers) | ✅ Keep | Snapdragon platform only; confirmed no RK daemon includes them |
| Delete `third_party/opencl/include/CL/` | ✅ Keep | RK uses MPP/RGA instead of Adreno OpenCL |
| Delete `third_party/qt5/larch64/bin/` (`lrelease`, `lupdate`) | ✅ Keep | comma AGNOS platform-specific binaries |
| Delete `third_party/bootstrap/` | ✅ Keep | Old comma installer UI, unused |
| Delete `third_party/qrcode/QrCode.cc`, `QrCode.hpp` | ✅ Keep | Not used in EOP UI |
| Submodule gitlink updates (arm_compute, clblast, etc.) | ✅ Keep | EOP submodule pins |

### Action Required
None.

---

## Commit 7 — `bd02d3daa` [MODELS] Model binaries + download scripts

**Files changed:** 10 (+1,132 / −1)

### Findings

| Change | Status | Note |
|--------|--------|------|
| `models/download_models.sh` | ✅ Keep | Install-time model fetcher |
| `models/MODEL_MANIFEST.md` | ✅ Keep | Model version + checksum reference |
| `models/README.md` | ✅ Keep | Usage docs |
| `selfdrive/modeld/models/driving_vision.onnx` (16 MB) | ⚠️ Deferred | Binary blob in git; user decision: "skip for now — leave blobs in git" |
| `selfdrive/modeld/models/driving_policy.onnx` (8 MB) | ⚠️ Deferred | Same |
| `selfdrive/modeld/models/big_driving_vision.onnx` | ⚠️ Deferred | Same |
| `selfdrive/modeld/models/big_driving_policy.onnx` | ⚠️ Deferred | Same |
| `scripts/validate_params_api.py`, `scripts/verify_setup.py` | ✅ Keep | EOP setup validation |

### Action Required
**Deferred** by user — model blobs stay in git for now. When ready:
1. Remove blobs via `git rm --cached selfdrive/modeld/models/*.onnx`
2. Add URLs + SHA256 to `models/MODEL_MANIFEST.md`
3. Update `models/download_models.sh` to fetch them

---

## Commit 8 — `93374f4c7` [SYSTEM] Rework daemons for RK3576/RK3588

**Files changed:** 230 (+19,787 / −12,283)

### ✅ JUSTIFIED: system/webrtc/ deleted

`system/webrtc/` (`__init__.py`, `device/audio.py`, `device/video.py`, `schema.py`, `tests/`, `webrtcd.py`) deleted.

**Reason:** steamd is the EOP teleop/streaming daemon. It fully replaces webrtcd — not just disabling it but superseding the entire design (UDP streaming, VR headset, no WebRTC signalling overhead). Keeping the upstream code as a disabled dead-code path adds audit surface with no benefit. The deletion is intentional and correct.

### ✅ Acceptable changes

| Area | Status | Note |
|------|--------|------|
| `system/v4l2d/` (new, 32 files) | ✅ Keep | V4L2 camera capture daemon for RK sensors |
| `system/socketd/` (new, 9 files) | ✅ Keep | CAN bridge with safety (replaces panda HW) |
| `system/inferenced/` (new, 11 files) | ✅ Keep | NPU inference scheduler |
| `system/bluetoothd/` (new, 11 files) | ✅ Keep | BT SPP for phone pairing |
| `system/hardware/rk3588/`, `rk3576/` (new) | ✅ Keep | RK HAL — additive alongside existing pc/tici |
| `system/hardware/pc/`, `system/hardware/tici/` | ✅ Present | Upstream hardware support retained |
| `system/athena/` | ✅ Present | `athenad.py` present; disabled in manager |
| `system/wdgd/`, `stated/`, `spkd/`, `micd/`, etc. | ✅ Keep | New EOP daemons |
| `system/mcapd/` | ✅ Keep | MCAP logger using standard pip package |
| `system/sensord/` | ✅ Keep | Extended for RK IMU drivers (additive) |
| `system/thermald/` | ✅ Keep | RK thermal zone support |
| `system/manager/process_config.py` | ✅ Keep | EOP daemon entries added |

### Action Required
None. Deletion is justified — steamd supersedes webrtcd.

---

## Commit 9 — `0e33b2959` [SELFDRIVE/ASSETS] UI icon/font/training asset bundle

**Files changed:** 221 (+444 / −112)

### Findings

| Change | Status | Note |
|--------|--------|------|
| 200+ asset files with `Bin 130 → XXXX bytes` pattern | ✅ Keep | "130 bytes" = LFS pointer size; now stored inline (correct for no-LFS policy) |
| New EOP assets (enhancedopenpilot.svg, spinner_eop.png, eop_qr.png, nav icons) | ✅ Keep | EOP branding and navigation UI |
| Deleted: `driver_face.png`, `prompt_distracted.wav`, `prep-svg.sh` | ✅ Keep | Driver monitoring assets; not used on EOP (no driver camera) |

### Action Required
None. Asset bundle is clean.

---

## Commit 10 — `9e5b84ed2` [SELFDRIVE] Daemons, controls, UI logic

**Files changed:** 400 (+62,124 / −4,799)

### Findings

| Area | Status | Note |
|------|--------|------|
| New EOP daemons (coordinationd, gridd, inferenced, mapd, monod, navd, obd2d, pathd, pointcloudd, reard, recordd, sided, soundd, steamd, stereod, surfaced, tripd, vehicled) | ✅ Keep | Core EOP feature set |
| `selfdrive/controls/lib/` — EOP feature modules (aeb, alcc, bsd, cat, mslc, mtsc, etc.) | ✅ Keep | Extended control capabilities |
| `selfdrive/modeld/runners/` — RKNN runner + platform | ✅ Keep | RK NPU inference path |
| `selfdrive/vehicled/` | ✅ Keep | Vehicle interface (Tesla CAN DBC + framework) |
| `selfdrive/monitoring/` — entire directory deleted | ✅ Keep delete | No driver camera on EOP; dmonitoringd not applicable |
| `selfdrive/modeld/dmonitoringmodeld.py` deleted | ✅ Keep delete | Same rationale |
| `selfdrive/modeld/models/dmonitoring_model.*` deleted | ✅ Keep delete | Same rationale |
| `selfdrive/modeld/runners/tinygrad_helpers.py` deleted | ✅ Keep delete | Tinygrad not used on RK |
| `selfdrive/ui/qt/onroad/driver_monitoring.{cc,h}` deleted | ✅ Keep delete | No driver monitoring UI |
| `selfdrive/debug/car/` — CAN debug scripts deleted | ✅ Keep delete | comma-cloud specific tools; EOP uses socketd instead |
| `selfdrive/controls/` — 28 files changed | ⚠️ **Step 15 pending** | Functional safety concern; each change needs individual review. Do not merge controls/ into main without explicit sign-off. |
| `selfdrive/ui/` — 81 files | ✅ Mostly additive | New cards (assist_card, nav_card, BEV widget, EOP panel, safety panel) are net-new; existing layouts trimmed to remove driver monitoring refs |

### Action Required
**Step 15 (gated):** Audit `selfdrive/controls/` 28-file diff individually. Each modified upstream file should be checked: is the change additive (new EOP feature) or a rewrite of safety-critical upstream logic? This requires human review and is not automated here.

---

## Commit 11 — `d11239cc8` [TOOLS] Developer tooling for EOP edge platform

**Files changed:** 150 (+6,745 / −14,169)

### ✅ JUSTIFIED: Upstream dev tools deleted

| Tool | Status | Reason |
|------|--------|--------|
| `tools/cabana/` | ✅ Deleted (fully) | EOP uses **CANape** for CAN analysis; Tesla CAN protocol is fixed and known — generic DBC message browser is not used |
| `tools/longitudinal_maneuvers/` | ✅ Deleted | comma-cloud longitudinal test harness; EOP longitudinal tuning follows different workflow |
| `tools/car_porting/` | ✅ Deleted | EOP targets fixed Tesla CAN protocol; generic fingerprinting and car-porting helpers are not relevant |

### ✅ Acceptable deletions

| Deletion | Status | Note |
|----------|--------|------|
| `tools/bodyteleop/` | ✅ Keep delete | WebRTC teleop replaced by steamd |
| `tools/profiling/snapdragon/` | ✅ Keep delete | Snapdragon-specific profiler |
| `tools/webcam/` | ✅ Keep delete | comma USB camera simulator |
| `tools/replay/can_replay.py` | ✅ Keep delete | comma cloud replay |
| `tools/car_porting/examples/` (6 Jupyter notebooks) | ✅ Deleted | Not relevant to fixed Tesla protocol |

### ✅ New additions

| Addition | Status | Note |
|----------|--------|------|
| `tools/convert_models_to_rknn.py` | ✅ Keep | ONNX → RKNN pipeline |
| `tools/calibration/camera_calibrator.py` | ✅ Keep | Multi-camera calibration |
| `tools/factory_calibration/calibrate_stereo.py` | ✅ Keep | Factory calibration harness |
| `tools/foxglove/` (3 files) | ✅ Keep | Foxglove bridge for EOP messages |
| `tools/rk3576_sdk/` (5 files) | ✅ Keep | RK3576 BSP toolchain |
| `tools/sim/` additions | ✅ Keep | Carla + workstation simulation |

### Action Required
None. All tool deletions are justified for the EOP workflow.

---

## Commit 12 — `d718c4b11` [DOCS] EOP architecture documentation and upstream audit

**Files changed:** 63 (+19,586 / −15)

### Findings

| Change | Status | Note |
|--------|--------|------|
| `docs/eop/**` (~50 files) | ✅ Keep | Legitimate architecture docs for EOP subsystems |
| `docs/upstream-audit/DELTA_AUDIT.md` | ⚠️ Stale | Contains old commit hashes from pre-Phase-2; step progress table needs updating |
| `docs/visionpilot/**` (3 files, ~1,500 lines) | ✅ Keep | VisionPilot comparison docs |
| `docs/voice/VOICE_PIPELINE_CLEAN_ARCHITECTURE.md` | ✅ Keep | Voice pipeline spec |
| `docs/integration/**` | ✅ Keep | RK integration notes |
| `docs/ARCHITECTURE_GPU_PARALLEL.md`, `DRIVER_CALLS.md`, `INFERENCED_ARCHITECTURE.md`, `SCHEMA.md`, `WORKSPACE.md` | ✅ Keep | Architecture references |
| `docs/assets/` SVG updates | ✅ Keep | Same upstream content, now inline (no-LFS) |

### Action Required
Update `docs/upstream-audit/DELTA_AUDIT.md` with:
1. Current commit hashes (Phase 2 restructure replaced old ones)
2. Step progress table reflecting completed work

---

## Correction Execution Plan

### Phase A — Apply corrections (in order)

**A1. Remove dev-machine files from [BUILD]** ✅ done — `a688630af`
```bash
git rm -r .claude/settings.json ARCHITECTURE.md SYSTEM_CONFIG.md \
  TASKS.md VISIONPILOT_CROSSCHECK.md Makefile.local-first bin/
```

**A2. system/webrtc/** — ✅ No action — deletion justified (steamd supersedes webrtcd)

**A3. tools/cabana etc.** — ✅ No action — deletion justified (CANape + fixed Tesla protocol)

**A4. Update audit docs** ✅ done — this file + DELTA_AUDIT.md

### Phase B — Squash A1 into [BUILD] (optional, for clean history)

Once the branch is approved, an interactive rebase can fold A1 into [BUILD],
eliminating the separate `[AUDIT-REVERT]` commit.

### Deferred (not blocking)

| Item | Step | Owner |
|------|------|-------|
| Model blobs out of git | Step 4 | Deferred by user |
| `selfdrive/controls/` 28-file audit | Step 15 | Human review required |
| SConstruct cosmetic cleanup | — | Low priority |
| `common/perf_monitor.py` replace with upstream equivalent | Step 10 | Medium priority |

---

## Step Progress (Updated)

| Step | Description | Status |
|------|-------------|--------|
| 0 | Audit written | ✅ done |
| 1 | Delete meta-docs (`docs/migration/`, stale reports) | ✅ done |
| 2 | Delete meta-tests (`tests/commit_verification/`) | ✅ done |
| 3 | Revert cereal schema churn (OnroadEvent IDs, DeviceType, services) | ✅ done (Phase 1) |
| 4 | Remove model blobs from git | ⏭ deferred (user) |
| 5 | `.vscode/launch.json` unchanged; AGENTS.md absent | ✅ n/a |
| 6 | `.gitmodules` cosmetic reformat reverted | ✅ done (Phase 1) |
| 7 | Jenkinsfile — upstream stages kept, rk3588 added | ✅ done |
| 8 | catch2, json11, kaitai, qrcode restored to upstream vendored | ✅ done (Phase 1) |
| 9 | acados, libyuv, raylib evaluated; restored to vendored | ✅ done (Phase 1) |
| 10 | MCAP stack: trimmed (4 files gone); perf_monitor.py kept (active importer) | ✅ done / justified |
| 11 | `system/webrtc/` deletion | ✅ justified — steamd supersedes webrtcd completely |
| 12 | Restore `selfdrive/pandad/` (disable in manager) | ✅ done |
| 13 | Restore `selfdrive/car/` framework | ✅ done |
| 14 | `tools/cabana/longitudinal_maneuvers/car_porting` deletion | ✅ justified — CANape + fixed Tesla protocol |
| 15 | Audit `selfdrive/controls/` 28-file diff | ⏭ gated (human review) |
| — | Remove dev-machine files from [BUILD] | ✅ done — `a688630af` |

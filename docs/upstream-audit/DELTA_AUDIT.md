# EOP10 Delta-from-Upstream Audit

**Goal:** Identify unnecessary divergence from `commaai/openpilot@c085b8af1` so we can revert what isn't required for the RK3576/RK3588 edge ADAS target. Less diff → smaller audit surface.

| 2026-08-23 working tree | **Optional USB eGPU shadow overlay.** Pins official tinygrad `v0.13.0`; adds `inferenced`-owned, fail-closed, independent `side_yolo_egpu` and `rear_yolo_egpu` shadow paths. Existing Hailo/local results remain authoritative and no public cereal schema changes are made. Segmentation is the main planned expansion, with distinct front/side/rear sessions. Production driving inference remains behind openpilot `modeld`'s runner, temporal-state, parser and `modelV2` contracts; the audited Autoware driving graphs are compatibility references only. Corner radar remains outside this OpenPilot path. | `.gitmodules`, `tinygrad_repo`, `pyproject.toml`, `SConstruct`, `launch_openpilot.sh`, `common/params_keys.h`, `system/inferenced/{client,compute,egpu,inferenced}.py`, `selfdrive/{sided,reard}/`, `models/`, `docs/eop/` |
| 2026-08-23 upstream audit | **Latest openpilot Chestnut pattern adopted as the future driving-eGPU design, with RK3588 adaptation.** Audited `commaai/openpilot@084747c75d`: exact flashed-device identity, offroad bounded firmware update, compiled/chunked tinygrad JIT, bounded big-model load+warmup, continuously available small model, normal `modelV2` contract, and one-way failure to small with engaged soft-disable/no onroad retry. EOP keeps the invariant of one device owner but uses `inferenced` so segmentation/side/rear can share it; `modeld` retains driving state/parsing and RKNN is the preloaded on-device fallback. Upstream pins an untagged tinygrad commit 948 commits after EOP's stable v0.13.0, so code promotion is blocked on stable-tag compatibility testing. No schema or BSP changes in this documentation pass. | `docs/eop/05_Features/CHESTNUT_EGPU_ADOPTION.md`, `docs/eop/05_Features/EGPU_CAMERA_SHADOW.md`, `task.md`, `CLAUDE.md`, `AGENTS.md` |
| 2026-08-23 model lineage audit | **Official upstream big model on eGPU; Bukapilot supercombo on local RKNN.** Audited `../bukapilot@0c6977fc69`: its 165,403,347-byte `supercombo.rknn` is a monolithic nine-input/one-output openpilot model (6,504 floats), built by RKNN compiler 2.3.0 explicitly for RK3588. Source ONNX LFS SHA-256 is `d21daa...` (51,461,700 bytes); checked RKNN SHA-256 is `39155c...`. RK3576 requires a separate conversion and validation from the exact source. Bring-up will first compare that same Bukapilot graph on tinygrad/eGPU versus RKNN, then introduce official upstream `big_driving_supercombo.onnx` (`a501760a...`, 1,757,355,221 bytes) as a different-generation model behind the same parsed openpilot contract. Engaged big→local failover soft-disables rather than silently continuing. | `docs/eop/05_Features/CHESTNUT_EGPU_ADOPTION.md`, `task.md`, `CLAUDE.md`, `AGENTS.md` |
| *(this session)* | **Radar weather severity: wiper + windshield-contamination detectors, 3-step ADAS risk gate.** Naming rationale (user question): `precipProb` names the physical rain/snow clutter signal, but the wiper is the driver-confirmed cue — added `WiperMotionDetector` (periodic bursts of near-range (<1.5 m) non-static returns; rising-edge sweep counting over a 10 s window, `sweep_rate_hz` exposed since fast wipe = heavy rain) publishing `Radar4D.wiperOn`. Windshield contamination (rain drops / washer fluid / snow ON the glass — 60 GHz is highly water-sensitive): `WindshieldContaminationDetector` combines a persistent near-range STATIC hot zone (film rides with the car, unlike the blade) with far-target attenuation vs a slowly-adapted clean SNR baseline (6 dB trigger, 0.5 s streak; `attenuation_db` exposed, 99 dB when far returns vanish), publishing `glassContaminated`. Severity: `classify_weather_severity()` merges all signals into `weatherSeverity` 0-3 (clear/light/moderate/heavy: heavy = attenuation >=12 dB or fast wipe + heavy clutter); `longitudinal_planner` gate replaced with `WEATHER_ACCEL_SCALE = (1.0, 0.8, 0.6, 0.4)` stepped by level (`_apply_weather_severity_limit`) so ADAS risk margin steps down with conditions. capnp: 3 new `Radar4D` fields (wiperOn, glassContaminated, weatherSeverity); cereal rebuilt. Tests: 198 passed (2 pre-existing failures); new tests for sweep rate, contamination depth, severity classifier (all levels), stepped gate. | `cereal/custom.capnp`, `selfdrive/controls/radar4d.py`, `selfdrive/controls/radar4d_pointcloud.py`, `selfdrive/controls/lib/longitudinal_planner.py`, `selfdrive/controls/tests/test_radar4d_pointcloud.py`, `selfdrive/controls/tests/test_weather_accel_gate.py` |

---

## 🔖 STATUS TRACKING

**Branch:** `EOP10` • **Base:** `c085b8af1` (upstream)  
**Backups:** tag `backup/EOP10-squashed-20260610` (pre-final-audit squashed state)  
**Last updated:** 2026-06-11 — **FINAL AUDIT COMPLETE**, see `FINAL_AUDIT_2026-06-10/LEDGER.md`

> **⚠️ Hash reachability note (2026-08-08):** the individual commit hashes
> recorded in this file below (`1d5f050ef` and earlier) are **no longer
> reachable** from the current branch tip — a later history rewrite orphaned
> them (`git merge-base --is-ancestor 1d5f050ef HEAD` fails; `git branch
> --contains 1d5f050ef` returns nothing). `git log 1d5f050ef..HEAD` will
> silently return the *entire* branch history instead of a real delta — do
> not use it to compute "what's new since the last audit." The content
> record below is still accurate (tree-identical per the squash), but to
> compute a real delta, anchor on **`858419d7d`** (`[DOCS] EOP architecture
> documentation and upstream audit records` — the last old topic commit that
> *is* still reachable) instead. See `docs/upstream-audit/CROSS_BRANCH_AUDIT.md`
> (Node 8) for the commits audited past that point (`858419d7d..ba0151e03`,
> plus the working tree as of 2026-08-08).

**Constraints given by user:**
- No LFS (adds repo complexity).
- Foundation-first commit layout.
- Model blobs: removed from git tracking; kept in working directory; `.gitignore` added.

**History note:** the 14-commit layout below was over-squashed to 3 commits (verified
content-preserving, all tips tree `fb27174a`), then the 2026-06-10 final audit
(defects D1–D18 fixed; see `FINAL_AUDIT_2026-06-10/`) rebuilt the branch as 12 clean
topic commits, tree-identical to the audited target:

```
063b722ac  [INFRA] Remove heavy submodules for edge deployment
17ed1e941  [INFRA] Convert third_party libraries to submodules
1e790950e  [THIRD_PARTY] Drop comma-device-specific headers and libs
112cf8804  [BUILD] Root build system + repo meta for RK edge platform
a6e0bf42d  [CEREAL] Messaging schema for EOP edge platform
c0a8de179  [COMMON] Core utilities for RK platform
7940b316d  [MODELS] Model download scripts + EOP setup helpers
c848459e4  [SYSTEM] Rework daemons for RK3576/RK3588 edge platform
4c764e409  [SELFDRIVE/ASSETS] UI asset bundle (LFS materialization + EOP additions)
af0dc850a  [SELFDRIVE] Controls, perception and UI logic for EOP edge platform
77d082bf7  [TOOLS] Developer tooling for EOP edge platform
766de0c9d  [DOCS] EOP architecture documentation and upstream audit records
(+ 7c6134902 [DOCS] rebuild record)
```

> **Next session:** resume from `FINAL_AUDIT_2026-06-10/LEDGER.md` (single remaining
> step: force-push after user confirmation). Per-commit reviews: `COMMIT_*_REVIEW.md`.

### Step progress

| Step | Description | Status |
|------|-------------|--------|
| 0 | Audit written (this file) | ✅ done |
| 1 | Delete meta-docs (`docs/migration/`, stale status reports) | ✅ done |
| 2 | Delete meta-tests (`tests/commit_verification/`) | ✅ done — dir removed in Phase 2 |
| 3 | Revert cereal schema churn (OnroadEvent IDs, DeviceType enum, service defs) | ✅ done — Phase 1 reverts + Phase 2 rebuild |
| 4 | Remove model blobs from git; update `download_models.sh` + sha256 manifest | ✅ done — `models/` contains only scripts + `MODEL_MANIFEST.md` |
| 5 | `.vscode/launch.json` unchanged vs upstream; AGENTS.md absent from repo | ✅ n/a |
| 6 | `.gitmodules` cosmetic reformat reverted | ✅ done — Phase 1 |
| 7 | Jenkinsfile: upstream stages kept; `rk3588` stage added additively | ✅ done |
| 8 | catch2, json11, kaitai, qrcode restored to upstream vendored copies | ✅ done — Phase 1 |
| 9 | acados, libyuv, raylib evaluated; restored to upstream vendored | ✅ done — Phase 1 |
| 10 | MCAP stack trimmed (4 files gone); `perf_monitor.py` kept (active importer in stereod) | ✅ done / justified |
| 11 | `system/webrtc/` deletion — justified (steamd supersedes webrtcd) | ✅ justified — no restoration needed |
| 12 | Restore `selfdrive/pandad/` — disable in manager | ✅ done — present in [SYSTEM] commit |
| 13 | Restore `selfdrive/car/` framework | ✅ done — present in [SELFDRIVE] commit |
| 14 | `tools/cabana/longitudinal_maneuvers/car_porting` deletion — justified (CANape + fixed Tesla) | ✅ justified — no restoration needed |
| — | Remove dev-machine files (bin/, ARCHITECTURE.md, SYSTEM_CONFIG.md, etc.) | ✅ done — `[AUDIT-REVERT] a688630af` |
| 15 | Audit `selfdrive/controls/` 28-file diff; convert rewrites to additive | ✅ done — see `docs/upstream-audit/CONTROLS_AUDIT.md` — 4 crash bugs + 5 behavioral issues fixed |
| 16 | Code-review `93374f4c` [SYSTEM] — 230-file daemon rework | ✅ done — see `docs/upstream-audit/COMMIT_93374F4C_REVIEW.md` — 8 bugs fixed |
| 17 | Code-review `9e5b84ed` [SELFDRIVE] — 400-file controls/UI rework | ✅ done — see `docs/upstream-audit/COMMIT_9E5B84ED_REVIEW.md` — 6 bugs fixed |
| 18 | Code-review `3610e2e2` WIP [inferenced refactor] — 51-file HAL/compute rework | ✅ done — see `docs/upstream-audit/COMMIT_3610E2E2_REVIEW.md` — 8 bugs fixed |
| 19 | Code-review `d11239cc` [TOOLS] — developer tooling (calibration, Foxglove, sim, RKNN convert) | ✅ done — see `docs/upstream-audit/COMMIT_D11239CC_REVIEW.md` — 8 bugs fixed |
| 20 | Code-review `cabe4693c` [feat(inferenced)] — hardware backend modules (NPU/RGA/Hailo/ACL/OpenCL) | ✅ done — see `docs/upstream-audit/COMMIT_CABE4693_REVIEW.md` — 8 bugs fixed |
| 21 | Code-review `73317b36c` [feat(inferenced)] — job execution framework with backend dispatch | ✅ done — see `docs/upstream-audit/COMMIT_73317B36_REVIEW.md` — 5 bugs fixed |
| 22 | Code-review `23527f7f6` [fix(adas)] — remove KeyboardInterrupt from critical control daemons | ✅ done — see `docs/upstream-audit/COMMIT_23527F7F_REVIEW.md` — 2 bugs fixed |
| 23 | Code-review `a00344102` [fix(phase-3)] — standardize error handling in 8 critical daemons | ✅ done — see `docs/upstream-audit/COMMIT_A0034410_REVIEW.md` — 4 bugs fixed |
| 24 | Code-review `73044c6b6` [fix(modeld)] — add explicit return type annotation to main() | ✅ done — see `docs/upstream-audit/COMMIT_73044C6B_REVIEW.md` — 0 bugs (annotation correct; 2 pre-existing issues documented) |
| 25 | Fix `globald` → `coordinationd` rename missed in `process_config.py` | ✅ fixed — `ec0c4e735` |
| 26 | Fix bluetoothd race conditions (BLE TX interleave, SPP socket race, NCPSession idempotency, param bytes decode) | ✅ fixed — `90b54e00b` |
| 27 | Audit commits `82f453f04` through `1d5f050ef` (18 unaudited commits) | ✅ done — all 20 `COMMIT_*_REVIEW.md` files written (22 total with prior reviews) |
| 28 | Remove dead opendbc-referencing code (`selfdrive/car/`, debug tools, tests) | ✅ done — `f384f56c5` — 56 files, 6,473 lines removed |
| 29 | Implement Hailo backend (`hailo_hef.py` placeholder → real HailRT API) | ✅ done — `ddbcb58ab` |
| 30 | Expand daemon import smoke test (`test_daemon_imports.py` 22 → 29 modules) | ✅ done — `ddbcb58ab` |
| 31 | Resolve WIP commits (`16ea9efe3`, `e11e33e8b`) | ✅ reviewed — code is syntactically valid and integrated; history rewrite deferred |
| 32 | Wire TJA resume alert (`longcontrol.py` TODO), monod segmentation, stereo depth | ✅ fixed — `be43b68a2` |
| 33 | Fix HIGH bugs from `COMMIT_82F453F4_REVIEW.md` (ONNX backend) | ✅ fixed — `be43b68a2` |
| 34 | Fix MEDIUM bugs from `COMMIT_985E09B4_REVIEW.md` (vehicled refactor) | ✅ fixed — `be43b68a2` |

**How to resume next session:**
1. `git log --oneline c085b8af1..HEAD` — verify topic commits still present.
2. Read this file's Step progress table — identify next `⏭ next` step.
3. Each step = one commit appended on top of current HEAD. Commit messages should be prefixed `[AUDIT-REVERT]` so they're easy to squash back into their topic commit later.
4. After each step: run a quick build-sanity check (if feasible) and update the status table.

**Squash-back plan (deferred until all steps done):**
Once steps 1–14 land, consider an interactive rebase that folds each `[AUDIT-REVERT]` commit back into its original topic commit, leaving the branch with the original 13 clean topic commits — but with the diff-from-upstream minimized.

---

**Legend**
- ✅ **Keep** — load-bearing for the edge hardware/feature set.
- ⚠️ **Trim** — partially needed; some sub-changes revertable.
- ❌ **Revert** — no clear edge-ADAS justification, pure churn, or cosmetic.

Commits are listed chronologically (foundation → leaf).

---

## 1. `1f35f3e56` [INFRA] Remove heavy submodules for edge deployment

**Touched:** `.gitmodules`, `.lfsconfig`, `opendbc`, `opendbc_repo`, `panda`, `teleoprtc`, `teleoprtc_repo` (7 files)

### Intent vs actual

Commit message says "remove panda/opendbc/teleoprtc + .lfsconfig". The diff does that, **but also** rewrites `.gitmodules` to declare ~17 new submodules for third_party (valhalla, acados, arm_compute, catch2, libyuv, raylib, json11, kaitai, qrcode, rockchip_mpp, rockchip_rga, hailort, python-udsoncan, python-can-isotp, pygnssutils, linux_gc4653, linux_ox03c10). Those additions semantically belong with commit 2 (`[INFRA] Convert third_party libraries to submodules`) — this commit is mislabelled.

### Change-by-change

| Change | Classification | Notes |
|---|---|---|
| Remove `panda` submodule | ⚠️ **Trim** | `panda` is comma STM32 CAN hardware. If edge ADAS uses a different CAN gateway, fine. But many of our own daemons (`selfdrive/pandad`) still reference panda concepts — confirm pandad can run without the submodule before keeping this revert. |
| Remove `opendbc` + `opendbc_repo` submodules | ❌ **Revert candidate** | Even with different CAN hardware, DBC message definitions are still needed to decode vehicle CAN. Upstream `selfdrive/car` imports heavily from `opendbc`. Removing it likely breaks `car/` imports — verify. |
| Remove `teleoprtc` / `teleoprtc_repo` | ✅ **Keep revert** | Teleop WebRTC is a comma cloud feature, not relevant to on-device ADAS. |
| Remove `.lfsconfig` | ✅ **Keep revert** | We don't want LFS — it adds operational complexity. Cost: previously-LFS assets become real blobs (see commit 7 & 9). Mitigation: large blobs (models) go through a download script, not git. Small assets (icons/fonts) stay in-tree. |
| Reformat `.gitmodules` (2-space → tab indent, add `branch =` fields) | ❌ **Revert** | Pure cosmetic churn. Upstream uses 2-space, no branch fields on most entries. Every line we reformat is a line the auditor has to read. |
| Add 17 third_party submodule declarations | ⚠️ **Move to commit 2** | Logically belongs with the third_party conversion. Low priority but improves audit clarity. |

### Recommended actions
1. Investigate whether `selfdrive/car` still needs `opendbc` — if yes, restore the `opendbc` / `opendbc_repo` submodule.
2. Revert cosmetic reformat of `.gitmodules` — keep upstream's 2-space indent; only add/remove entries, don't re-indent existing ones.
3. Move the 17 third_party `[submodule ...]` blocks into commit 2.

---

## 2. `39f348e21` [INFRA] Convert third_party libraries to submodules

**Touched:** 368 files, +644 / −84,757. Deletes vendored copies of acados/arm_compute/catch2/libyuv/raylib/json11/kaitai/qrcode/qt5/valhalla from `third_party/` and replaces them with submodule gitlinks. Adds a few rockchip-related dirs and `valhalla.json.template`.

### Rationale check

Upstream openpilot deliberately vendors these third_party libs (no submodules for them) so that `git clone` gives you a buildable tree. Converting them to submodules means:
- Any clone now requires `git submodule update --init --recursive`.
- Upstream syncs become harder (we'll conflict with upstream's vendored copies every time they bump them).
- `third_party/` now points at *upstream third-party projects directly*, pinned to specific tags; if those projects remove the tag or rewrite history, our build breaks.

### Change-by-change

| Change | Classification | Notes |
|---|---|---|
| Submodule: `acados` (v0.5.3) | ⚠️ **Trim** | We use acados for MPC. Upstream vendors it. No edge-specific reason to move to submodule — pure style change. Revert to vendored. |
| Submodule: `arm_compute` (v52.8.0) | ✅ **Keep** | ARM Compute Library for NEON-accelerated CV on RK3588. New need vs upstream. |
| Submodule: `catch2` (v3.14.0) | ❌ **Revert** | Catch2 is upstream's C++ test framework and already vendored there. No edge reason. |
| Submodule: `libyuv` | ⚠️ **Trim** | Upstream vendors. Revert unless we bumped the pin for a specific fix. |
| Submodule: `raylib` (5.5) | ✅ **Keep** | EOP uses raylib for some UI components (per AGENTS.md). Upstream doesn't. |
| Submodule: `json11`, `kaitai`, `qrcode` | ❌ **Revert** | All three are upstream-vendored. No edge-ADAS reason to move to submodules. |
| Submodule: `rockchip_mpp`, `rockchip_rga` | ✅ **Keep** | Rockchip video/graphics acceleration — hardware-required. |
| Submodule: `hailort` (v5.3.0) | ✅ **Keep** | Hailo NPU runtime — required for monod (per commit 4 services). |
| Submodule: `python-udsoncan`, `python-can-isotp`, `pygnssutils` | ⚠️ **Trim** | These are Python packages. Prefer `uv`/`pyproject.toml` pinning over git submodules. Check if these are also pip-installable. |
| Submodule: `linux_gc4653`, `linux_ox03c10` | ✅ **Keep** | Kernel source trees for camera sensor drivers — required for v4l2d. |
| Submodule: `valhalla` (3.6.3) | ⚠️ **Trim** | Routing engine. If we're shipping offline maps, keep. If this is dev-only, could drop. Verify runtime usage. |
| Delete `third_party/qt5/larch64/bin/{lrelease,lupdate}` | ✅ **Keep delete** | qt5 larch64 binaries are for comma AGNOS; we build on RK AGNOS. |
| Delete `third_party/bootstrap/` | ✅ **Keep delete** | Bootstrap icons used by old commai installer flow. |
| Add `third_party/valhalla.json.template` (98 lines) | ⚠️ **Review** | If valhalla submodule goes, this goes too. |

### Recommended actions
1. Revert `catch2`, `json11`, `kaitai`, `qrcode` submodules back to upstream's vendored copies.
2. Evaluate reverting `acados`, `libyuv`. Only keep as submodule if we actually bumped the version.
3. Replace `python-udsoncan`, `python-can-isotp`, `pygnssutils` submodules with `uv` dependencies.
4. Keep Rockchip + Hailo + camera-sensor-kernel submodules (hardware-required).

### Audit-surface impact
~90% of the deletions here are upstream-vendored code we're re-sourcing from submodules. Reverting the unnecessary conversions could cut this commit from 368 files to ~50-80 files (just the new Rockchip/Hailo/camera additions + qt5/bootstrap deletions).

---

## 3. `9eafaa23c` [BUILD] Root build system + IDE/repo meta

**Touched:** `SConstruct` (259 lines), `site_scons/valhalla_build.py` (new, 102), `Jenkinsfile` (gutted, 113→~40), `AGENTS.md` (new, 996), `.vscode/launch.json`, `README.md`, `.gitignore`, `.gitattributes` (20 lines removed).

### Change-by-change

| Change | Classification | Notes |
|---|---|---|
| `SConstruct` rewrite (259 lines changed) | ⚠️ **Trim** | Needed to add RK3588/RK3576 targets and wire new submodule third_party libs. But review carefully — upstream SConstruct already supports arm64 via tici; we should be additive (add arch detection for RK), not rewrite. |
| `site_scons/valhalla_build.py` (new) | ⚠️ **Keep if valhalla stays** | Only makes sense if valhalla submodule stays (see commit 2). Otherwise drop. |
| `Jenkinsfile` — remove all `tici-*` CI stages (onroad, HW, loopback, legacy camera daemon (AR0231/OX03C10/OS04C10), sensord, replay, tizi) and replace with one `rk3588 tests` stage | ❌ **Revert most** | We killed upstream's entire HIL CI matrix. For audit, it's much cleaner to **keep all upstream stages intact** and **add** a parallel `rk3588` stage. Upstream reviewers expect to see their CI still running. |
| `.gitattributes` — remove all `filter=lfs` lines (20 entries for onnx/svg/png/gif/ttf/wav, third_party .a/.so/.dylib, catch2, qt5) | ✅ **Keep** | We don't want LFS. Removing the filter rules is correct. Side effect is bloat (previously-LFS blobs now real), which we solve separately for models (download script) and accept for small assets. |
| `.gitignore` — add `selfdrive/ui/_spinner`, `selfdrive/ui/_text` | ✅ **Keep** | Local build outputs, fine to ignore. |
| `.vscode/launch.json` — add "Replay drive" + LLDB attach configs | ❌ **Revert** | Personal dev environment, doesn't belong in-tree. If kept, at least move to `.vscode/launch.sample.json` and gitignore the real one. |
| `README.md` — add EOP banner | ✅ **Keep** | One-line banner pointing at upstream is reasonable for a fork. |
| `AGENTS.md` (new, 996 lines) | ⚠️ **Trim** | Useful for AI coding agents, but 996 lines is a lot. Move to `docs/` and link from README. Don't clutter repo root. |

### Recommended actions
1. **Jenkinsfile**: restore upstream CI stages verbatim, add a single `rk3588` stage alongside them (net change: ~10 lines instead of 113).
2. **`.vscode/launch.json`**: revert. Move personal debug configs out of tree.
3. **`AGENTS.md`**: move to `docs/AGENTS.md`.
4. **`SConstruct`**: audit whether each change is additive or a rewrite; convert rewrites to additive patches where possible.

---

## 4. `feeaba930` [CEREAL] Messaging schema + submodule pins

**Touched:** `cereal/custom.capnp` (+926), `cereal/log.capnp` (+1038 net), `cereal/services.py` (+114 net), `msgq_repo`, `rednose_repo`, `tinygrad`, `tinygrad_repo`.

### Change-by-change

| Change | Classification | Notes |
|---|---|---|
| `cereal/custom.capnp` — add `CustomReserved20…99` (80 empty struct stubs) | ❌ **Revert** | These are empty placeholder structs. Upstream already has `CustomReserved0…19` for the same purpose — there's no reason to pre-allocate 80 more empty slots. Cap'n Proto IDs are reserved at first use, not declaration. Pure noise. |
| `cereal/log.capnp` — add `OnroadEvent` entries for `stereoFault`, `inferenceFault`, `monoFault`, `rgaFault`, `mppFault`, `gridFault`, `pointcloudFault` (7 new events) | ✅ **Keep** | Directly tied to our new edge daemons (stereod, inferenced, monod, etc.). Hardware-specific fault states are legitimate schema additions. |
| `cereal/log.capnp` — remove `OnroadEvent.userBookmark`, `audioFeedback`, and renumber `excessiveActuation` | ❌ **Revert** | Unrelated to edge hardware. Upstream just added these (see upstream commit `c085b8af1` "feedbackd: remove lkas toggle"). Renumbering capnp IDs breaks wire compat with upstream logs. |
| `cereal/log.capnp` — `InitData.DeviceType`: replace `{neo, chffrAndroid, chffrIos, tici, pc, tizi, mici}` with `{rk3588}` only | ⚠️ **Trim** | We need `rk3588` (and `rk3576`?), but removing `tici/pc` breaks backwards-compat with upstream log decoders. **Add** `rk3588 @8, rk3576 @9` rather than replacing. |
| `cereal/log.capnp` — `FrameData.ImageSensor`: add `gc4653 @4` | ✅ **Keep** | New camera sensor — additive, proper enum numbering. |
| `cereal/log.capnp` — `DeviceState`: add `externalStoragePresent/FreePercent/Path` | ✅ **Keep** | New hardware feature (external storage on RK boards). |
| `cereal/log.capnp` — `PeripheralState`: add `ignitionLine`, `ignitionCan` | ✅ **Keep** | GPIO ignition for platforms without panda. |
| `cereal/log.capnp` — add `CameraObject`, multi-camera tracking, stereo, grid, BEV, voice, BSD, OBD, SPP, etc. structs | ✅ **Keep** | Core to EOP feature set. These are the messages for the new daemons. |
| `cereal/services.py` — add ~50 services (monoDetections, stereoDepth, voiceState, gridd/pathd/surfaced/etc.) | ✅ **Keep** | Matches the new daemons. |
| `cereal/services.py` — remove `pandaStates`, `qcomGnss`, `driverCameraState`, `driverEncodeIdx`, `driverStateV2`, `driverMonitoringState`, `navInstruction`, `navRoute`, `navThumbnail`, `userBookmark`, `bookmarkButton`, `audioFeedback`, `testJoystick` | ⚠️ **Trim** | Many of these are "we don't use this feature". Fine to not emit them, but **removing the service definitions** means we can't replay upstream logs. Keep the service entries; only the producers need to not start. |
| `cereal/services.py` — rename `testJoystick` → `remoteDriveStick` | ❌ **Revert** | Pure rename, no functional reason. Breaks tools that key on upstream service names. |
| `msgq_repo`, `rednose_repo` pointer bumps | ⚠️ **Review** | Pointer bumps — need to see what changed in those submodules. If it's just upstream-sync, fine. |
| `tinygrad`, `tinygrad_repo` pointers (−1 each) | ⚠️ **Review** | Looks like we removed one tinygrad entry. Upstream has `tinygrad_repo`. Make sure we didn't accidentally delete the working pointer. |

### Recommended actions
1. **Delete all 80 `CustomReserved20..99` stubs** from `custom.capnp`. Add new structs only when actually used.
2. **Restore upstream `OnroadEvent` entries** (userBookmark, audioFeedback) and keep their numbering. Add new events at the next free index.
3. **Restore upstream `DeviceType` enum entries**; add rk3588/rk3576 additively.
4. **Restore service definitions** for upstream services we don't produce. Unused service definitions cost nothing but preserve log compat.
5. **Un-rename `testJoystick`**.

---

## 5. `13c1ba7d3` [COMMON] Core utilities (GPU, MCAP, perf, params)

**Touched:** 12 files, +2,294 / −23.

### Change-by-change

| Change | Classification | Notes |
|---|---|---|
| `common/core_config.py` (new, 213) | ⚠️ **Review** | Yet-another-config abstraction. Check if it duplicates `openpilot.common.params` or `common/util`. Likely consolidatable. |
| `common/gpu_utils.py` (new, 217) | ✅ **Keep** | RK GPU detection/capability — hardware-necessary. |
| `common/mcap_foundation.py` (442), `mcap_logger.py` (384), `mcap_wrapper.py` (71), `logging_mcap_patch.py` (145) | ⚠️ **Trim heavily** | Four MCAP files totaling 1,042 lines. MCAP is a standard logging format (pip-installable). Almost all of this should be `pip install mcap` + a small adapter. **Strong candidate for consolidation**. |
| `common/perf_monitor.py` (new, 473) | ✅ **Keep** | Imported by 10 daemons (sided, recordd, coordinationd, mapd, soundd, navd, obd2d, modeld, vehicled/car/card.py, pointcloudd). Load-bearing — do not delete. |
| `common/params_keys.h` (+271) | ✅ **Keep** | New parameter keys for EOP features (voiced, coordinationd, etc.) must be declared. But audit that each new key is actually used somewhere. |
| `common/realtime.py` (+36 net) | ⚠️ **Review** | Small change — check if it's adding an RK-specific sched_fifo call or something generic. |
| `common/transformations/camera.py` (+43 net) | ✅ **Keep** | New cameras (gc4653, ox03c10 stereo) require new transform entries. |
| `common/util.{cc,h}` (+11 each) | ⚠️ **Review** | Tiny, likely fine. Inspect the 11 lines. |

### Recommended actions
1. **Audit MCAP stack** — collapse the 4 files into one thin wrapper around the upstream `mcap` Python package. Expected reduction: ~800 lines.
2. ~~**Delete `perf_monitor.py`**~~ — confirmed load-bearing (10 daemon importers). Keep.
3. **Audit `core_config.py`** for overlap with existing params/util.
4. Keep gpu_utils, params_keys, transformations — these are genuinely hardware-driven.

---

## 6. `4c5744754` [THIRD_PARTY] Drop legacy comma-specific headers and libs

**Touched:** 43 files, +4 / −13,872 (almost pure deletion).

### Analysis

Deletes:
- Qualcomm/MSM Linux headers (`third_party/linux/include/**`) — comma Snapdragon platform, irrelevant on RK.
- OpenCL headers (`third_party/opencl/include/CL/**`) — comma Adreno GPU via OpenCL, RK uses Mali via OpenCL too but we use Rockchip MPP/RGA instead.
- Qt5 larch64 binaries.
- Bootstrap icons.

Plus submodule pointer bumps for `acados`, `arm_compute`, `libyuv`, `json11`, `raylib`, `valhalla`.

### Change-by-change

| Change | Classification | Notes |
|---|---|---|
| Delete MSM/Qualcomm Linux headers | ⚠️ **Trim** | Not used on RK — **but** the upstream camera daemon includes some of these for v4l2 ioctl defs. Confirm RK v4l2d doesn't indirectly include them. If safe, delete is fine. |
| Delete OpenCL headers | ⚠️ **Trim** | Same caution — some upstream daemons include `CL/cl.h`. If we've replaced all OpenCL paths with MPP/RGA, safe. If any path still uses OpenCL, this breaks builds on non-RK dev machines. |
| Delete Qt5 larch64 binaries (lrelease, lupdate) | ✅ **Keep delete** | Platform-specific binaries we don't ship. |
| Delete `third_party/bootstrap/` | ✅ **Keep delete** | Installer UI asset, not used. |
| Submodule pointer bumps (acados, arm_compute, libyuv, json11, raylib, valhalla) | ⚠️ **Review** | If we reverted those to vendored per commit 2, this commit's pointer bumps disappear. |

### Recommended actions
1. **Verify upstream includes**: `grep -rn "CL/cl.h\|linux/msm\|linux/cam_" system/ selfdrive/` before accepting the MSM/OpenCL header deletions.
2. If commit 2 reverts submodule conversions, the pointer bumps here need the same treatment.

---

## 7. `1d63f6b0f` [MODELS] Add RKNN/ONNX/HEF model binaries + install scripts

**Touched:** 13 files, +360,268 lines. Most of the line count is ONNX files that git-text-expanded (`egolanes_lite_int8.onnx` 74k lines, `scene3d_lite_int8.onnx` 141k, `sceneseg_lite_int8.onnx` 143k).

### Change-by-change

| Change | Classification | Notes |
|---|---|---|
| `models/rknn/driving_vision.rknn` (37 MB), `driving_policy.rknn` (8.5 MB) | ❌ **Remove from git** | Download via `download_models.sh` on setup/install. Ship a versioned manifest + URL, not the blob. |
| `models/hef/whisper_base_5s_encoder.hef` (46 MB), `yolov8n.hef` (13 MB) | ❌ **Remove from git** | Same — download on install. |
| `models/onnx/*_int8.onnx` (5 files, ~160 MB total) | ❌ **Remove from git** | Same — download on install. |
| `models/download_models.sh` (124 lines) | ✅ **Keep** | The install-time fetcher. Update to cover all model files after we remove them from git. |
| `scripts/install_pygnssutils.sh` (30) | ❌ **Revert** | Replace with a `pyproject.toml` dependency (see commit 2). |
| `scripts/validate_eop.sh` (203), `scripts/verify_setup.py` (103) | ⚠️ **Review** | Nice-to-have validation scripts. If they duplicate upstream's `tools/op.sh` / `setup.sh` logic, consolidate. |

### Recommended actions
1. **Remove all `.rknn/.hef/.onnx` blobs from git**; extend `download_models.sh` to fetch them from a release/CDN. Add a manifest (URL + sha256 + version) checked into the repo.
2. **Drop `install_pygnssutils.sh`**; move pygnssutils to `pyproject.toml`.
3. **Audit `validate_eop.sh` / `verify_setup.py`** for duplication with upstream setup.

### Audit-surface impact if actioned
Commit goes from **+360,268 lines → ~+500 lines** (scripts + download manifest only; model blobs are not in git at all).

---

## 8. `ed7518464` [SYSTEM] Rework daemons for RK3576/RK3588 edge platform

**Touched:** 207 files, +13,262 / −16,073.

### Top-level breakdown

| Subdir | Files | Nature | Classification |
|---|---|---|---|
| `system/hardware/` | 43 | RK3588/RK3576 HAL, deletes `pc/` + `tici/` | ⚠️ **Trim** — add RK HAL additively, don't delete pc/tici |
| `system/v4l2d/` | 32 | Replaced upstream camera pipeline | ✅ **Keep** |
| `system/inferenced/` (new) | 11 | NPU inference scheduler | ✅ **Keep** |
| `system/bluetoothd/` (new) | 11 | BT SPP for phone integration | ⚠️ **Review** — is this edge-ADAS core or UX nice-to-have? |
| `system/v4l2d/` (new) | 10 | V4L2 capture daemon | ✅ **Keep** |
| `system/sensord/` | 10 | IMU drivers | ⚠️ **Trim** — upstream sensord supports BMX/LSM; add RK IMU additively |
| `system/socketd/` (new) | 9 | CAN bridge with safety (replaces panda) | ✅ **Keep** |
| `system/athena/` (−8, gutted) | 8 | **Deletes `athenad.py` (861 lines), registration, tests** | ⚠️ **Trim** — upstream athenad is the comma-cloud agent; we don't need it running but deleting the code makes upstream rebases painful. Disable in manager instead. |
| `system/webrtc/` | 7 | WebRTC daemon (`webrtcd.py`, audio/video devices, tests) | ✅ **Present** — files restored |
| `system/updated/` | 8 | Updater for RK | ⚠️ **Review** — rewrite vs additive? |
| `system/thermald/` | 5 | Thermal management | ⚠️ **Review** — RK thermal zones differ, but ideally additive |
| `system/qcomgpsd/` | 5 | Qualcomm GPS daemon | ✅ **Keep delete** (if deleted) — Qcom-specific |
| `system/loggerd/` | 5 | Logger | ⚠️ **Review** |
| `system/proclogd/` (−6) | 6 | Process logger | ⚠️ **Review** |
| `system/manager/`, `logcatd/`, `ui/`, `ubloxd/`, `tests/` | 14 | Various | ⚠️ **Review case-by-case** |
| `system/wdgd/`, `stated/`, `spkd/`, `micd/`, `networkd/`, `mcapd/`, `imud/`, `rtkd/`, `rtcd/`, `tombstoned.py`, `timed.py` | ~20 | New EOP daemons | ✅ **Keep** |

### General principle

Upstream divergence is minimized when we **add new daemons as new directories** and **keep upstream daemons intact** (just disabled via `system/manager`). Many changes in this commit look like *wholesale rewrites* of upstream files — those are the highest audit burden.

### Recommended actions
1. **`system/athena/` + `system/webrtc/`**: restore upstream code, disable in manager config. The deleted tests especially should come back.
2. **`system/hardware/pc/` + `tici/`**: restore. Add `rk3588/`, `rk3576/` as siblings. Upstream's hardware registry pattern supports this.
3. **`system/sensord/`, `thermald/`, `updated/`, `loggerd/`, `proclogd/`, `manager/`**: audit each — convert rewrites to additive patches. Aim for each file's diff to be < 50% of its size.
4. Keep new-daemon additions (inferenced, v4l2d, socketd, wdgd, etc.) as-is — those are genuine EOP features.

### Audit-surface impact if actioned
Expect ~30–40% reduction in touched lines (mostly by reverting gutted-upstream-daemon deletions).

---

## 9. `b09070122` [SELFDRIVE/ASSETS] UI icon/font/training asset bundle

**Touched:** 221 files, +444 / −106. Binary assets (icons, fonts, sounds, offroad images, training screens).

### The LFS pointer explanation

Most of these files show diffs like `awake.gif | Bin 130 -> 69319 bytes`. **The "130 bytes" is the size of the LFS pointer file in upstream.** By killing `.lfsconfig` + `.gitattributes` LFS rules (commits 1 & 3), we caused every previously-LFS asset to be checked into git as a real blob. Since we don't want LFS, this is the expected cost.

For audit, the right framing is: **byte-verify these against upstream** (they should be the identical binary content upstream hosts via LFS), then only review genuinely new assets.

### Change-by-change

| Change | Classification | Notes |
|---|---|---|
| 200+ files with `130 -> XXXX bytes` pattern (upstream LFS content) | ✅ **Keep, byte-verify** | Same content as upstream, now stored inline. Auditor verifies sha256 against upstream LFS and moves on. |
| Any *genuinely new* asset (not upstream) | ✅ **Keep, review** | Need to diff asset tree against upstream to find these. Likely < 20 files. |

### Recommended actions
1. **Generate a sha256 manifest** comparing our `selfdrive/assets/` against upstream's LFS-tracked blobs. Add to `docs/upstream-audit/` so the auditor can quickly confirm "these are byte-identical to upstream".
2. **Identify net-new assets** (files that don't exist in upstream at all) and call them out in a short review note.

### Audit-surface impact if actioned
Files stay in the commit, but the auditor's effort drops to "run sha256, compare" for 200 files + detailed review of ~10–20 genuinely new assets.

---

## 10. `967349fcc` [SELFDRIVE] Daemons, controls, UI logic

**Touched:** 317 files, +55,540 / −6,236. The biggest commit.

### Top-level breakdown

| Subdir | Files | Nature | Classification |
|---|---|---|---|
| `selfdrive/ui/` | 81 | Heavy UI rework (new `qt/cards/assist_card.*`, `nav_card.*`, layouts, installer) | ⚠️ **Trim** — audit for additive vs replacement |
| `selfdrive/controls/` | 28 | Control loop changes | ⚠️ **Review** — edge ADAS needs new control modes, but keep upstream CC/ACC code |
| `selfdrive/pandad/` | 24 | CAN gateway daemon (`panda.cc`, `pandad.cc`, `spi.cc`, tests) | ✅ **Present** — files restored; disabled in manager (socketd is EOP CAN gateway) |
| `selfdrive/modeld/` | 18 | Model daemon RK adaptation | ⚠️ **Review** — additive if possible |
| `selfdrive/pathd/` (new) | 17 | Path planning | ✅ **Keep** |
| `selfdrive/vehicled/` (new) | 16 | Vehicle interface replacing opendbc (per AGENTS.md: "Tesla-only") | ⚠️ **Review** — if really Tesla-only + experimental, keep but audit scope |
| `selfdrive/gridd/` (new) | 16 | Occupancy grid | ✅ **Keep** |
| `selfdrive/intentd/` (deleted) | 15 | Intent processing — consolidated into voiced | ❌ **Deleted** |
| `selfdrive/car/` | 11 | Car framework (`car_specific.py`, `card.py`, `cruise.py`, `docs.py`, `tests/`) | ✅ **Present** — all files restored |
| `selfdrive/locationd/` | 9 | Localization | ⚠️ **Review** |
| `selfdrive/stereod/` (new) | 8 | Stereo depth | ✅ **Keep** |
| `selfdrive/obd2d/` (new) | 8 | OBD2 daemon | ✅ **Keep** |
| `selfdrive/navd/`, `pointcloudd/`, `coordinationd/`, `steamd/`, `selfdrived/`, `mapd/`, `surfaced/`, `soundd/`, `recordd/`, `monod/`, `tripd/`, `inferenced/`, `debug/` | ~80 | New EOP daemons | ✅ **Keep** — all present |
| `selfdrive/voiced/`, `selfdrive/waked/`, `selfdrive/monitoring/` | — | Planned EOP daemons | ⚠️ **Not yet implemented** — directories do not exist |

### Recommended actions
1. **`selfdrive/car/`** — hard stop. Don't delete car framework or tests. If vehicled replaces opendbc for Tesla specifically, keep upstream car interfaces for future vehicle support and parity. Restore `card.py`, `car_specific.py`, `tests/`.
2. **`selfdrive/pandad/`** — restore files; disable daemon in manager.
3. **`selfdrive/ui/`** — audit the 81 files. New cards (`assist_card`, `nav_card`) are net-new — keep. But check if we gutted existing layouts unnecessarily.
4. **`selfdrive/controls/` (28 files)** — treat with extreme care. Auditor/functional-safety concern scales with changes here. Aim for < 10 files changed, all additive.
5. Keep all net-new daemons.

### Audit-surface impact if actioned
Restoring `car/`, `pandad/`, and trimming ui/controls could drop this from 317 files to ~180–200. Still the largest commit, but the changes become more clearly "add new daemons" rather than "rewrite half the car stack".

---

## 11. `4919d431b` [TOOLS] Developer/debug tooling updates

**Touched:** 105 files, +2,073 / −13,728.

### Breakdown

| Subdir | Files | Nature | Classification |
|---|---|---|---|
| `tools/cabana/` (−72, deleted) | 72 | **Entire cabana (CAN message viewer) deleted** (~5,000 lines of C++) | ❌ **Revert deletion** — cabana is upstream's primary CAN debug tool, used against any CAN log. Even if we don't bundle it in releases, deleting it loses a dev tool. |
| `tools/bodyteleop/` (−9, deleted) | 9 | Teleop web UI | ✅ **Keep delete** — depends on removed `teleoprtc` |
| `tools/profiling/snapdragon/` (−3, deleted) | 3 | Snapdragon profiler | ✅ **Keep delete** — platform-specific |
| `tools/longitudinal_maneuvers/` (−3, deleted) | 3 | Longitudinal test maneuvers | ❌ **Revert deletion** — useful for ADAS validation on any hardware |
| `tools/foxglove/` | 3 | Foxglove integration | ⚠️ **Review** |
| `tools/car_porting/` | 3 | Car porting helpers | ❌ **Revert deletion** — same rationale as `selfdrive/car/` |
| `tools/webcam/` | 2 | Webcam camera simulator | ⚠️ **Review** — small changes probably OK |
| `tools/convert_models_to_rknn.py` (new) | 1 | RKNN conversion helper | ✅ **Keep** |
| Other (lib, calibration, factory_calibration, replay) | 5 | Various | ⚠️ **Review** |

### Recommended actions
1. **Restore `tools/cabana/`, `tools/longitudinal_maneuvers/`, `tools/car_porting/`**. They're dev tools — zero runtime cost on the target, high value for debugging.
2. Keep the bodyteleop / snapdragon-profiler deletions.
3. Keep `convert_models_to_rknn.py` (new, useful).

### Audit-surface impact if actioned
From 105 files to ~20–25 files.

---

## 12. `053942e14` [TESTS] Commit-verification test suite

**Touched:** 17 files, +1,301. All new, all in `tests/commit_verification/`.

### Analysis

These are smoke tests that validate the structural claims of our other commits (e.g. `test_infra_submodules.py` verifies submodule removal, `test_docs_structure.py` checks docs exist). They're **meta-tests about our own changes**, not ADAS functional tests.

### Change-by-change

| Change | Classification | Notes |
|---|---|---|
| `test_infra_submodules.py`, `test_cleanup.py`, `test_docs_structure.py`, `test_commit_*` | ❌ **Revert** | Meta-tests about the PR itself. These become stale immediately and add zero ongoing value. Belong in a review checklist, not the test suite. |
| `test_hardware_abstraction.py`, `test_inference_stack.py`, `test_perception_stack.py`, `test_vehicle_interface.py`, `test_voice_pipeline.py`, `test_navigation_stack.py`, `test_system_services.py`, `test_ui_components.py`, `test_logging_calibration.py` | ⚠️ **Trim** | These may be shallow smoke tests. If they just `import` modules to verify they load, consolidate into one `test_imports.py`. |
| `analyze_code_quality.py` (333 lines), `run_simple_tests.py` (280), `run_tests.sh` (93), `conftest.py` (15) | ⚠️ **Review** | These are harness scripts. If upstream already has pytest + CI, this is duplicate infra. |

### Recommended actions
1. **Delete meta-tests** (`test_infra_submodules`, `test_cleanup`, `test_docs_structure`, `test_commit_*`).
2. **Consolidate** remaining smoke tests into 1–2 files. Move functional tests (if any) to the relevant daemon's existing `tests/` directory.
3. **Drop `analyze_code_quality.py`** unless it's doing something pytest/ruff can't.

### Audit-surface impact if actioned
From 17 files / 1,301 lines to ~3 files / ~200 lines.

---

## 13. `7dd1a6573` [DOCS] EOP architecture, migration, and component docs

**Touched:** 64 files, +18,111 / −15.

### Breakdown

| Subdir | Files | Notes |
|---|---|---|
| `docs/eop/` | ~40 | EOP architecture, hardware, controllers, daemons, integration, features, localization docs. |
| `docs/migration/` | 7 | **Process docs**: `SUBMODULE_COMMITS_REVIEW.md`, `SUBMODULE_FINAL_STATUS.md`, `SWEEP_CHECK_REPORT.md`, `THIRD_PARTY_SUBMODULES_STATUS.md`, `SUBMODULE_BUILD_INTEGRATION.md`, `SUBMODULES_CLEANUP.md`, `ROCKCHIP_SUBMODULES_SUMMARY.md` |
| `docs/visionpilot/` | 3 | VisionPilot feature/system comparison docs |
| `docs/voice/` | 1 | Voice pipeline architecture |
| Root `docs/*.md` (new) | 7 | `ARCHITECTURE_GPU_PARALLEL.md`, `CENTRALIZATION_STATUS.md`, `CONSISTENCY_CHECK_REPORT.md`, `DRIVER_CALLS.md`, `INFERENCED_ARCHITECTURE.md`, `SCHEMA.md`, `WORKSPACE.md` |
| `docs/assets/icon-*.svg`, `three-back.svg` | 5 | Upstream LFS content now inlined (same content, different storage) |
| `common/README.md`, `common/CLEANUP_REPORT.md`, `models/README.md` | 3 | Misc READMEs |

### Change-by-change

| Change | Classification | Notes |
|---|---|---|
| `docs/eop/**` | ✅ **Keep** | Legitimate architecture docs for the EOP feature set. |
| `docs/migration/**` (7 files) | ❌ **Revert** | These are one-off "we did a migration, here's the report" documents. Valuable during the migration PR review, worthless 3 months later. Put the useful content in commit messages; delete the files. |
| `docs/visionpilot/**` | ⚠️ **Review** | If VisionPilot is a reference implementation we're comparing against, docs are fine — but 1,500 lines of comparison doc is a lot. Consider summarizing. |
| `docs/CENTRALIZATION_STATUS.md`, `CONSISTENCY_CHECK_REPORT.md`, `CLEANUP_REPORT.md` (common/) | ❌ **Revert** | All three read like point-in-time audit reports — stale within weeks. Delete after merge. |
| `docs/DRIVER_CALLS.md`, `INFERENCED_ARCHITECTURE.md`, `ARCHITECTURE_GPU_PARALLEL.md`, `SCHEMA.md`, `WORKSPACE.md` | ✅ **Keep** | Architecture references, useful ongoing. |
| Asset `.svg` diffs | ✅ **Keep, byte-verify** | Same content as upstream LFS, now inlined. |

### Recommended actions
1. **Delete `docs/migration/**`, `docs/CENTRALIZATION_STATUS.md`, `CONSISTENCY_CHECK_REPORT.md`, `common/CLEANUP_REPORT.md`** — move relevant content to commit messages or a single `CHANGELOG.md`.
2. **Trim `docs/visionpilot/**` to a single concise summary.**
3. Keep `docs/eop/**` and architecture references.

### Audit-surface impact if actioned
From 64 files / 18,111 lines to ~45 files / ~12,000 lines.

---

## Summary table — estimated audit-surface reduction

| # | Commit | Current lines | Estimated after revert | Reduction |
|---|---|---|---|---|
| 1 | INFRA remove submodules | 82 ins / 26 del | ~20 del | ~80% |
| 2 | INFRA third_party submodules | +644 / −84,757 | +~300 / −~10,000 | ~85% |
| 3 | BUILD root | +1,306 / −248 | +~200 / −~20 | ~80% |
| 4 | CEREAL schema | +2,014 / −70 | +~600 / −0 | ~70% |
| 5 | COMMON utilities | +2,294 / −23 | +~800 / −~10 | ~65% |
| 6 | THIRD_PARTY drops | +4 / −13,872 | +4 / −13,872 | 0% (mostly justified) |
| 7 | MODELS blobs | +360,268 | +~500 (rest via LFS) | >99% |
| 8 | SYSTEM daemons | +13,262 / −16,073 | +~8,000 / −~4,000 | ~40% |
| 9 | SELFDRIVE/ASSETS | +444 / −106 (221 files) | ~10 files | ~95% |
| 10 | SELFDRIVE daemons | +55,540 / −6,236 | +~40,000 / −~1,000 | ~30% |
| 11 | TOOLS | +2,073 / −13,728 | +~500 / −~5,000 | ~60% |
| 12 | TESTS | +1,301 | +~200 | ~85% |
| 13 | DOCS | +18,111 / −15 | +~12,000 | ~30% |

## Step-by-step revert plan (no LFS)

Ordered from lowest-risk/highest-ROI to most invasive. Each step is a separate commit on top of the current 13, to be squashed back into the original topic commits at the end (or left as standalone revert-history — your call).

| Step | Action | Scope | Risk | ROI |
|------|--------|-------|------|-----|
| 1 | Delete meta-docs: `docs/migration/`, `docs/CENTRALIZATION_STATUS.md`, `CONSISTENCY_CHECK_REPORT.md`, `common/CLEANUP_REPORT.md` | Pure doc deletion | none | −~1,500 lines |
| 2 | Delete meta-tests: `test_infra_submodules.py`, `test_cleanup.py`, `test_docs_structure.py`, `test_commit_*` | Test-only | none | −~400 lines |
| 3 | Revert cereal schema churn: drop `CustomReserved20…99` stubs, restore upstream `OnroadEvent` IDs + `DeviceType` enum + `services.py` service defs, un-rename `testJoystick` | Schema (wire-compat) | low | −~900 lines, restores upstream log compat |
| 4 | Remove model blobs from git; update `download_models.sh` to fetch all models + add sha256 manifest | 13 `.rknn/.hef/.onnx` files | low (download path already exists) | −~360k lines |
| 5 | Move `AGENTS.md` → `docs/AGENTS.md`, revert `.vscode/launch.json` changes | 2 files | none | Removes personal-dev-env drift |
| 6 | Revert `.gitmodules` cosmetic reformat; move misplaced third_party submodule declarations from commit 1 → commit 2 | `.gitmodules` rework | low | Cleaner commit topic separation |
| 7 | Jenkinsfile: restore upstream CI stages, add `rk3588` stage alongside (additive) | 1 file | low | Preserves upstream CI matrix |
| 8 | Revert third_party submodule conversions for upstream-vendored libs: catch2, json11, kaitai, qrcode (restore as vendored) | 4 submodules, re-add vendored code | medium | Reduces commit 2 by ~70% |
| 9 | Evaluate acados, libyuv, raylib submodule conversions — keep only where we bumped the pin for a reason | Same pattern as step 8 | medium | Further commit-2 reduction |
| 10 | Trim `common/` MCAP stack: collapse 4 files (mcap_foundation, mcap_logger, mcap_wrapper, logging_mcap_patch) into one thin wrapper around the `mcap` pip package; delete `perf_monitor.py` unless an importer justifies it | `common/` | medium (need to trace importers) | −~800 lines |
| 11 | Restore upstream-deleted files (don't gut them — disable in manager): `system/athena/`, `system/webrtc/`, `system/hardware/pc/`, `system/hardware/tici/` | system/ | medium–high (check manager refs) | Large audit-surface reduction, better upstream-rebase story |
| 12 | Restore upstream-deleted: `selfdrive/pandad/`, disable in manager | selfdrive/ | medium | Same rationale as 11 |
| 13 | Restore `selfdrive/car/` framework (card.py, car_specific.py, docs.py, tests/) | selfdrive/car | high (touches safety-adjacent code) | Critical for functional-safety review |
| 14 | Restore `tools/cabana/`, `tools/longitudinal_maneuvers/`, `tools/car_porting/` | tools/ | none (dev tools only) | Restores developer experience |
| 15 | Audit `selfdrive/controls/` 28-file diff: convert any rewrites to additive patches | controls | **highest** (functional safety) | Gate behind review |

### Execution notes
- Steps 1–5 are safe and fast; do them first in a single session.
- Steps 6–9 are mechanical but benefit from verifying the repo still builds between steps.
- Steps 11–13 need grep-based dependency checks before deletion-restoration (e.g. "does `manager` still reference athenad? does `selfdrive/car/card.py` still import from opendbc?").
- Step 15 is gated behind a human review — I will flag each `selfdrive/controls/` change rather than modify blindly.

---

## Unaudited Commits (post-2026-05-30)

The following 18 commits landed after the last audit sweep. They have **no corresponding `COMMIT_*_REVIEW.md` files** and need review.

| Commit | Subject | Scope | Risk | Review status |
|---|---|---|---|---|
| `82f453f04` | feat(x86): dual-backend ONNX Runtime + ARM RKNN inference | `system/inferenced/`, `selfdrive/modeld/` | **high** — new inference path for dev PC | ✅ audited — `COMMIT_82F453F4_REVIEW.md` — 3 HIGH bugs |
| `6e6a2c9ca` | fix(monod): use inference_backend() for x86/ONNX compatibility | `selfdrive/monod/` | medium | ✅ audited — `COMMIT_6E6A2C9C_REVIEW.md` |
| `06df38d6e` | feat(models): add dev-pc download type for ONNX models | `models/download_models.sh` | low | ✅ audited — `COMMIT_06DF38D6_REVIEW.md` |
| `36fd7a5cc` | docs: add DEV_PC_GUIDE.md and update audit timestamp | `docs/eop/`, `docs/upstream-audit/` | none | ✅ audited — `COMMIT_36FD7A5C_REVIEW.md` |
| `907cb47b8` | fix(coordinationd): eliminate duplicate RoadConstraint class | `selfdrive/coordinationd/` | low | ✅ audited — `COMMIT_907CB47B_REVIEW.md` |
| `e0bd42810` | test: add inference pipeline integration tests | `selfdrive/test/test_inference_pipeline.py` | low | ✅ audited — `COMMIT_E0BD4281_REVIEW.md` |
| `63f5b96dc` | fix(cereal): add inferenceJobRequest/Result to services.py | `cereal/services.py` | low | ✅ audited — `COMMIT_63F5B96D_REVIEW.md` |
| `edd691fb4` | docs(sim): document CARLA GPU requirement + MetaDrive | `docs/eop/` | none | ✅ audited — `COMMIT_EDD691FB_REVIEW.md` |
| `a5c25defd` | fix(sim): fix MetaDrive bridge startup | `tools/sim/` | low | ✅ audited — `COMMIT_A5C25DEF_REVIEW.md` |
| `3ac9acf80` | InferenceD Phase 3: Complete daemon integration + docs update | `selfdrive/gridd/`, `selfdrive/recordd/`, `system/inferenced/` | medium | ✅ audited — `COMMIT_3AC9ACF8_REVIEW.md` |
| `142c0ef24` | EOP: Hardware ID, UI resolution, and system switcher | `system/hardware/`, `selfdrive/ui/`, `system/manager/` | medium | ✅ audited — `COMMIT_142C0EF2_REVIEW.md` |
| `74a6ec915` | fix(docs): remove dangling references to deleted SESSION_COMPLETION_SUMMARY.md | `docs/eop/` | none | ✅ audited — `COMMIT_74A6EC91_REVIEW.md` |
| `d347fea13` | docs(dev-pc): add comprehensive dev PC testing guide | `docs/eop/DEV_PC_GUIDE.md` | none | ✅ audited — `COMMIT_D347FEA1_REVIEW.md` |
| `d2a00ddc4` | feat(inferenced): implement daemon job execution + IPC client mode | `system/inferenced/`, `cereal/` | **high** — IPC job execution is new | ✅ audited — `COMMIT_D2A00DDC_REVIEW.md` |
| `ddf6705b9` | chore(skills): tune Claude Code skills for EOP/openpilot codebase | `.claude/skills/` | none | ✅ audited — `COMMIT_DDF6705B_REVIEW.md` |
| `985e09b49` | update | 49 files across vehicled/tesla/, inferenced/, controls/, sim/ | **high** — massive undescribed commit | ✅ audited — `COMMIT_985E09B4_REVIEW.md` — 6 issues |
| `a19317ef3` | fix(schema+imports): resolve all Python import crashes blocking daemon startup | `cereal/`, `common/`, `selfdrive/` | medium | ✅ audited — `COMMIT_A19317EF_REVIEW.md` |
| `5785837b5` | test: add daemon import smoke test for all 22 critical EOP modules | `selfdrive/test/test_daemon_imports.py` | low | ✅ audited — `COMMIT_5785837B_REVIEW.md` |
| `1d5f050ef` | update | `docs/upstream-audit/` (bulk audit doc updates) | none | ✅ audited — `COMMIT_1D5F050E_REVIEW.md` |

**Plus 3 WIP commits in history:**
- `3610e2e21` WIP — inferenced refactor (51 files)
- `16ea9efe3` WIP — safety.py CAN ID additions, sbu_detection.py, camera_calibrationd updates
- `e11e33e8b` WIP — bsd.py, dlat.py updates

### Incomplete / TODO findings in unaudited code

| Location | Issue | Severity | Notes |
|---|---|---|---|
| `system/inferenced/hailo_hef.py:120` | `TODO: Implement actual Hailo inference using HailRT API` | ✅ fixed — `ddbcb58ab` — replaced broken `HailoRT` placeholder with real `VDevice`/`HEF`/`ConfiguredInferModel.infer()` API |
| `selfdrive/monod/monod.py:764` | `TODO: Serialize segmentation data` | 🟡 medium | Feature incomplete |
| `selfdrive/monod/calibration_fusion.py:516` | `TODO: add stereo depth` | 🟡 medium | Feature incomplete |
| `selfdrive/controls/lib/longcontrol.py:70` | `TODO: wire to plannerd / soundd for a 'resume required' alert` | 🟡 medium | TJA resume alert not wired |
| `selfdrive/car/` | Entire directory imports `opendbc` which was removed; dead code | 🟡 medium | Not used by EOP runtime (vehicled replaces it), but adds audit noise |
| `opendbc` / `opendbc_repo` submodules | Missing from `.gitmodules` | 🟡 medium | `selfdrive/car/` and debug tools reference it; either restore submodule or delete dead code |
| `985e09b49` commit message | Single word "update" for 49-file change | 🟡 medium | Commit should be split or message expanded |

---

## Fixes applied during this session

| Commit | Fix | Files |
|---|---|---|
| `90b54e00b` | bluetoothd: BLE TX interleave race, SPP socket send race, NCPSession idempotent start, param bytes→int TypeError | `system/bluetoothd/ble_gatt.py`, `bluetoothd.py`, `ncp_session.py`, `spp.py`, `CLAUDE.md` |
| `ec0c4e735` | manager: `globald` renamed to `coordinationd` in `process_config.py` (rename commit `64158f5b4` missed this reference) | `system/manager/process_config.py` |
| `f384f56c5` | Remove dead opendbc-referencing code (`selfdrive/car/`, debug tools, tests, process replay framework) | 56 files deleted, 6,473 lines removed |
| `ddbcb58ab` | Hailo backend: replace broken `HailoRT` placeholder with real Hailo Platform API; expand daemon smoke test 22→29; write code reviews for `82f453f04` + `985e09b49` | `system/inferenced/hailo_hef.py`, `selfdrive/test/test_daemon_imports.py`, `docs/upstream-audit/COMMIT_82F453F4_REVIEW.md`, `docs/upstream-audit/COMMIT_985E09B4_REVIEW.md` |
| *(this session)* | Simulator packer bit-positions: rewrite `_set_value` Motorola snake pattern; fix all `_pack_*` start_bit/size/scale to match `tesla_parser.py`; fix SCCM address 0x3c3→0x129 | `tools/sim/lib/simulated_car.py` |
| *(this session)* | `.pkl` metadata: regenerated from valid ONNX models — `driving_vision` output shape (1, 1576) with slices (plan, lanelines, road_edges, lead, desire_state, meta, pose, wide_camera, hidden_state); `driving_policy` output shape (1, 1000) with slices (plan, desire_state, pad) and input shapes (desire_pulse, traffic_convention, features_buffer) | `selfdrive/modeld/models/driving_vision_metadata.pkl`, `selfdrive/modeld/models/driving_policy_metadata.pkl` |
| *(this session)* | `openpilot-rk3588.service`: add missing `Conflicts=visionpilot.service` (rk3576 already had it) | `tools/systemd/openpilot-rk3588.service` |
| *(this session)* | `test_daemon_imports.py`: embed `OPENPILOT_STUB_PARAMS_PYX=1` in autouse pytest fixture so CI doesn't need external env var | `selfdrive/test/test_daemon_imports.py` |
| *(this session)* | `system/inferenced/client.py`: reduce IPC busy-poll 1000 Hz → 100 Hz (`time.sleep(0.001)` → `0.01`) | `system/inferenced/client.py` |
| *(this session)* | `system/inferenced/inferenced.py`: warn when multi-output model only serializes first output over IPC | `system/inferenced/inferenced.py` |
| *(this session)* | Code review files written for all 20 unaudited commits (`6e6a2c9ca`–`1d5f050ef`). See `docs/upstream-audit/COMMIT_*_REVIEW.md` (22 total including prior `82f453f04` + `985e09b49`). | `docs/upstream-audit/` |
| *(this session)* | **ONNX models fixed** — Replaced corrupted `driving_vision.onnx` / `driving_policy.onnx` with valid versions from dragonpilot `pre-build` branch; regenerated `.pkl` metadata from source. | `selfdrive/modeld/models/driving_vision.onnx`, `driving_policy.onnx`, `*_metadata.pkl` |
| *(this session)* | **Model blobs removed from git** — `git rm --cached` all `.onnx` + `.pkl` in `selfdrive/modeld/models/`; added `.gitignore`; files remain in working directory. | `selfdrive/modeld/models/.gitignore`, `selfdrive/modeld/models/*.onnx`, `*.pkl` |
| *(this session)* | **Dead docs cleanup** — Fixed 114+ broken internal doc links (wrong paths, missing files, malformed syntax); converted 49 dead code path references to plain text with `*(not implemented)*` annotations; fixed external repo links. | `docs/` (44 files) |
| *(this session)* | **Removed ExoPilot 02M (RK3576) platform support** — openpilot now supports RK3588 (ExoPilot 01M) only; 02M is VisionPilot's. Deleted `system/hardware/rk3576/`, `tools/rk3576_sdk/`, the 02M-only `tele_road` camera path, and rk3576-only docs; collapsed hardware registry/capability detection, `rknn_platform.py`, `camera_geometry.py`, `multi_camera_fusion.py`, and build system (`SConstruct`, `launch_openpilot.sh`) to RK3588-only. Relocated shared Rockchip ctypes bindings (RGA/MPP/RKNN) from `rk3576/rockchip/` to `system/hardware/rockchip/` since RK3588 depends on them too. | `system/hardware/`, `tools/rk3576_sdk/` (deleted), `selfdrive/gridd/camera_geometry.py`, `multi_camera_fusion.py`, `selfdrive/modeld/runners/rknn_platform.py`, `SConstruct`, `launch_openpilot.sh`, `common/core_config.py` |
| *(this session)* | **Removed dead camera-tier PCIe accelerator code** — Hailo-8/DX-M1 "camera tier" detection (`probe_pcie_accelerator`, `HardwareCapability.HAILO_8`/`DX_M1`) turned out to have no consumer left once the 02M-only `tele_road` path was gone: deleted `selfdrive/monod/hailo_detector.py` and monod's Hailo/tele_road code (collapsed to 2-camera RKNN-only), and removed the `hailo_present` BSD-chime gate in `controlsd.py` (stale pre-radar-refactor assumption). `sided`'s Hailo-8 usage for side-camera BSD is a separate, real 01M feature and was left untouched. | `selfdrive/monod/monod.py`, `hailo_detector.py` (deleted), `selfdrive/controls/controlsd.py`, `system/hardware/base.py`, `system/hardware/rk3588/hardware.py`, `system/manager/manager.py` |
| *(this session)* | **Removed RTK/ZED-F9P support** — `system/rtkd/` deleted; stripped ZED-F9P baud-negotiation/RTCM-injection code from `pigeond.py`. RK3588 (ExoPilot 01M) uses NEO-M8U, which has no RTK capability — ZED-F9P/RTK was ExoPilot 02M-only hardware. | `system/rtkd/` (deleted), `system/ubloxd/pigeond.py`, `common/params_keys.h`, `system/manager/process_config.py` |
| *(this session)* | **Restored navd/Valhalla on-device navigation** (partial revert of `bd17cf80e`) — On-device routing is a monetized FREE-tier feature per `docs/eop/SUBSCRIPTION_BUSINESS_MODEL.md` and a prerequisite for the Premium "Convoy/Follow Friend" feature (NavPilot), so it should not have depended entirely on phone-side routing. Restored `selfdrive/navd/`, re-added the `third_party/valhalla` submodule, and restored `--with-valhalla` SConstruct wiring + `EOPNavEnabled`/process_config/core_config entries. Deliberately did **not** restore the Cards/Map-Panel UI (`qt/cards/*`, `qt/maps/*`) — the map/route stay on the NavPilot phone app. | `selfdrive/navd/` (restored), `third_party/valhalla` (submodule re-added), `SConstruct`, `.gitmodules`, `common/params_keys.h`, `system/manager/process_config.py`, `common/core_config.py` |
| *(this session)* | **Added turn-by-turn maneuver overlay** — New `HudRenderer::drawNavInstruction` in the onroad UI draws an arrow (rotated per Valhalla maneuver modifier) + distance + street name from `navInstruction`, top-left of the camera view. No map or tiles rendered on-device — verified via full `scons selfdrive/ui/` build (links successfully). | `selfdrive/ui/qt/onroad/hud.cc`, `hud.h` |
| 2026-07-06 | **Completed docs sweep removing remaining ExoPilot 02M (RK3576) references** — Fixed `docs/INFERENCED_ARCHITECTURE.md` (wrongly described InferenceD as RK3576/RK3588 dual-platform; now RK3588-only, matching the already-collapsed `rknn_platform.py`). Added a "⚠️ SUPERSEDED" banner to `docs/eop/SYSTEM_SWITCHING.md` (same treatment as `MAP_PANEL.md`) — the switch.sh/dual-service concept it described was never built, contradicts CLAUDE.md's one-stack-per-board rule, and had an internal table contradiction. Full clean sweep confirmed with user 2026-07-06 (supersedes this file's earlier "leave historical records untouched" note below): also removed RK3576/02M content from the point-in-time status/history docs previously left alone — `docs/eop/00_Index/{IMPLEMENTATION_STATUS,STRATEGY,VISIONPILOT_GAP_ANALYSIS,OVERVIEW}.md`, the `PHASE{3,4,5,6}_*` reports, `SESSION_SUMMARY.md`, `INFERENCED_TASKS.md`, and the controller docs — including deleting completed-work entries like the "GPS-RK3576" status row, the "Phase 5: RK3576 Map Panel" planning writeup, and the "Task 5.1: RK3576 Hardware Test" procedure in `PHASE5_HARDWARE_READINESS.md`. Also swept `docs/eop/03_Software/Architecture/CAMERA_ISP_HDR_ARCHITECTURE.md`, `docs/eop/05_Features/MAP_PANEL.md`, `docs/eop/03_Software/Daemons/Enhanced/VOICE_PIPELINE.md`, `docs/eop/04_Integration/BLE_DESIGN.md`, and several more (see git diff for full list). Deliberately left alone: `docs/SCHEMA.md` (documents the DB schema's real `exopilot02m` source tag, used for legitimate cross-fleet data provenance — see next gap note) and comparison-table rows already correctly annotated "VisionPilot only / not supported by openpilot" (accurate, not misleading). | `docs/` (~25 files) |
| 2026-07-06 | **Consolidated triplicated camera mounting geometry, fixed a real drift** — `camera_geometry.py`'s `CameraArrayGeometry` (road/stereo_right at vehicle centerline, wide_road/stereo_left +80mm) and `hardware.py`'s `get_camera_array_config()` (all four MIPI cameras symmetric at ±40mm) disagreed on each camera's offset from centerline despite agreeing on the 80mm stereo baseline. Traced usage: `get_camera_array_config()`'s only consumer (`v4l2d.py`) never reads the disputed per-camera offsets, while `CameraArrayGeometry` has 6 real geometric-math consumers (calibration_storage.py, camera_calibrationd.py, multi_camera_fusion.py, lazy_bev.py, monod/calibration_fusion.py, camera_calibrator.py) and is internally self-consistent — consolidated around that data into `hal.platform.rk3588_camera_geometry`. Verified end-to-end with hal installed (positions/rotations/projections all correct) and the no-hal fallback (degrades to empty data, no crash). | `selfdrive/gridd/camera_geometry.py`, `system/hardware/rk3588/camera_config.py`, `system/hardware/rk3588/hardware.py` |
| *(this session)* | **BGT60TR13C radar4d port: unbroke the daemon, moved the driver to shared hal, fixed two real DSP bugs, added CFAR/AoA/tracking.** `radar4d.py` was crash-on-start (imported a nonexistent `hal.drivers.radar` — the module actually lives in the sibling `exopilot` repo's `hal` package, not this repo; the daemon's own duplicate at `system/radar4d/bgt60tr13c.py` was a fixed-but-unused fork of that same driver, never wired up). Deleted the duplicate; `radar4d.py` now imports the real `hal.drivers.radar` with a graceful idle (not crash) if `hal` isn't installed, gated behind a new `EOPRadar4DEnabled` param (was ungated, would crash-loop on every box without the sensor). Rewrote `hal`'s driver against Infineon's official `sensor-xensiv-bgt60trxx` C source (was based on the unofficial `micropython-radar-bgt60` port) — found and fixed a real FIFO burst-trigger byte bug (`0xBD` hardcoded vs. the correct `(fifo_addr<<1)`-derived value) via primary-source verification. Replaced the wrong-model 3-element-ULA beamforming DSP with real 2-D CA-CFAR + dual-baseline phase AoA (BGT60TR13C's RX array is L-shaped, not linear — Rx1/Rx3 gives azimuth, Rx2/Rx3 gives elevation), adding elevation output the old driver never had. Synthetic self-test (`hal/tests/test_radar_dsp.py`) caught two further real bugs before they'd have surfaced as silent accuracy loss on hardware: (1) a full complex FFT on the range axis produces a spurious mirror-image ghost detection with inverted azimuth/elevation for every real target, since the ADC is real-valued not I/Q — fixed with `rfft`; (2) `chirp_period_s = 1/(frame_rate_hz*n_chirps)` wrongly assumed zero dead time between chirps, collapsing max unambiguous velocity to under 2 m/s — fixed by deriving the real chirp period from the RTU timing register. Fixing (1) also revealed the driver's range-config sizing was itself wrong (a real n_samples=1024 only gives ~14m unambiguous range post-fix, under the 15m application target) — resized to n_samples=2048 with adc_div lowered to keep the velocity ceiling automotive-useful (~15 m/s) despite the larger sample count. Added confirm/drop hit-streak track hysteresis (`radar4d_tracker.py`, openpilot-specific, not shared) replacing every-frame ID reassignment. Schema gained `elevation`/`existenceProb` fields (additive, capnp ordinals `@5`/`@6`); `gridd._fuse_radar4d()` uses elevation as a clutter-rejection gate and blends `existenceProb` into its confidence boost. Cross-checked against `../visionpilot`'s concurrent, independent radar4d port (a fuller Autoware-style ROS2 node) — both sides converged on identical `hal.drivers.radar` API shape and identical n_samples/adc_div sizing, confirming consistency without needing to touch VisionPilot's actively-being-edited files. | `selfdrive/controls/radar4d.py`, `selfdrive/controls/radar4d_tracker.py` (new), `selfdrive/controls/tests/test_radar4d_tracker.py` (new), `selfdrive/gridd/gridd.py`, `selfdrive/gridd/tests/test_fuse_radar4d.py` (new), `cereal/custom.capnp`, `cereal/services.py`, `common/params_keys.h`, `system/manager/process_config.py`, `selfdrive/ui/qt/offroad/eop_panel.{h,cc}`, `system/radar4d/` (deleted), `docs/eop/bgt60_radar.md`; also `../exopilot/hal/hal/drivers/radar/{bgt60tr13c.py,dsp.py,__init__.py}`, `../exopilot/hal/hal/platform/rk3588_pins.py`, `../exopilot/hal/tests/test_radar_dsp.py` (new), `../exopilot/docs/02-HARDWARE/bgt60_radar.md` |

| *(this session)* | **radar4d lidar-style migration: Kalman tracker, object-level fusion, stereo geometry, calibration wizard.** Moved the pipeline from raw-CFAR-point fusion to an Autoware-inspired pointcloud→objects flow. `radar4d_tracker.py`: replaced alpha-beta-gamma with a constant-acceleration EKF (`KalmanTrackManager`) — adaptive SNR/range-dependent gains, covariance-scaled occlusion coasting (confirmed tracks survive 10 missed frames), and a wider re-acquisition gate after occlusion. `radar4d_lidar.py` (new): ground filter → DBSCAN clustering → PCA/L-shape shape estimation; `radar4d_geometry.py` (new): center-mounted radar ↔ stereo camera coordinate transforms with FOV gating; `radar4d_calibrate.py` (new): interactive intrinsic calibration wizard writing automotive-band (3/6/9/12m) LUT JSON. `gridd.py`: `_fuse_radar4d_objects()` consumes `Radar4DObject` clusters with a shape-aware (length/width/yaw) association gate and uses the cluster's own vRel/aRel; falls back to raw points when no objects are published. `cereal/custom.capnp`: added `Radar4DObject` struct and `objects` list to `Radar4D`. `cereal/services.py`: radar4d rate 10→20 Hz to match the camera pipeline. `radar4d.py`: 20 Hz preset with `n_chirps=64` and `high_speed_spi` for better velocity resolution. Tests: 78 passing across tracker, lidar, geometry, calibration, and gridd fusion. | `cereal/custom.capnp`, `cereal/services.py`, `selfdrive/controls/radar4d.py`, `radar4d_tracker.py`, `radar4d_lidar.py`, `radar4d_geometry.py`, `radar4d_calibrate.py`, `selfdrive/gridd/gridd.py`, `selfdrive/gridd/tests/test_fuse_radar4d.py`, `selfdrive/controls/tests/test_radar4d*.py` |
| *(this session)* | **radar4d rename: "lidar" identifiers → pointcloud.** `radar4d_lidar.py` → `radar4d_pointcloud.py`, `RadarLidarProcessor` → `RadarPointcloudProcessor` (daemon attr `lidar_processor` → `pointcloud_processor`), test file + class + method names likewise. Rationale: no lidar hardware exists in the system and `pointcloudd` already owns the stereo pointcloud meaning, so "lidar" identifiers misattributed the sensor; the Autoware-pipeline lineage is kept in docstrings/comments instead. Also fixed the doc's "treats the BGT60 as a sparse 4D lidar" framing. Tests: 76 passing across pointcloud, tracker, geometry, calibration, and gridd fusion; daemon + gridd imports verified. | `selfdrive/controls/radar4d_pointcloud.py` (renamed), `selfdrive/controls/radar4d.py`, `selfdrive/controls/tests/test_radar4d_pointcloud.py` (renamed), `docs/eop/bgt60_radar.md` |
| *(this session)* | **Unified camera+radar calibration storage: factory intrinsics owned by exopilot HAL, extrinsics by the application.** Added `hal/paths.py` (`calibration_root()` = `EOP_DATA_ROOT` → `/data` → `~/.comma/data`, mirroring `Paths.eop_data_root()`). Radar: persistence moved into `hal/drivers/radar/intrinsics.py` (`load_intrinsics`/`save_intrinsics`, same JSON schema); `radar4d.py` now loads via HAL; wizard `radar4d_calibrate.py` saves via HAL and its default output was fixed from `/data/eop/calibration/...` (raw env var) to `Paths.eop_data_root()/calibration/...` — previously the wizard's output landed where `radar4d.py` never looked. Camera: new `hal/drivers/camera/intrinsics.py` (`StereoIntrinsics` dataclass + npz `load_stereo_intrinsics`/`save_stereo_intrinsics`); consumers `gridd.py`, `pointcloudd.py`, `steamd/stereo_correction.py` (canonical path first, legacy fallbacks kept), and writer `tools/factory_calibration/calibrate_stereo.py` all route through it; `pointcloudd`'s hardcoded `/data/calibration` path fixed; dead `CALIBRATION_PATH` removed from `stereod.py`. Radar extrinsics (user-refined mount) stay at the application layer: `RadarMounting.load/save` JSON at `radar_extrinsics.json`, consumed by `gridd.py`. All HAL call sites degrade gracefully when `hal` isn't installed (dev PC). Tests: hal 10/10, controls 126/127 (pre-existing test_nslc failure), new storage roundtrip tests both repos. | `../exopilot/hal/hal/paths.py` (new), `../exopilot/hal/hal/drivers/radar/intrinsics.py`, `../exopilot/hal/hal/drivers/camera/intrinsics.py` (new), `../exopilot/hal/tests/`, `selfdrive/controls/radar4d.py`, `radar4d_calibrate.py`, `radar4d_geometry.py`, `selfdrive/gridd/gridd.py`, `selfdrive/pointcloudd/pointcloudd.py`, `selfdrive/steamd/stereo_correction.py`, `selfdrive/stereod/stereod.py`, `tools/factory_calibration/calibrate_stereo.py`, `docs/eop/bgt60_radar.md` |
| *(this session)* | **visionpilot calibration alignment + cross-repo naming consistency (`<sensor>_intrinsics/extrinsics`).** visionpilot: `radar4d_node` now loads intrinsics via the HAL store (empty `intrinsics_path` = canonical path — previously defaulted to `""` and NEVER loaded calibration); its wizard saves via HAL with the canonical default output; `stereo_matcher_node` load priority = param → HAL npz → legacy YAML → defaults (its shipped `config/stereo_calibration.yaml` had a schema mismatch — `left:`/`camera_driver_matrix` vs the node's `stereo_left:`/`camera_matrix` — so it silently ran on hardcoded defaults; fixed the YAML and marked it placeholder fallback), rectification maps now cached per image size (was recomputed every frame); `tools/calibration/stereo_calibrator.py` writes the HAL npz alongside YAML; `steamd/stereo_correction.py` (both apps) loads via HAL. Naming scheme unified across all three repos: files `<sensor>_intrinsics.*` (factory, HAL-owned: `radar_intrinsics.json`, `stereo_intrinsics.npz` — legacy `stereo_calibration.npz` still read as fallback) / `<sensor>_extrinsics.*` (app-owned: `radar_extrinsics.json`); classes `RadarIntrinsics`/`StereoIntrinsics`; functions `load_radar_intrinsics`/`save_radar_intrinsics`, `load_stereo_intrinsics`/`save_stereo_intrinsics`; old names kept as migration aliases. Tests: hal 16, openpilot 172 (1 pre-existing failure), visionpilot radar4d 23. | `../exopilot/hal/hal/drivers/{radar,camera}/intrinsics.py`, `../visionpilot/src/sensing/radar4d/`, `../visionpilot/src/sensing/stereo_matcher/`, `../visionpilot/src/steamd/steamd/stereo_correction.py`, `../visionpilot/tools/calibration/stereo_calibrator.py`, `../visionpilot/AGENTS.md`, `../visionpilot/docs/bgt60_radar.md`, `selfdrive/controls/radar4d*.py`, `selfdrive/gridd/gridd.py`, `selfdrive/pointcloudd/pointcloudd.py`, `selfdrive/steamd/stereo_correction.py`, `tools/factory_calibration/calibrate_stereo.py`, `AGENTS.md` |
| *(this session)* | **Radar fusion de-restricted to all cameras + radar environment inference (weather accel gate, drop-off guard).** Camera coverage: removed gridd's modelV2 lane-corridor rejection (`_radar4d_is_roadside_clutter` + `_R4D_LANE_REJECT_MARGIN_M`) from both radar fusion paths — radar data must map with stereo/wide/road cameras, not just the ego lane; extended `_radar4d_in_camera_fov` from road-only to the union of road + wide_road camera FOVs so wide-only returns (|az| 20-75°) survive. monod radar fusion was briefly added then REVERTED on user direction — fusion belongs at the gridd level, and gridd already merges monoDetections into `all_objects` before `_fuse_radar4d` annotates them. Weather: new `estimate_precipitation()` in `radar4d_pointcloud.py` (weak low-SNR returns scattered across azimuth bins = rain/snow signature, 0-1), EMA-smoothed in `radar4d.py`, published as `Radar4D.precipProb`; `plannerd` now subscribes radar4d and `longitudinal_planner._apply_weather_accel_limit()` scales max accel to a 0.4x floor at full precipitation (slippery-surface gating). Drop-off guard: new `detect_dropoff()` finds forward-corridor returns far below the expected road plane (z < -(mount 0.5 m + margin 0.6 m)) — evidence the clustering ground filter would discard, so it runs on raw points; radar4d publishes `dropOffHazard`/`dropOffDistM` after a 2-frame confirm streak; gridd `_merge_radar4d_dropoff()` appends a 0.95-confidence `dropoff` object and marks the costmap at cost=1.0 (hard limit) across the corridor, merging with the camera road-surface detection where it can miss (night/glare). capnp: 3 new fields on `Radar4D` (precipProb, dropOffHazard, dropOffDistM); cereal rebuilt. Pre-existing issue noted, not fixed: `modeld/runners/rknn_platform.py` hard-imports `hal.tuning`, breaking monod import on dev PCs. Tests: 181 passed (2 pre-existing failures); new tests for precipitation/drop-off detection, weather accel gate, drop-off costmap merge, FOV union; obsolete lane-reject test replaced with an out-of-lane-keep test. | `cereal/custom.capnp`, `selfdrive/controls/radar4d.py`, `selfdrive/controls/radar4d_pointcloud.py`, `selfdrive/controls/plannerd.py`, `selfdrive/controls/lib/longitudinal_planner.py`, `selfdrive/gridd/gridd.py`, `selfdrive/monod/monod.py` (reverted), `selfdrive/controls/tests/test_radar4d_pointcloud.py`, `selfdrive/controls/tests/test_weather_accel_gate.py` (new), `selfdrive/gridd/tests/test_fuse_radar4d.py` |
| *(this session)* | **Autoware radar+lidar upgrades: Bayesian existence, ghost filter, fusion gates, cluster split, temporal shape filter.** Radar-side (from autoware_universe radar packages + multi_object_tracker): (1) `radar4d_tracker.py` — replaced the flat ±step existence accumulator with Autoware `tracker_base`'s Bayesian update (hit: `p'=p·TPR/(p·TPR+(1−p)·FPR)`, TPR=0.8/FPR=0.2, computed 0–1 and stored on the 0–100 capnp scale; miss: 0.5 s half-life decay scaled by measured dt) and replaced the fixed 10-frame occlusion coast with a hard 1.0 s wall-clock limit plus early expiry at p<1.5 (0.015×100). (2) `radar4d.py` — crossing-yaw ghost filter from `radar_crossing_objects_noise_filter`: drop confirmed tracks moving >1.5 m/s with `|cos(heading−bearing)| < cos70°` (ego-turn clutter). (3) `gridd.py::_fuse_radar4d_objects` — two-phase association (best-score/nearest-center cluster wins per stereo object instead of last-write-wins, from `radar_fusion_to_detected_object`) plus a velocity-consistency gate (`_R4D_VEL_ASSOC_GATE_MPS=3.0`): if the stereo object already has a vRel disagreeing with the radar cluster, keep the confidence boost but skip the velocity/shape attach. Lidar-side (from autoware_shape_estimation / euclidean_cluster / vehicle_tracker): (4) `radar4d_pointcloud.py` — oversized clusters (PCA principal-axis extent >8 m, e.g. guardrail+vehicle merged blob) are bisected at the median along the principal axis, depth-bounded (`splitOversizedClusters`). (5) `radar4d_tracker.py` — `_merge_shape_metadata`: per-frame cluster length/width/height now run through a dual-rate EMA (jump vs stable gain, `UnstableShapeFilter`) with plausibility clamps (≤12 m length, ≤3.5 m width/height), and yaw through a π-ambiguity-normalized EMA (`normalizeYaw`) so 180°-flipped L-shape fits never swing the tracked yaw. Deliberately skipped: twist-yaw fusion gate (`Radar4DObject` has no vx/vy twist fields — documented as future capnp extension), tracker-seeded L-shape window search (needs pre-association re-architecture), GIoU association (current shape-aware gate adequate at this sparsity), voxel downsampling / ray-ground-filter / static-clutter map (need dense returns we don't have). ABGTrackManager left on the legacy step accumulator intentionally (Kalman is the default). Tests: 158 passed in controls/tests + 25 in gridd/tests (only pre-existing test_nslc + device-file depth failures); new tests for Bayes/half-life/expiry, ghost filter, best-pick + velocity gate, cluster split, shape EMA + yaw guard. | `selfdrive/controls/radar4d_tracker.py`, `selfdrive/controls/radar4d.py`, `selfdrive/controls/radar4d_pointcloud.py`, `selfdrive/gridd/gridd.py`, `selfdrive/controls/tests/test_radar4d_tracker.py`, `selfdrive/controls/tests/test_radar4d.py`, `selfdrive/controls/tests/test_radar4d_pointcloud.py`, `selfdrive/gridd/tests/test_fuse_radar4d.py` |
| *(this session)* | **Radar4D 3-step weather severity spread to all ADAS risk levers.** New `Radar4D.weatherSeverity` (0=clear/1=light/2=moderate/3=heavy) from `classify_weather_severity()` in `radar4d_pointcloud.py`, combining precipProb + new wiper-motion detection (periodic azimuth sweep signature in near-field returns → `Radar4D.wiperOn` + sweep rate) and windshield contamination detection (persistent near-field attenuation/shadow → `Radar4D.glassContaminated`); EMA/streak state owned by `radar4d.py`. `longitudinal_planner._apply_weather_severity_limit()` scales max accel by `WEATHER_ACCEL_SCALE` (1.0/0.8/0.6/0.4). `rcd.py`: `update()` split into `_update_camera()` + `_radar_weather_limit()` (`RADAR_WEATHER_LIMITS_MS` = 0/0/20/12 m/s — light rain uncapped since the accel gate covers it, heavy ≈ WET limit); radar limit only tightens, never loosens, and works with no camera source (night/glare). `aeb.py`: new `AEB.apply_weather_margins(severity)` scales RSS `min_brake` (×1.0/0.9/0.8/0.65), `reaction_time` (+0/0.1/0.25/0.5 s), and `BrakingController.TTC_PARTIAL`/`TTC_FULL` (×1.0/1.1/1.25/1.5) from stored bases; applied in `update()` from `sm['radar4d']`. `controlsd.py` now subscribes `radar4d`. capnp: 4 new `Radar4D` fields (weatherSeverity, wiperOn, wiperSweepRate, glassContaminated); cereal rebuilt. Tests: 208 passed (2 pre-existing failures); new `test_rcd_radar_limit.py` + `test_aeb_weather_margins.py` (10 tests). | `cereal/custom.capnp`, `selfdrive/controls/radar4d.py`, `radar4d_pointcloud.py`, `lib/longitudinal_planner.py`, `lib/rcd.py`, `lib/aeb.py`, `controlsd.py`, `selfdrive/controls/tests/test_rcd_radar_limit.py`, `test_aeb_weather_margins.py`, `test_weather_accel_gate.py` |
| 2026-08-13 | **RK3588/RK3576 runtime hardening: RKNN fp16 scoping, driver-version check, dead-code removal, manager race fix, camera shutdown grace, tici hardware removal.** Compared a proven RK3588 production runtime against `dev/EOP10`. `system/inferenced/rockchip_npu.py`: `data_type="float16"` cast now scoped to a `_FP16_MODELS` allowlist (`driving_vision`, `driving_policy` only — other backend consumers like sceneseg/ppliteseg have unverified quantization and must keep RKNNLite's uint8 default); added `_check_driver_version()` logging `RKNNLite.get_sdk_version()` and warning below `MIN_RKNPU_DRIVER_VERSION="0.9.6"` (observed driver bugs below this on production hardware). `selfdrive/modeld/runners/rknn_platform.py`: removed `NPUMask` enum (duplicated RKNNLite's own core-mask constants), `validate_core_mask`, `get_available_cores`, `get_task_core_mask`/`get_npu_core_mask` alias, and the entire unused TOPS-budget calculator (`get_safe_budget_tops`, `can_fit_on_core`, `get_core_headroom_tops`, `recommend_sharing`) — zero callers repo-wide (verified by grep); kept only the API `monod.py` actually calls (`get_platform_npu_config`, `.is_core_available`, `.is_rk3588`, `.core_count`), confirmed byte-identical core-mask output (`driving_vision`→0x1, `policy`→0x4) before/after. `system/manager/manager.py`: split the single `write_onroad_params` call into `onroad_transition`/`offroad_transition` so `IsOnroad` publishes *before* `ensure_running()` starts onroad processes and `IsOffroad` publishes *after* — closes a race where consumers could observe offroad while vision processes were still tearing down (ported from a proven production commit). `system/manager/process.py`: added `CAMERA_PRODUCER_STOP_TIMEOUT_S=15s` for `v4l2d`/`uvcd` (hold VisionIPC buffers other daemons read from) vs. the default 5s grace before SIGKILL. Removed `system/hardware/tici/` (Qualcomm/comma-device code not applicable to RK3588 — kept only `pins.py`/`__init__.py`, which `test_pandad.py`/`test_pigeond.py` import under `@pytest.mark.tici` gating), and the now-dead `agnos_init`/`/AGNOS` codepath in `launch_chffrplus.sh` and the `tici/id_rsa` default in `tools/scripts/ssh.py`. `docs/eop/EOP.md`: corrected the RK3588 platform status row, which claimed "✅ Production" despite the code never having run on real hardware — see `docs/eop/RK3588_HARDWARE_VALIDATION_CHECKLIST.md` (new, enumerates exactly what needs real-hardware confirmation, ordered cheapest-first) and `docs/eop/RKNN_the proven stack_PROVENANCE.md` (new, full fix-by-fix reasoning against the proven stack source), `docs/eop/DEVICE_FALLING_DETECTION.md`, `docs/eop/PROCESS_LIFECYCLE_HARDENING.md` (new, this entry's process.py/manager.py rationale), `docs/eop/TICI_DEAD_CODE_REMOVAL.md` (new). All of it is unvalidated against real RK3588 hardware — see the checklist doc before trusting any of it in production. | `system/inferenced/rockchip_npu.py`, `selfdrive/modeld/runners/rknn_platform.py`, `system/manager/manager.py`, `system/manager/process.py`, `system/hardware/tici/` (14 files deleted), `launch_chffrplus.sh`, `tools/scripts/ssh.py`, `docs/eop/EOP.md`, `docs/eop/RK3588_HARDWARE_VALIDATION_CHECKLIST.md` (new), `docs/eop/RKNN_the proven stack_PROVENANCE.md` (new), `docs/eop/DEVICE_FALLING_DETECTION.md` (new), `docs/eop/PROCESS_LIFECYCLE_HARDENING.md` (new), `docs/eop/TICI_DEAD_CODE_REMOVAL.md` (new) |

---

## Remaining gaps (action required)

- **Fleet-interop code intentionally still references rk3576/exopilot02m — this is correct, not a leftover.** `selfdrive/controls/lib/eop_utils.py` (`detect_exopilot_platform`), `selfdrive/controls/lib/surface_quality_db.py` + `selfdrive/surfaced/surface_detector.py` (DB `CHECK` constraints), and `selfdrive/locationd/calibration_storage.py` (`_parse_visionpilot_yaml`) all read/tag data that legitimately originates from VisionPilot (02M) devices for cross-fleet data merging — not dead platform-detection branches. Confirmed with user 2026-07-06: leave as-is.
- ~~**"Convoy/Follow Friend" feature gap identified, not built.**~~ **Resolved 2026-08-03.** `navd.py` already handles a continuously-updating `NavDestination` param (10m distance gate + backoff on Valhalla queries — no planner changes needed). `bluetoothd/protocol.py`/`ncp_session.py` now implement `CMD_CONVOY_LEAD`/`CMD_CONVOY_CANCEL` (0x70/0x71), advertised via `'convoyFollow'` in `supportedServices` so NavPilot capability-gates the dedicated path vs. the `CMD_NAVIGATE`/`CMD_CANCEL_NAV` fallback for older firmware. See `docs/eop/04_Integration/BLE_DESIGN.md` and NavPilot's `docs/architecture/NCP_V41_PROTOCOL.md`.

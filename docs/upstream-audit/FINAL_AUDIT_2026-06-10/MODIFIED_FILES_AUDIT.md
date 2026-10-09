# Modified-Files Audit (hunk-level) — `c085b8af1` → `88a04bc76`

Verdicts: **KEEP** / **REVERT** / **MIXED** (partial revert) / **NEEDS-DECISION**.
Every REVERT/MIXED row becomes an entry in `REVERT_LOG.md` when applied (Phase 3).

## §meta — repo meta / dev-machine files (step 2.1)

| path | verdict | justification / action |
|---|---|---|
| `.claude/skills/**` (7 files) | **REVERT** | Dev-machine Claude workflow skills; same class as prior `a688630af` dev-file removal. Untrack (keep on disk), ignore via `.gitignore`. |
| `switch.sh` | **REVERT** | Ops helper that switches systemd services between sibling repos (references non-EOP product `visionpilot`); not part of openpilot port. Untrack. |
| `README.md` | **MIXED** | EOP fork banner: KEEP intent, but hunks contain **encoding corruption (mojibake)**: `·`→`-+` (4×), `attorneys’`→`attorneysG��`, `�`/`?` in banner text. Rewrite banner hunk with correct UTF-8; restore corrupted upstream chars. |
| `RELEASES.md` | **REVERT** | Edits a 2019 upstream changelog entry to mention EOP `imud` — historical record must not be rewritten. Restore upstream bytes. |
| `uv.lock` | **MIXED** | Regenerated for pyproject changes (metadrive/qrcode dropped, pyopencl added — consistent) **but stale**: still lists `aiortc` + `pyaudio` as openpilot deps though pyproject removed them. Action: `uv lock` regen (Phase 3); if uv unavailable on this PC → NEEDS-DECISION. |
| `pyproject.toml` | **MIXED** | Dep changes justified (webrtc/teleoprtc removal, pyopencl for Mali G52, metadrive drop). Fix: stale `[tool.pytest.ini_options] testpaths` entries `system/updated` + `tools/cabana` point at deleted dirs. |
| `.gitattributes` | **KEEP** | LFS filter removal — documented no-LFS constraint. |
| `.gitignore` | **MIXED** | EOP artifact ignores justified (model blobs, UI binaries, submodule build dirs). Cleanup: drop `STUB_COMPLETION_TRACKING.md` (dev-session residue) and duplicate `__pycache__/` (upstream already has it via `**/__pycache__`? verify; if duplicate, drop). Add `.claude/` when untracking skills. |
| `.gitmodules` | **KEEP** | panda/opendbc/teleoprtc/tinygrad submodules removed (no panda HW; tinygrad unused on RK); RK-port submodules added (mpp, rga, rknpu2, hailort, arm_compute, clblast, valhalla, udsoncan, isotp, pygnssutils, carla[update=none]). **Current dependency correction:** the exact msgq gitlink `0e1ec5eb42404bfed9f5ad6ca06f3044488b3a15` is publicly reachable from `commaai/msgq`, so `.gitmodules` now uses that upstream repository; no msgq source or gitlink changed. |
| `.python-version` (new) | **KEEP** | uv python pin 3.12 matching RK userspace; 1 line, low risk. |
| `Jenkinsfile` | **KEEP** | Additive `rk3588 tests` stage; upstream stages untouched (DELTA_AUDIT step 7). |
| `.github/pull_request_template.md` | **KEEP** | Car-port checklist → vehicled (Tesla-only) checklist; fork-correct. |
| `.github/workflows/badges.yaml` (D) | **KEEP** (deletion) | comma.ai-infra cron job; noise in fork CI. |
| `.github/workflows/selfdrive_tests.yaml` | **KEEP** | Removes `simulator_driving` job (metadrive dep dropped). |
| `panda` (submodule, D) | **KEEP** (deletion) | No panda hardware; socketd/SocketCAN replaces it. |
| `rednose_repo` (pin bump) | **KEEP** | Fast-forward to 2 genuine upstream commaai commits (`6ccb8d0`, `7ffefa3`). |
| `tinygrad`, `tinygrad_repo`, `teleoprtc`, `teleoprtc_repo` (D) | **KEEP** (deletion) | Heavy/unused submodules removed for edge deployment ([INFRA] commit). |
| `site_scons/valhalla_build.py` (new) | **KEEP** | Used by `SConstruct:367`; ARM64 Valhalla routing build for nav. |

**Defects found by this step (fix in Phase 3):** README mojibake, RELEASES.md history edit, stale uv.lock (aiortc/pyaudio), stale pytest testpaths (updated/cabana), gitignore residue, tracked dev-machine files.

## §cereal — messaging schema (step 2.2a)

Automated ordinal-stability scan (`parse all 'name @N' per struct scope, upstream vs EOP`):
`log.capnp` 23 violations; `custom.capnp`/`legacy.capnp` 0 (purely additive — clean).

| path | verdict | justification / action |
|---|---|---|
| `cereal/log.capnp` | **MIXED** | 146 EOP Event members + EOP structs: KEEP. **Violations to fix (Phase 3 surgery):** (a) 7 upstream Event union members DELETED (`userBookmark@93`, `bookmarkButton@148`, `audioFeedback@149`, `customReserved0–3@107–110`) forcing (b) 14 renumberings (`accelerometer 98→97`, `soundPressure 103→209`, …) and (c) `ControlsState.curvatureStateDEPRECATED 65→68`, `personalityDEPRECATED 66→69`. Renumbering = wire-format break vs upstream replay/tooling for zero port benefit; DELTA_AUDIT step 3 claimed this was reverted but it persists. Fix: restore upstream members at original ordinals, move EOP additions to ≥150 contiguous block. No source-code impact (capnp access is by name); no fleet logs exist yet. |
| `cereal/car.capnp` | **MIXED — contains HIGH crash bug** | Symlink→standalone copy: KEEP (opendbc submodule removed). **BUG (guaranteed boot crash):** copy is from an *older* opendbc generation ending at `@50`+`speedLimit@51`, but EOP *code* is from the newer generation: `card.py:182` assigns `CS.vCruise`, `controlsd.py:382` reads `vCruiseCluster`, `carstate.py:205/210` set `stockLkas`/`invalidLkasSetting`, `selfdrived.py:294` reads `vCruise`, `hud_renderer.py:86`, `mcapd.py:101` — none of these fields exist in the schema → pycapnp raises at runtime. `speedLimit@51` collides with opendbc `espActive@51`. Fix: restore opendbc-pin fields `@51–60` verbatim, move `speedLimit→@61`; restore `ButtonEvent.Type.lkas@6`/`mainCruise@8` (unused in py but needed for enum parity). Justified deviations KEPT: old-style `CarEvent` (vs `OnroadEventDEPRECATED`) and `RadarData.errors@0:List(Error)` — EOP radar/event code consistently uses the old generation; renaming is wire-identical or fork-internal. Documented in file header. |
| `cereal/custom.capnp` | **KEEP** | +1011 lines EOP structs in fork-reserved slots — exactly the intended fork mechanism; 0 violations. |
| `cereal/services.py` | **MIXED** | 82 EOP services appended + dict reordered. Restore deleted upstream entries `customReservedRawData0–2` (goes with customReserved restoration). Deleted `driverEncodeData`/`livestreamDriverEncode*`: NEEDS-DECISION — `driverEncodeIdx` kept but its Data stream deleted (inconsistent; check loggerd camera config in §system). `navRoute (True,0.)→(True,0.,-1)`: verify Service tuple semantics for decimation=-1. |
| `cereal/SConscript`, `cereal/README.md` | **KEEP** | Trivial (build for new schema, doc note). |

## §common — core utilities (step 2.2b)

| path | verdict | justification / action |
|---|---|---|
| `common/params_keys.h` | **MIXED** | Keys themselves clean: 283 EOP keys added, 0 upstream attr changes, 5 dropped keys (`AlwaysOnDM`, `IsDriverViewEnabled`, `IsRhdDetected`, `RecordFront`, `RecordFrontLock`) verified unreferenced (DM stack removed) — KEEP all key changes. **Churn to revert:** whole-file style rewrite from initializer-list to `m.emplace()` lambda with no conditional logic — pure format churn turning ~283 added lines into a 408/125 rewrite. Phase 3: restore upstream initializer-list format, upstream block byte-identical, EOP keys appended. |
| `common/api.py` | **KEEP** | Stubbed (returns None) — EOP has no comma cloud. athenad/uploader not in `process_config.py` (dormant), so stub is safe. |
| `common/realtime.py` | **KEEP** | RK3588 big/little core constants + `set_core_type()`. `config_realtime_process` signature changed (cores→dt, affinity moved to `core_config.py`) — verified ALL 12 callers pass DT floats; deviation documented in docstring. |
| `common/util.h/.cc`, `common/swaglog.cc`, `transformations.pyx` | **KEEP** | Small additive helpers / missing `<cstring>` include. |
| `common/transformations/camera.py` | **MIXED — D11** | EOP rk3588/rk3576 sensor configs + `get_device_camera_config()` helper: KEEP. **BUG:** upstream `DEVICE_CAMERAS` entries (`tici`/`neo`/`pc`/`unknown` + tizi/mici products) deleted, but `modeld.py:521` does direct `DEVICE_CAMERAS[(deviceType, sensor)]` → KeyError when deviceType isn't rk* (PC/sim/replay of comma routes). Fix: restore upstream entries additively. |
| `common/transformations/model.py` | **KEEP** | DM intrinsics removed; verified zero references remain. |

## §system — daemons & HAL (step 2.2c)

Static screens over ALL 401 changed .py files: `py_compile` 0 failures; `ruff F821` (undefined names) → 8 real bugs (defect D14), 0 mojibake outside README.

| path | verdict | justification / action |
|---|---|---|
| `system/hardware/__init__.py`, `registry.py` (new) | **MIXED — D13** | RK platform registry + capability flags: KEEP (incl. `TICI = ROCKCHIP` compat alias). **BUG:** `PlatformRegistry` registers only rk3588/rk3576; `detect()` defaults to `'rk3588'` when no device tree → on dev PC `HARDWARE=RK3588Hardware`, `PC=False`; preserved `pc/hardware.py` `Pc` class is dead code, `HARDWARE=pc` env raises ValueError. Verified by live import. Fix: register `Pc` as `'pc'`, `detect()` falls back to `'pc'` (upstream semantics); board behavior unchanged (device tree present there). |
| `system/hardware/hardwared.py`, `base.py`, `hw.py/.h` | **KEEP** | RK power-rail/thermal rework (COMMIT_93374F4C review covered). Deleted upstream helpers (`set_offroad_alert_if_changed` etc.) verified unreferenced. tici/ HAL preserved (only `tests/test_power_draw.py` deleted — tici-bench-specific); pc/ preserved (+9). |
| `system/ubloxd/pigeond.py` | **KEEP** | Rewrite for EOP GNSS (NEO-M8U on RK3588, ZED-F9P RTK on RK3576); comma GPIO/tty path replaced. Hardware-port justified. |
| `system/manager/process_config.py` | **KEEP** (notes) | 29 upstream daemons removed (camerad/pandad/DM/athena/updated/webrtc... all matching documented architecture), 32 EOP daemons added, run-states only_onroad→ignition_on. Notes: `ui` watchdog_max_dt=5 now unconditional (upstream: None on PC) — LOW, watch for PC ui kills; `encoderd` no longer launched anywhere → modified `encoderd.cc`/`v4l_encoder.cc` are dormant build-only code (recordd owns recording) — LOW. |
| `system/sentry.py` | **KEEP** | DSNs blanked — no external crash reporting from fork. |
| `system/manager/manager.py`, `build.py` | **KEEP** | Offline dongle-id from eMMC CID; build tweaks. |
| `system/loggerd/*` (loggerd.h, deleter.py, encoderd.cc, v4l_encoder.cc, test_loggerd.py) | **KEEP** (note) | MCAP path + EOP camera streams. `test_encoder.py` has F821 `record_front` (in D14). `driverEncodeIdx`-kept/`driverEncodeData`-deleted inconsistency (D3) traced: no driver cam in EOP loggerd config — resolve D3 by deleting `driverEncodeIdx` too OR restoring both; prefer restoring upstream entries (1-line each, dormant). |
| `system/ui/spinner.py`, `system/micd` move | **KEEP** | Trivial platform adaptations. |

## ⏸ RESUME POINT (saved 2026-06-11, quota checkpoint)

**Next actions, in order:**
1. **§common finish:** `common/transformations/camera.py` + `model.py` diffs (verify tici/pc camera configs preserved).
2. **§system (step 2.2c):** modified files by size: `pigeond.py` (257/239 — rewrite for non-ublox GPS?), `hardwared.py` (253/480), `hardware/base.py` (225/223), `process_config.py` (154/80), `hw.h` (126/22), `hw.py`, `hardware/__init__.py` (67/11 — check PC/TICI HAL preserved per CLAUDE.md), `loggerd.h` (92/18), `deleter.py`, `encoderd.cc`, `v4l_encoder.cc`, `manager.py`, `build.py`, `spinner.py`, `test_loggerd.py`, `system/sentry.py`, `system/ubloxd`. Open question from §cereal: `driverEncodeIdx` service kept but `driverEncodeData` deleted — check loggerd camera/encoder config for driver-cam intent (note: `system/loggerd/loggerd.py` does not exist; encoder config is in `loggerd.h`/`encoderd.cc`).
3. **§selfdrive (2.2d):** controls (11 files — xref `CONTROLS_AUDIT.md`), locationd (5), selfdrived (3), modeld (3, modeld.py 578/317), test (3), debug (2), `selfdrive/SConscript`, `pandad/__init__.py`.
4. **§ui (2.2e):** 80 modified files in `selfdrive/ui` — sample-audit raylib/qt split; check upstream files only touched where needed.
5. **§tools (2.2f):** sim (12), joystick (3), replay (2), camerastream (2), lib/logreader, clip, car_porting, `scripts/lint`.
6. **Step 2.3:** deleted files (316) — map to DELTA_AUDIT justifications (cabana/camerad/webrtc/car/updated/bodyteleop known); fresh verdicts for unmapped.
7. **Step 2.4:** added files (685) — reachability (imports/process_config/SConscript references).
8. **Step 2.5:** binary assets — verify M assets are exactly LFS-pointer→upstream-content (sha256 vs upstream LFS objects; upstream remote available).
9. **Phase 3:** apply reverts (see Defect Register below) as `[AUDIT-REVERT]` commits → target tree T.
10. **Phases 4–6:** rebuild ~12 topic commits from T (plan: `/home/vcar/.claude/plans/crystalline-hopping-flurry.md`), verify tree identity, move EOP10, update DELTA_AUDIT.md, ask user before force-push.

**Defect register so far (Phase 3 fix list):**
| # | severity | file | defect |
|---|---|---|---|
| D1 | **HIGH (boot crash)** | `cereal/car.capnp` | CarState missing opendbc fields @51–60 (`vCruise`, `vCruiseCluster`, `stockLkas`, `invalidLkasSetting`…) referenced by `card.py:182`, `controlsd.py:382`, `selfdrived.py:294`, `carstate.py:205/210`, `hud_renderer.py:86`, `mcapd.py:101`; `speedLimit@51` collides with `espActive@51`. Fix: add @51–60 per opendbc pin `4b203ff5d`, move `speedLimit→@61`; restore `ButtonEvent.Type.lkas@6`/`mainCruise@8`. Ref copy: `/tmp/opendbc_car.capnp` (refetch: `curl raw.githubusercontent.com/commaai/opendbc/4b203ff5d1ad867de127de6b27382ba73e6e31a7/opendbc/car/car.capnp`). |
| D2 | MED (compat) | `cereal/log.capnp` | 23 ordinal violations: restore 7 deleted Event members (incl. `customReserved0–3`), un-renumber 14 + 2 ControlsState fields; EOP additions → ≥150 block. |
| D3 | MED | `cereal/services.py` | Restore `customReservedRawData0–2`; decide `driverEncodeData`/`livestreamDriverEncode*`; verify `navRoute` decimation `-1` semantics. |
| D4 | MED | `README.md` | Invalid UTF-8 (CP1252 `0x97` em-dash, `?` arrow, mangled `’`). Rewrite banner hunk in UTF-8, restore other hunks to upstream bytes. Only file repo-wide failing `iconv` UTF-8 check. |
| D5 | LOW | `RELEASES.md` | 2019 changelog entry edited — restore upstream bytes. |
| D6 | MED | `uv.lock` | Stale: still resolves `aiortc`+`pyaudio` though pyproject dropped them. Regen needs `uv` (NOT installed on this PC) — NEEDS-DECISION or install uv. |
| D7 | LOW | `pyproject.toml` | testpaths reference deleted dirs `system/updated`, `tools/cabana`. |
| D8 | LOW | `.gitignore` | Residue: `STUB_COMPLETION_TRACKING.md`; add `.claude/` when untracking skills. |
| D9 | LOW | `.claude/skills/**`, `switch.sh` | Untrack dev-machine files (keep on disk). |
| D10 | LOW (churn) | `common/params_keys.h` | Restore initializer-list format (drop emplace-lambda rewrite). |
| D11 | **HIGH (PC/sim crash)** | `common/transformations/camera.py` | Upstream `DEVICE_CAMERAS` entries deleted; `modeld.py:521` direct lookup KeyErrors for non-rk deviceType. Restore upstream entries additively. |
| D12 | — | (merged into D14) | |
| D13 | **HIGH (dev-PC misdetection)** | `system/hardware/registry.py` | `detect()` defaults to `rk3588` on PC; `Pc` HAL unregistered/dead. Register `'pc'` + fall back to it without device tree. |
| D14 | **HIGH×3, MED×5 (NameError)** | 8 files, ruff F821-verified | Missing imports: `calibration_fusion.py` (`CameraGeometry`/`CameraPosition` — **import-time crash**, no future-annotations), `surfaced.py:1076` (`logging` — crash at main()), `alert_renderer.py:87` (`TICI` — crash in alert path), `pairing_agent.py:237` (`time`), `onnx_backend.py:142` (`cloudlog`), `rockchip_mpp.py:225` (`np`, annotation-only — benign w/ future-annotations but fix), `mcapd.py:517` (`sys` — SIGINT handler), `test_encoder.py:80` (`record_front` undefined). |
| D15 | MED | 4 files | Dangling imports into deleted/moved modules: `test_soundd.py`→`selfdrive.ui.soundd` (moved), `athenad.py`→`system.camerad.snapshot` (athena in pytest testpaths → collection error), `system/ui/feedback/feedbackd.py`→`system.micd` (moved), `tici/agnos.py`→`system.updated.casync`. See `DELETED_FILES_AUDIT.md`. |
| D16 | NEEDS-DECISION | 13 modules | Confirmed orphan (dead) EOP modules — delete (recoverable from git) or wire. See `ADDED_FILES_AUDIT.md`. |
| D17 | MED (silent UI) | `third_party/bootstrap/bootstrap-icons.svg` | Emptied to 67-byte stub; `bootstrapPixmap()` → blank icons. Restore from upstream LFS oid; also restore 6 other generation-mismatched assets (docs SVGs, inter-ascii.ttf). |

**Session git state at checkpoint:** branch `EOP10`, commits on top of `88a04bc76`: `1ffcca147` (phase 0–1), `dc5ef0a37` (phase 2 partial), + this checkpoint commit. These `[AUDIT]` commits fold into the `[DOCS]` topic commit at Phase 4.

# Deep Linkage Audit — 2026-06-12 (post-rebuild follow-up)

Goal: prove every piece of EOP-added code is **linked and runnable** — wiring chains
(process_config → module → messaging services → capnp schema → params keys → build files)
all resolve; no dead references; no latent runtime crashes.

## Checks run (all machine-verified)

| check | scope | result |
|---|---|---|
| process_config wiring | 48 PythonProcess + 2 NativeProcess | ✅ all module paths/binaries resolve |
| AST import resolution | 646 files, 1,389 `openpilot.*` imports | 9 module errors, 7 name errors → triaged below |
| Service-name cross-check | every PubMaster/SubMaster/new_message vs `services.py` | 7 unknown names → **D19** |
| capnp field assignments | 102 `msg.<member>.<field>=` sites vs schema | 16 violations → **D20** |
| Param keys | 312 used keys vs `params_keys.h` | 15 missing (manager boot-blocker!) → **D21** |
| Deep ruff (bugbear/PLE/E71x) | 321 added .py files | 0 real bugs (only unused-var/import noise) |
| SConscript source refs + launch/systemd | all build/launch files | ✅ (only generated-file false positives) |

## Defects fixed (continuation of D1–D18 register)

| # | severity | what | fix |
|---|---|---|---|
| D19 | **HIGH (boot crash ×5 daemons)** | `hardwared`→`powerState`, `wdgd`→`wdgState`, `rtcd`→`rtcStatus`, `imud`→`temperature`, `networkd`→`networkState` used in PubMaster/new_message but absent from `services.py` (and 3 absent from Event union); `recordd` subscribed never-published `socketdState` | services.py +5 entries; log.capnp +`RtcStatus`/`ImuTemperature`/`NetworkState` structs + Event members @292–294; `socketdState` removed from recordd SubMaster (was never read) |
| D20 | **HIGH (runtime crash)** | soundd→`audioData`/`sounddStatus`, spkd→`spkdStatus` not Event members; imud assigned list to `SensorVec` struct (`.accel`/`.gyro =`) and clobbered the union by also setting `temperature`; navd `navRoute.routeId` missing; ncp_session `voiceCommandRequest.command` missing | Event members `audioData@289` (existing AudioData), `SounddStatus@290`, `SpkdStatus@291`; imud rewritten to `.acceleration.v`/`.gyro.v` lists (die temp goes via `temperature` msg); `NavRoute.routeId@1`; `VoiceCommandRequest.command@8` |
| D21 | **CRITICAL (manager cannot boot)** | 15 param keys used but unregistered → `UnknownKeyName`: `EOPVoiceEnabled` (manager.py:67 startup!), `CarMake/CarType/CarVin` (obd2d), `CurrentMCAPRoute`, `LastGPSLat/Lon/Alt` + `UbloxAssistNowToken` (pigeond), `NavPilotOAuthEmail/Token` (bluetoothd), `OpenBLT*` ×3 (UI), `EOPCardsSwipeThreshold` | 15 keys added to params_keys.h (tokens DONT_LOG) |
| D22 | LOW–MED | acados codegen imports used source-repo layout `acados.interfaces.acados_template` (vendored tree has no `interfaces/`) — `lat_mpc.py`/`long_mpc.py` `__main__` codegen path broken (runtime path unaffected) | reverted to upstream import path |
| D23 | LOW | dead/broken leftovers: `tools/convert_models_to_rknn.py` imported nonexistent `get_rknn_target_platform` (unused); `system/ubloxd/tests/ubloxd.py` + `selfdrive/ui/tests/body.py` reference deleted modules/features; `test_uploader.py` + 2 `test_loggerd.py` preserve-tests test the removed cloud-upload/xattr feature (broke pytest collection of `selfdrive` + `system/loggerd` testpaths); `calibration_fusion.py` imported `get_view_frame_from_calib_frame` from wrong module | import removed; 3 dead files deleted; preserve tests removed with note; import corrected |

## Verified-OK findings (no action)

- micd/spkd `i2s_audio` import: guarded try/except with mock fallback — intentional BSP-pending pattern.
- Subscriber-only services (`driverStatus`, `mppStatus`, `rgaStatus`, `liveLocationKalman`, `sppStatus`, `micStatus`): all uses guarded by `sm.valid[...]` — declared integration points for backends not yet publishing health; consistent with D16 WIP stance.
- Declared-only encode/stream services (`recording*Packets`, `livestream*`, `stereo*Encode*`, `teleRoad*`, `uiEncode*`, `microphoneData`, `monoFeatures`, `navigationData`, `osmLocalizerStatus`, `impactEvent`): dormant declarations for planned encoder wiring — 1 line each, no crash risk.
- `athenad`/`tici/agnos` dangling imports: function-local/dormant comma-cloud paths (already documented in DELETED_FILES_AUDIT).
- systemd service `@@DIR@@` template: filled by installer.

## Second pass (2026-06-12, continued): D24–D26

New check dimensions: service↔Event **bidirectional** parity (SubMaster pre-constructs
`new_message(s)` for every subscribed service → service without Event member = crash),
read-side `sm['x'].field` validation (194 sites), aliased `var = msg.x; var.y =` writes
(183 sites), C++ SubMaster/PubMaster service strings (27 names), `log.X.Y` enum/struct
chains (47), shell `bash -n` + JSON validity on all changed files.

| # | severity | what | fix |
|---|---|---|---|
| D24 | **HIGH (native UI startup crash)** | `ui.cc` subscribes `voiceState`/`ttsStatus` — Event members + Custom structs existed but `services.py` entries didn't → C++ `services.at()` throws at SubMaster construction | 2 service entries added (voice pipeline feeds, valid-guarded in ui) |
| D25 | **HIGH (plannerd startup crash)** | `liveLocationKalman` service had no Event member → python SubMaster default-construction crash in plannerd (reader in `longitudinal_planner.py:343` is real+guarded); 4 `livestreamRear*Encode*` services likewise memberless; `navigationData` + `recording*Packets` ×3 declared with zero references anywhere and no derivable schema | Event members added: `liveLocationKalman@295` (upstream struct retained at log.capnp:2003), `livestreamRear{Left,Right}Encode{Idx,Data}@296–299`; the 4 dead reference-free entries removed |
| D26 | MED (silent feature failures) | `camera_overlay.py` used `log.CarState.GearShifter` (CarState is in car.capnp) inside `except: pass` → reverse-camera switch **never worked**; `selfdrived.py` read `.enabled` on `RgaStatus`/`MppStatus` which have no such field (latent AttributeError on the fault-gate path once backends publish); `pointcloudd.py` wrote `validPoints`/`isFiltered` missing from `PointcloudStatus` → status publish silently swallowed every cycle; `tools/replay/ui.py` read `gasDEPRECATED` (EOP schema names it `gas`) | `car.CarState` import fixed (×2 sites, try/except removed); selfdrived gates on `.fault` only (fault implies active per struct docs); 2 fields added to `PointcloudStatus`; `gas` read fixed |

Residual (accepted): `tools/tuning/measure_steering_accuracy.py` reads `controlsState.active`/`carControl.actuatorsOutput` — file is byte-identical to upstream (upstream's own stale debug tool); kept for parity.

Root cause of earlier `Duplicate ID @0xf3b1f17e` "flake": **deterministic, not flaky** — `cereal/__init__.py` itself capnp-loads log.capnp, so any checker that both called `capnp.load()` AND imported `cereal.*` registered the file twice (stdout buffering hid partial progress). Checkers now import via the `cereal` package. No repo defect.

## Third pass (2026-06-12): deferred daemon-import test EXECUTED — D27–D29

Built `msgq` Python extensions standalone (`cd msgq_repo && scons -k`; only catch2 test
binaries skipped), installed runtime deps to user site, ran the deferred
`test_daemon_imports.py` on this PC. Result: **27/28 pass**.

| # | severity | what | fix |
|---|---|---|---|
| D27 | **HIGH (all dev-PC operation broken)** | EOP `hw.py` rewrite dropped every upstream `if PC:` branch — `log_root`/`swaglog_root`/`persist_root`/`stats_root` returned board paths (`/data/...`, `/persist/`) on PCs → PermissionError in every daemon/test (all 28 import tests failed on this) | upstream PC branches restored (`~/.comma/...`); board SD-card logic untouched |
| D28 | MED (modeld crash on PC) | `modeld.py:64`: `"aarch64" in HARDWARE.get_os_version()` — `Pc.get_os_version()` returns `None` → TypeError | `or ""` guard |
| D29 | MED (import kills process) | `reard.py`/`sided.py` ran `exit(1)` at **module level** when platform lacks the camera — importing the module terminates the importer (same class as prior 23527f7f KeyboardInterrupt cleanup) | capability check moved into `main()`, returns 1 |

Remaining failure (plannerd/acados) was then closed by building the MPC solvers on this
PC — which exposed two more defects:

| # | severity | what | fix |
|---|---|---|---|
| D30 | **HIGH (23 corrupt binaries in repo)** | 23 files were still git-LFS **pointer text** committed as content: all `third_party/acados/{x86_64,larch64,Darwin}` libs + `t_renderer` (MPC build impossible — t_renderer "executed" as a shell script), `libyuv`/`raylib` static libs (build + python UI), `catch2/catch.hpp` (msgq tests), `tici/updater`. D17 only covered *modified* assets; these were upstream-untouched so they stayed pointers. | All 23 fetched from commaai LFS by pointer oid, sha256-verified (82.7 MB), exec bits restored — repo now contains zero LFS pointers (`git grep '^version https://git-lfs'` = 0) |
| D31 | **HIGH (plannerd chain import)** | `surface_quality_db.py` (longitudinal_planner → plannerd) imports `exopilot_shared.geohash`/`.surface_quality` — package exists nowhere: not on this machine, not in the GitHub org, not in any of 291 historical commits. Phantom external dependency (deployed-only `/data` package). | try/except fallback: in-repo geohash (same algorithm as `surface_detector.py`), 8×45° `heading_to_bucket`, `get_default_source()` = `detect_exopilot_platform()`; deployed package still wins when present |

**Final result: `test_daemon_imports.py` 28/28 PASS on this dev PC** (msgq built standalone;
MPC solvers codegen'd with materialized `t_renderer` + compiled per the SConscript recipe —
generated artifacts stay gitignored; the scons build reproduces them on any machine).

## Fourth pass (2026-06-12): FULL C++ BUILD — D32–D37

Installed the complete toolchain (`tools/install_ubuntu_dependencies.sh`, passwordless sudo)
and drove `scons` to **`done building targets` (exit 0)**: native `ui` (20 MB, all EOP
widgets), `loggerd`/`encoderd`/`bootlog`, cereal/msgq/common/modeld libs, and both acados
MPC solvers generated+compiled by scons itself. `test_daemon_imports.py` then passes
**28/28 with the real compiled `params_pyx`** (no stub).

| # | severity | what | fix |
|---|---|---|---|
| D32 | HIGH (build) | SConstruct still carried submodule-era CMake bootstrap blocks for libyuv/catch2/raylib (sources not checked out → cmake abort) and LIBPATH pointed at their `build/{arch}` outputs instead of the vendored prebuilts the audit restored | blocks removed; LIBPATH → vendored `third_party/{libyuv/{arch}/lib, raylib/{arch}, acados/{arch}/lib}` |
| D33 | HIGH (API mismatch) | exo msgq fork removed the OpenCL surface (`cl_mem`, `CLContext`, `VisionBuf.buf_cl`) but upstream-derived code still targeted it: `commonmodel_pyx.pyx` uncompilable, `modeld.py` called 4-arg `VisionIpcClient(..., use_cl, cl_context)` vs the fork's 3-arg signature | commonmodel CL pipeline excluded from build (numpy implementation in `commonmodel_pyx.py` + inferenced/RGA is the EOP path; sources kept); modeld calls fixed to `(name, stream, conflate)` |
| D34 | HIGH (C++ twin of D27) | `hw.h` `Path::params()/log_root()` returned `/data/...` unconditionally → compiled `Params()` aborted on PCs; `Hardware::PC()` used a fragile HOME heuristic | PC-aware paths (upstream semantics); `PC()` = `!ROCKCHIP()` (device-tree), mirroring python |
| D35 | MED | `long_mpc.py` codegen called `Params()` at generation time → fails in clean build envs (scons strips HOME) | fail-safe fallback to compile-time personality defaults |
| D36 | MED | `encoder.h` included deleted `third_party/linux` header; `v4l_encoder.cc` is Qualcomm-only (V4L2_QCOM_*/VIDC) yet still in the build | system `<linux/v4l2-controls.h>`; v4l_encoder excluded from build (encoderd.cc header already documented this; MPP is the EOP encode path) |
| D37 | MED (UI build wiring) | never-compiled EOP UI code: duplicate `SetupWidget` (dead `prime.cc` vs `drive_stats.cc`), `bev_widget.cc` not in SConscript + contained Python syntax (`sm.valid.get("x", false)`), header-only `SideCard` never moc'd, `openblt_update_widget_impl.cc` name broke qt-tool moc pairing, `eop_panel.cc` extra paren, `hud.cc` read face fields from `DriverPoseState` (steering-based — they live in `DriverStatus`) | prime.cc dropped from build (dead, kept on disk); bev_widget added + syntax fixed; `side_card.cc` moc stub; impl renamed `openblt_update_widget.cc`; paren fixed; hud reads face fields from `driverStatus` |

## Fifth pass (2026-06-12): test-suite execution — D38–D41

Ran the runnable pytest tranches (common, cereal/messaging, system/loggerd) on the green build.

| # | severity | what | fix |
|---|---|---|---|
| D38 | HIGH (test infra) | exo msgq fork roots prefixed shm segments at `/dev/shm/msgq_<prefix>/` (msgq.cc) but upstream-derived `prefix.py` created `/dev/shm/<prefix>` → every `OpenpilotPrefix` test env got IpcError; 359 messaging tests failed instantly | `prefix.py` msgq_path matches the fork; **522/522 messaging core tests pass** |
| D39 | **HIGH (always-run daemon dead)** | `storage_policy.py` had malformed `Callable[[list[Path \| None], None]]` annotation → TypeError at class definition → `deleter` crashed at import on every Python version | annotation fixed to `Callable[[list[Path]], None] \| None`; import test expanded 28→40 daemons (now covers deleter, hardwared, wdgd, rtcd, imud, micd, spkd, mcapd, bluetoothd, socketd, thermald, stated) |
| D40 | HIGH (bluetoothd dead) | `pairing_agent.py` `RequestPinCode` dbus decorator declared `in_signature='os'` for a 1-arg method → ValueError at class definition (caught by the expanded import test) | `in_signature='o'` (BlueZ agent API) |
| D41 | HIGH (storage management) | deleter/StoragePolicy split-brain: legacy `os.statvfs` signal vs policy `shutil.disk_usage` metrics could disagree → loop spins at 0.1 s deleting nothing on a full disk; any transient metric error **killed the thread silently**; ordering by `st_ctime` (nondeterministic); preserved segments skipped forever (full-disk deadlock); upstream `PRESERVE_COUNT` rule missing | `enforce_limits(force=...)` guarantees progress on the caller's signal; loop hardened (log + continue); deletion order = upstream semantics (name order, only newest 5 preserved protected, boot/crash last); `deleter.PRESERVE_*` API re-exported. **6/6 upstream deleter tests pass** |

Also staged: steering-attention speed gates as m/s constants (`ATTENTION_BYPASS_SPEED=18.0`,
`ATTENTION_MAX_SPEED=36.0` in `events.py` — 9 m/s grid; 65/130 km/h are the km/h roundings).
Known env-only failures: `datetime.UTC` tests need Python 3.12 (this PC runs 3.10).

## Validation

- pycapnp: schema loads; all 8 new/used Event members exercised (init + field assignment) — 10/10 deterministic runs. (Sporadic `Duplicate ID` seen ONLY via stdin-heredoc python with overlapping import roots + the repo's `openpilot→.` self-symlink — validation-harness artifact, not a schema defect; scons capnpc is unaffected.)
- `cereal.services` imports: 174 services.
- ruff F821/F811 clean on every edited file.
- All daemon wiring checks re-run green.

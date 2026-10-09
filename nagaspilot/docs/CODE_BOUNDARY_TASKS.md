# Code boundary audit and task list

Owner rule: **`selfdrive/` stays as close to upstream as possible.** Product work lives in
`nagaspilot/`; heavy change is expected only in `system/` (board/hardware) and the UI.
Baseline for every measurement: official openpilot v0.10.0, commit
`c085b8af19438956c15592828bd082803f43dfaf`. Measured 2026-10-03; the 01M/02M rows were identical (161) and are
unverified, so rerun `python3 nagaspilot/footprint.py` on each branch before relying on them.

Related: [`BOUNDARIES.md`](BOUNDARIES.md) (layer rules), [`NAMING_CONVENTIONS.md`](NAMING_CONVENTIONS.md)
(prefixes), `nagaspilot/footprint.py` (ratchet tool), `nagaspilot/footprint_budget.json`.

## 1. Lineage and prefix rule

| Stage | Branch | Prefix | Hardware |
| --- | --- | --- | --- |
| NagasPilot | `dev/NGP10` | `ngp_` / `NGP` | comma 3 |
| ExoPilot foundation (RK3588, old C++ UI) | `dev/EOP10` | `eop_` / `EOP` | RK3588 |
| ExoPilot 01M | `dev/01M` | `eop_` / `EOP` | RK3588 |
| ExoPilot 02M | `dev/02M` | `eop_` / `EOP` | RK3576 |

- Code that works from comma 3 inputs only (model output, car state, IMU, GPS) and is identical
  on NGP10 keeps `ngp_`. Anything that needs EOP-only hardware (stereo, side/rear cameras,
  accelerator detectors, side/rear radar) is `eop_` and is never on NGP10.
- 01M and 02M follow the EOP rule exactly; they do not invent a third prefix.

Branch order is linear: **NGP10 → EOP10 → 01M → 02M**. A change lands on the earliest branch it
belongs to, and each later branch takes it by rebase (never by cherry-pick). Comma-3-capable policy
goes on NGP10 first, EOP-hardware work on EOP10, UI on 01M, RK3576 on 02M. Where the branches
currently carry separate copies of the same commit (01M and 02M each hold their own "share …"
commits, 50 and 92 commits past EOP10's old tip `4fca75ea2`), the next rebase must drop the copies.

## 2. Upstream `selfdrive/` footprint (excluding `ui`, `assets`)

| Branch | Upstream files modified | Deleted | New files added |
| --- | --- | --- | --- |
| NGP10 | 11 (+518 / −51 lines) | 0 | 8 |
| EOP10 | 221 (all of `selfdrive/`) | 89 | 397 |
| 01M | 161 (all of `selfdrive/`) | 186 | 418 |
| 02M | 161 (all of `selfdrive/`) | 186 | 434 |

Largest edits to upstream files, NGP10 versus 01M:

| File | NGP10 | 01M |
| --- | --- | --- |
| `selfdrive/modeld/modeld.py` | +174 | +733 / −317 |
| `selfdrive/controls/lib/longitudinal_planner.py` | +216 | +622 |
| `selfdrive/controls/controlsd.py` | +48 | +358 |
| `selfdrive/controls/lib/desire_helper.py` | +24 | +177 (was +362; see log) |
| `selfdrive/selfdrived/selfdrived.py` | +5 | +313 |
| `selfdrive/controls/lib/latcontrol_torque.py` | — | +222 |
| `selfdrive/selfdrived/events.py` | — | +165 |
| `selfdrive/controls/lib/longcontrol.py` | — | +144 |

Findings:
- NGP10 already meets the rule. Its two biggest hooks (`longitudinal_planner`, `modeld`) are still larger than a hook should be; part of `modeld.py` is eGPU runtime, not policy.
- 01M/02M embed EOP logic inside upstream files; these are the files that conflict on every upstream sync.
- 01M/02M add ~420 files directly under `selfdrive/` (whole daemons: `gridd`, `steamd`, `stereod`, `monod`, `pathd`, …) instead of `nagaspilot/`.
- `system/` changes are large and mixed (hardware, `inferenced`, `socketd`, `camerad` deleted). Target shape: additive board directories (`system/hardware/rk3588/`, `rk3576/` on 02M).

## 3. Prefix findings on the EOP line

- 36 params added without an `EOP` prefix: `BLE*`, `Bluetooth*`, `OpenBLT*`, `SteamD*`, `NavPilotOAuth*`, `Chestnut*`, `QuietMode`, `PowerSaverEntryDuration`, `CurrentMCAPRoute`, `CameraCalibrationParams`, `FactoryCalibrationParams`, `SpeedLimitPolicy`, `NeuralNetworkLateralControl`, `CarMake/CarType/CarVin`, `LastGPS*`, `NavDestination*`, `UpdateStatus`. Some may be upstream names; each needs a check. The `Egpu*` params on NGP10 have the same problem.
- `custom.capnp`: 92 of 95 structs have no `Eop` prefix (wire-safe to rename, but breaks every reference). Rule going forward: new structs only.
- Policy classes `NGPMonoD`, `NGPCapabilities`, `NGPSOC`, … exist only on the EOP line and carry the NGP prefix.
- Seven `ngp_*` libraries sit in `selfdrive/{adaptd,gridd,mapd,monod,pathd,tripd}`; on 01M/02M nothing outside tests imports them (dead code).
- `lazy_bev.py` on 01M/02M is real daemon code (`gridd`, `segd`); it does **not** define `NGPLazyBEV`, so `selfdrive/gridd/tests/test_ngp_portable_features.py` fails at import there.
- Both `ngp_panel.*` and `eop_panel.*` exist on EOP10.
- Root files that do not belong on EOP10: `NGP10_CHESTNUT_MIGRATION_PLAN.md`, `task.md`.

## 4. Tasks

Status: ☐ open · ◐ partial · ☑ done · ⛔ needs on-device validation or a build this environment does not have.

| # | Task | NGP10 | 01M | 02M |
| --- | --- | --- | --- | --- |
| T1 | This document | ☑ | ☑ | ☑ |
| T2 | Footprint ratchet (`footprint.py` + budget + test): the footprint may not grow | ☑ | ☑ | ☑ |
| T3 | Move NGP-lineage libraries out of `selfdrive/` into `nagaspilot/`, `eop_` names on the EOP line | ☑ (done earlier) | ☑ | ☑ |
| T4 | Remove or fix the broken `test_ngp_portable_features.py` (`NGPLazyBEV` import) | n/a | ☑ | ☑ |
| T5 | Shared policy modules (DLON, lead handoff) on NGP10 and every later branch | ☑ | ☑ | ☐ not yet checked on 02M |
| T6 | Rename the 36 non-`EOP` params, with a settings migration | n/a | ⛔ | ⛔ |
| T7 | Remove `ngp_panel.*` from the EOP line | n/a | ⛔ | ⛔ |
| T8 | Move EOP daemons from `selfdrive/` into `nagaspilot/<name>d/` | n/a | ⛔ | ⛔ |
| T9 | Extract product logic from large upstream files, down to hook size | ◐ pure NGP longitudinal policies moved; stateful orchestration remains | ⛔ `desire_helper` and `events.py` helpers moved; 6 runtime files need behavior-equivalence/build validation | ⛔ `desire_helper` and `events.py` helpers moved; 6 runtime files need behavior-equivalence/build validation |
| T10 | Make `system/` additive: board directories, shared files untouched | n/a | ⛔ | ⛔ |
| T12 | Unprefixed EOP daemon modules (`lazy_bev.py`, `gridd/*`, …): decide prefix and location together with T8; `lazy_bev` is live code used by `gridd`/`segd`, not dead like the libraries moved in T3 | n/a | ☐ | ☐ |
| T11 | Remove the stale root `NGP10_CHESTNUT_MIGRATION_PLAN.md` from the EOP line. Keep `task.md`: it is the live task record there | n/a | ☑ | ☑ |
| T13 | Lane-change gap and lane-width guards from modelV2 in one shared module (`ngp_lane_change.py`) | ◐ module + hook + tests, opt-in, no build run | ◐ EOP10 `desire_helper` calls it; 01M `eop_lane_change.py` copy still to become an adapter | ☐ same as 01M |
| T14 | NGP10 footprint ratchet is red at `82d52eca4`: controlsd +53 > 48, `long_mpc.py` new, longitudinal_planner +244 > 193. Shrink the hooks or justify and `--update` | ◐ | n/a | n/a |
| T15 | Lead-departure notice from `radarState` in one shared module (`ngp_lead_departure.py`) | ◐ opt-in `ngp_lon_lead_departure`, `EventName` `@98`-`@108` aligned with EOP10 | ☑ `selfdrived` uses the module | ☑ same as 01M |

Order: T1 and T2 first on every branch (they stop further growth), then T3/T4 (mechanical, dead
code), then the ⛔ items one daemon or file at a time, each with its own hook budget and an
on-device check.

## 5. Working rules

1. A change to an upstream `selfdrive/` file is a hook: a call into `nagaspilot/` plus the minimum glue. Policy, tables and state machines live in `nagaspilot/`.
2. `python3 nagaspilot/footprint.py --update` is run only after the footprint shrank, and the diff of `footprint_budget.json` is reviewed.
3. Each step is its own commit, on each branch separately, with tests run where the environment allows and the limits stated in the commit message.
4. Branch order is NGP10 → EOP10 → 01M → 02M (section 1). Do not treat EOP10 as retired.

## 6. Progress log

- **T9 / `desire_helper.py` (01M `b4056162c`, merged into 02M)**: road-edge guard, adjacent-gap TTC check, lane-width check and blind-spot priority logic moved into `nagaspilot/controls/eop_lane_change.py` as pure functions; `desire_helper.py` keeps the state machine. Upstream-file delta +362 → +177. Old and new compared over 900,000 randomized outputs, 0 differences; the repo's own `desire_helper` tests were not run (no cereal build here). The state machine stays upstream-side: it needs on-device validation to move.
- **T9 / longitudinal policies across the lineage**: adaptive acceleration and speed-offset math now lives in `nagaspilot/controls/longitudinal_policy.py`, shared by NGP10 and the EOP branches. NGP10's old module path remains an alias; branch call sites retain their existing names.
- **Shared lane-change lead handoff (NGP10, EOP10, 01M, 02M)**: EOP's old implementation is now a parameter adapter over `nagaspilot/controls/ngp_lc_lead_handoff.py`. The shared policy converts modelV2 right-positive lateral coordinates to radarState left-positive coordinates once; this also fixes NGP10's prior side-selection/sign mismatch.
- **Shared speed-limit policy (NGP10, EOP10, 01M, 02M)**: EOP's Params and cereal adapter now calls the same source resolver as NGP10. In car-fallback mode, map/navigation wins whenever available; car speed is used only when both are unavailable.
- **Portable longitudinal features (NGP10, EOP10, 01M, 02M)**: acceleration-profile interpolation and adaptive following-gap math now live in `nagaspilot/controls/longitudinal_policy.py`. NGP10 keeps its existing normal acceleration curve and both branches default adaptive gap off; the OBD/BLE-driven adaptive-personality daemon remains hardware/infrastructure-specific. The NGP MPC accepts optional overrides while preserving its previous behavior when they are omitted. On-road validation is still required before enabling adaptive gap.
- **Steering resume ramp (NGP10, EOP10, 01M, 02M)**: the shared stateful resume ramp now lives in `nagaspilot/controls/steering_policy.py` and is used by both control runtimes. It has no EOP-only inputs; hardware validation is still needed for vehicle-specific steering response.

- **T13 / lane-change audit (NGP10)**: `nagaspilot/controls/ngp_lane_change.py` holds the adjacent-lane TTC gap and target-lane width checks, pure and from modelV2 only. `DesireHelper` calls them in `preLaneChange` through two default-off params, `ngp_lat_lca_gap_eval` and `ngp_lat_lca_lane_width`; the state machine is unchanged. Findings: (1) `radarState` is **not** subscribed in `modeld`: `radarState.leadOne/leadTwo` are radard's radar-refined copies of `modelV2.leadsV3[0/1]`, published one frame after modelV2, so they carry no adjacent-lane information beyond what modeld already has and would double-count a vehicle; the function still accepts a `radar_state` for callers that have one. (2) Frame: model y is right-positive, flipped once inside the function, same rule as `ngp_lc_lead_handoff`. (3) Unknown data (no model, short lines, missing edge) never blocks. (4) EOP's road-edge blinker check is not ported; NGP's `ngp_road_edge` already covers it, and blind-spot alert fields stay EOP-only. Tests: `nagaspilot/tests/test_ngp_lane_change.py`, `selfdrive/controls/tests/test_desire_helper_lane_confidence.py` (12 pass with `--noconftest`; no full build or vehicle run). EOP10's `desire_helper` now delegates to the module (2026-10-03, behaviour identical: same thresholds, 1.52 m radar offset, frames). Next: rebase 01M and 02M so `eop_lane_change.py`'s gap and width functions become adapters.

### Next audit handoff

The portable EOP10 longitudinal gaps found on 2026-10-03 are now shared: selectable acceleration profiles, default-off adaptive following gap, and the post-resume steering ramp. Shared longitudinal/steering policies are mirrored across NGP10, EOP10, 01M, and 02M; branch-specific Params and sensor adapters stay at the runtime boundary.

Lane-change behaviour is now audited (T13); the paragraph below is the original handoff, kept for the checks still open (call timing on a real build, thresholds, adapters on later branches).

Original handoff: audit EOP lane-change behavior against NGP10. EOP road-edge direction blocking, target-lane width validation, and TTC gap evaluation use `modelV2` and `radarState`, which are available on comma 3; NGP's `modeld` currently passes road-edge booleans and lane confidence but does not pass model geometry or subscribe to `radarState` for lane-change checks. Verify coordinate frames, call timing, and unknown-data behavior before wiring these guards. Preserve NGP's current `DesireHelper` state machine and use EOP's pure helpers as policy references. Do not include EOP-only `blindSpotAlert`/side-rear radar fields.

Also classify, without blind copying: (1) EOP TJA launch ramp is standard-state input based but opt-in; standstill hold timeout needs a driver-visible resume event path; (2) lead-departure alert can use `radarState` but needs event/UI integration; (3) EOP traffic-light, surface, road-condition, collision, enhanced-trajectory and adaptive-personality policies need their documented perception, map/pathd, or OBD/BLE inputs; (4) steering/turn limits need vehicle-model equivalence review. Keep default behavior and thresholds explicit.

No test suite, build, vehicle, or HIL validation was run for the 2026-10-03 changes. `git diff --check` was clean. Validate behavior and compile on the target build before enabling adaptive gap or relying on actuator changes.
- **Boundary cleanup (2026-10-03, EOP10)**: `test_boundaries.py` was failing 2 of 4 on every EOP branch. (1) `eop_speed_limit_resolver.py` (Params) and `eop_runtime_policy.py` (VehicleModel) are runtime adapters, so they moved from the pure `nagaspilot/controls/` to `nagaspilot/runtime/`; `eop_utils.py` (Params) moved there too, importers updated, no shim (a shim named `eop_*` would itself be flagged). `eop_settings_backup.py` moved to `nagaspilot/tools/`. (2) `eop_panel.{cc,h}` and `eop_qr.png` joined the reviewed outside-`nagaspilot/` list next to `ngp_panel.*` (C++ UI hook and an asset). (3) The stray `NGP10_CHESTNUT_MIGRATION_PLAN.md` is removed (T11). (4) **Bug**: after B4, `gridd` still read a `self.vipc_road` it no longer creates and called an undefined `_nv12_to_bgr` on the first loop pass, so it would crash at start; the unused road-frame read is removed.
- **T15 (EOP10, 2026-10-03)**: `selfdrived`'s lead-departing check now calls `nagaspilot/controls/ngp_lead_departure.py` (shared with NGP10, where `EventName` ordinals `@98`–`@108` now match this branch). Behaviour change: the baseline range is the closest since the stop and resets on lead/track change, instead of the first range kept forever; thresholds (1 m, 1 m/s) and the TTS notice are unchanged.
- **Footprint budget (EOP10, car.capnp swap, 2026-10-04)**: `longcontrol.py` +144/-2 -> +148/-6 and `test_longcontrol.py` (+3/-1, new entry) are the `CP.deprecated.*` reads forced by opendbc's schema; `longitudinal_planner.py` -/+ is the same one-line `deprecated.vEgoStopping` change. Not a shrink, a schema-forced exception to rule 2.
- **Runtime verification by launching things (EOP10, 2026-10-04)**: after the `car.capnp` swap, builds and imports passed but real launches did not. Fixed by running each producer/daemon: card (`CANParser.update`, no `carName`, `safetyModel` slot, `actuators.torque`), radar3d/radard/gridd (`RadarPoint` deprecated group, `errors` struct), selfdrived (no `radar4d` service on EOP10/01M), controlsd (no `accelMin/accelMax`, `longitudinalTuning.deprecated.kf`, alccState enum), `long_mpc` personality `_DynamicEnum`, hardwared/pathd/tripd/surfaced/updated (pycapnp 2.x rejects IntEnum, numpy scalars and `list.append`; typed Params; `SelfdriveState` has no `isOnroad`, `ControlsState` no `enabled`). Tests: `nagaspilot/tests/test_*_smoke.py`, `test_radar_*_schema.py`, `test_capnp_strictness.py`, `selfdrive/controls/tests/test_long_mpc_personality_enum.py`. Not launched: modeld (needs models), steamd/uvcd/sided/reard/imud/v4l2d (hardware), and the daemons that need `HARDWARE.get_can_interfaces` / `get_camera_geometry`, which the dev-PC `Pc` class does not have.
- **Open from that run**: `tripd` loads daily stats from Params keys named `Daily_<date>_*` that are not in `params_keys.h`, so every load fails (logged, not fatal); it needs registered keys or file storage. `lagd` reports "Failed to retrieve initial lag" on a clean params store.
- **Driver-activity monitoring (EOP10, 2026-10-04)**: no driver camera, so the monitor is `driveractivityd` (shared with NGP10: drain by speed band 11/22/33 m/s, a wheel press / brake press / gas press refills, soft/prompt/critical at 50/25/0 %, critical decelerates, never disengages). `selfdrived` now takes DM events from `driverMonitoringState` instead of the never-published `driverPoseState`; the unused EOP `driverAttention`/`driverWarning`/`driverCritical` alert definitions and the 12/24/36 m/s constants were removed (the enum entries stay). Replaces the SAFETY gap recorded in task.md.

- **Carried from NGP10 (2026-10-04)**: identical pure modules, adapters, tests and the replay tool for monod YOLO, cut-in speed trim, path selector, pathAdjust consumers (`nagaspilot/controls/ngp_{detect,ranging,object_tracker,cutin_speed,path_selector,pathd_consumer}.py`, `runtime/{cutin_adapter,path_adapter,monod,pathd}.py`, `tools/replay_object_guard.py`, `docs/OBJECT_PROTECTION_STUDY.md`). They are inert here: nothing imports them and `process_config.py` does not register `nagaspilot.runtime.monod` or `.pathd` (EOP10 keeps its RKNN `selfdrive.monod` and `selfdrive.pathd`). Schema aligned: `MonoDetections.modelExecutionTime @4` and `PathAdjust @0x86ee74962cb31a1d` added to `custom.capnp` (same struct ids), Event `pathAdjust @304` (NGP10: `@152`; `monoDetections` stays `@218` here, `@151` there), service `pathAdjust`. Verified: pycapnp load of the whole `log.capnp` (305 ordinals, no gaps) and a message round-trip; 60 pure tests pass on this branch. Hooks (controlsd/planner/plannerd) are NOT carried: EOP10's planner differs; the adapters to write are listed in `task.md`.
- **Boundary cleanup (2026-10-03, from EOP10, then 01M)**: runtime adapters (`eop_speed_limit_resolver`, `eop_runtime_policy`) and `eop_utils` live in `nagaspilot/runtime/`, the settings-backup script in `nagaspilot/tools/`; `test_boundaries.py` is 4/4. `gridd.run()` no longer reads the removed road frame (it crashed on the first loop pass; fixed in `nagaspilot/daemons/gridd/gridd.py` here). Stray `NGP10_CHESTNUT_MIGRATION_PLAN.md` removed.
- **01M rebased onto the car.capnp swap (2026-10-04)**: `cereal/car.capnp` is the opendbc symlink here too; `footprint_budget.json` re-measured for 01M after the swap (`CP.deprecated.*` reads). `CarState.lkaDisabled` had no writer, so its two readers were removed.

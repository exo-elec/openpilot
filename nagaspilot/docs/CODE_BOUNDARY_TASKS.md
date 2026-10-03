# Code boundary audit and task list

Owner rule: **`selfdrive/` stays as close to upstream as possible.** Product work lives in
`nagaspilot/`; heavy change is expected only in `system/` (board/hardware) and the UI.
Baseline for every measurement: official openpilot v0.10.0, commit
`c085b8af19438956c15592828bd082803f43dfaf`. Measured 2026-10-03.

Related: [`BOUNDARIES.md`](BOUNDARIES.md) (layer rules), [`NAMING_CONVENTIONS.md`](NAMING_CONVENTIONS.md)
(prefixes), `nagaspilot/footprint.py` (ratchet tool), `nagaspilot/footprint_budget.json`.

## 1. Lineage and prefix rule

| Stage | Branch | Prefix | Hardware |
| --- | --- | --- | --- |
| NagasPilot | `dev/NGP10` | `ngp_` / `NGP` | comma 3 |
| ExoPilot generation 1 (being replaced by `dev/01M`) | `dev/EOP10` | `eop_` / `EOP` | RK3588 |
| ExoPilot 01M | `dev/01M` | `eop_` / `EOP` | RK3588 |
| ExoPilot 02M | `dev/02M` | `eop_` / `EOP` | RK3576 |

- Code that works from comma 3 inputs only (model output, car state, IMU, GPS) and is identical
  on NGP10 keeps `ngp_`. Anything that needs EOP-only hardware (stereo, side/rear cameras,
  accelerator detectors, side/rear radar) is `eop_` and is never on NGP10.
- 01M and 02M follow the EOP rule exactly; they do not invent a third prefix.

## 2. Upstream `selfdrive/` footprint (excluding `ui`, `assets`)

| Branch | Upstream files modified | Deleted | New files added |
| --- | --- | --- | --- |
| NGP10 | 11 (+518 / −51 lines) | 0 | 8 |
| EOP10 | 221 (all of `selfdrive/`) | 89 | 397 |
| 01M | 32 (+2993 / −709) in the ratchet; 161 counting `ui` and `assets` | 70 in the ratchet; 186 counting `ui` | 418 |
| 02M | 32 (+2993 / −709) in the ratchet, identical to 01M; 161 counting `ui` and `assets` | 70 in the ratchet; 186 counting `ui` | 434 |

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
- `ngp_panel.*` was an orphan on 01M/02M: the Qt UI no longer builds there.
- Root file that did not belong on EOP10: `NGP10_CHESTNUT_MIGRATION_PLAN.md` (NGP planning). `task.md` is EOP's live task list and stays.

## 4. Tasks

Status: ☐ open · ◐ partial · ☑ done · ⛔ needs on-device validation or a build this environment does not have.

| # | Task | NGP10 | 01M | 02M |
| --- | --- | --- | --- | --- |
| T1 | This document | ☑ | ☑ | ☑ |
| T2 | Footprint ratchet (`footprint.py` + budget + test): the footprint may not grow | ☑ | ☑ | ☑ |
| T3 | Move NGP-lineage libraries out of `selfdrive/` into `nagaspilot/`, `eop_` names on the EOP line | ☑ (done earlier) | ☑ | ☑ |
| T4 | Remove or fix the broken `test_ngp_portable_features.py` (`NGPLazyBEV` import) | n/a | ☑ | ☑ |
| T5 | Shared policy modules (DLON, lead handoff) mirrored across branches | ☑ | ☑ (EOP10 only) | ☐ |
| T6 | Rename the fork-added non-`EOP` params (40 found against v0.10.0), with a settings migration | n/a | ◐ renamed + migration, not run on a device | ◐ (inherited) |
| T7 | Remove `ngp_panel.*` from the EOP line | n/a | ☑ (orphan: the Qt UI is gone on 01M/02M) | ☑ |
| T8 | Move EOP daemons from `selfdrive/` into `nagaspilot/<name>d/` | n/a | ⛔ | ⛔ |
| T9 | Extract EOP logic from the eight large upstream files, down to hook size | ◐ (small hooks) | ◐ `desire_helper` helpers done; 7 files left | ◐ inherited from 01M |
| T10 | Make `system/` additive: board directories, shared files untouched | n/a | ⛔ | ⛔ |
| T12 | Unprefixed EOP daemon modules (`lazy_bev.py`, `gridd/*`, …): decide prefix and location together with T8; `lazy_bev` is live code used by `gridd`/`segd`, not dead like the libraries moved in T3 | n/a | ☐ | ☐ |
| T11 | Remove the stray NGP planning file from the EOP root (`task.md` is EOP's live task list and stays; earlier audit text was wrong) | n/a | ☑ | ☑ |

Order: T1 and T2 first on every branch (they stop further growth), then T3/T4 (mechanical, dead
code), then the ⛔ items one daemon or file at a time, each with its own hook budget and an
on-device check.

## 5. Working rules

1. A change to an upstream `selfdrive/` file is a hook: a call into `nagaspilot/` plus the minimum glue. Policy, tables and state machines live in `nagaspilot/`.
2. `python3 nagaspilot/footprint.py --update` is run only after the footprint shrank, and the diff of `footprint_budget.json` is reviewed.
3. Each step is its own commit, on each branch separately, with tests run where the environment allows and the limits stated in the commit message.
4. `dev/EOP10` is being replaced by `dev/01M`; new work targets 01M and 02M, not EOP10.

## 6. Progress log

- **T9 / `desire_helper.py` (01M, `b4056162c`)**: the road-edge guard, adjacent-gap TTC check, lane-width check and blind-spot priority logic moved into `nagaspilot/controls/eop_lane_change.py` as pure functions. `desire_helper.py` keeps the state machine and thin delegates (`_evaluate_gap` stays callable unbound, as `test_lateral_sign_conventions.py` does). Upstream-file delta +362 → +177, file 456 → 271 lines. Equivalence check: old and new compared over 900,000 randomized outputs (40 seeds × 2,500 steps, helper calls included, fake clock, stubbed cereal/Params/DLAT), 0 differences. Not run: the repo's own `desire_helper` tests (no cereal build here) — run them on a build before relying on this. The state machine itself stays upstream-side; it is stateful and timing-sensitive, so moving it needs on-device validation.
- **T7 / T11 (01M, 02M)**: deleted the orphan `selfdrive/ui/qt/offroad/ngp_panel.{cc,h}` (`selfdrive/ui/SConscript` states no Qt UI is built on either UI branch, nothing referenced the files) and the NGP-only `NGP10_CHESTNUT_MIGRATION_PLAN.md` (unreferenced; it stays on `dev/NGP10`). Correction: `task.md` is EOP's live working list and is referenced by `docs/` and `models/`, so it stays.
- **T6 (01M, 02M)**: 40 fork-added params that lacked a prefix now follow `EOP<Name>` (none were upstream names; v0.10.0 has none of them). 146 quoted references in 30 files plus 27 comment lines were renamed; the `SpeedLimitPolicy` enum class in `ngp_speed_policy.py` is a different thing and keeps its name. `nagaspilot/param_migration.py` renames the stored param files on first manager start (hook in `manager_init`, before anything reads params) so tokens and settings carry over. Tests cover the rename (new names declared, old gone, no quoted old name left in code) and the migration (moves values, keeps an existing new value, idempotent). **Not verified on a device**: a real boot with old values, and external clients that may use the old names (NavPilot phone app, SteamD remote clients) — check those before shipping.


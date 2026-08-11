# Node 3 — `opendbc_repo` submodule vendoring divergence (EDP10 vs NGP10)

**Verdict: ✅ not a bug — fully inherited from each branch's base fork, not
introduced by branch edits.**

## Finding

- **EDP10** (dragonpilot base): `opendbc_repo` is a plain tree (`040000 tree`),
  not a submodule gitlink. Same for `panda`, `msgq_repo`, `rednose_repo`,
  `teleoprtc_repo`, `tinygrad_repo` — **all** vendored as plain directories.
  `.gitmodules` does not exist anywhere in the tree.
- **NGP10** (comma base): `opendbc_repo` is a submodule gitlink
  (`160000 commit 2cde2462...`), pointing at `git@github.com:exo-electronics/opendbc.git`
  branch `master` — matches comma's standard convention.
- Checked `base/dev-EDP10-dragonpilot-0.10.0` directly (the fork point, before
  any EOP/EDP edits): it **already** has no `.gitmodules` and already vendors
  `opendbc_repo` as a plain tree. This is dragonpilot's own upstream
  convention, not a structural choice made while developing EDP10.

## Consequence for this audit

This means the EDP10 vs NGP10 diffstat comparison (e.g. `longitudinal_planner.py`
+57 vs +101) partly reflects this base difference in addition to actual feature
work — reinforcing the index's note to compare added hunks / new-file modules
rather than raw line counts across these two branches.

## Follow-up (not chased further — separate scope)

The NGP10 submodule pins `opendbc_repo` to `exo-electronics/opendbc.git@2cde2462`.
Whether that fork commit itself carries any exo-electronics-specific patches
(and whether those patches are consistent with what EDP10 vendors inline) is a
submodule-content audit, out of scope for this repo-level pass. Worth a
follow-up if BYD support or other car ports are ever expected to be shared
between EDP10 and NGP10.

No fixes applied — no bug to fix.

## Update (2026-08-12): the flagged follow-up — BYD/Chery/JAECOO ported to `exo-electronics/opendbc`

Surveyed `../bukapilot` (kommuai/bukapilot) and `../opendbc` (kommuai/opendbc,
both MIT-licensed) for BYD and JAECOO car-port material. JAECOO is Chery's
sub-brand, so the real lead was kommuai/opendbc's Chery module, which already
covers `CHERY_JAECOO_J7_PHEV` (marked "under validation" upstream),
`CHERY_TIGGO_8_PRO`, `CHERY_OMODA_5`, and `CHERY_ICAUR_03`, plus a more
complete BYD port than what EDP10 vendors inline (multiple DBCs, split
`cam_lka`/`mpc_lka` steering variants).

**exo-electronics/opendbc was on v0.2.1 (2025-02-10), predating the
`opendbc/safety/` migration entirely** — kommuai's byd/chery car ports (Python
`car/` API + C safety modes) don't import against that old core at all. Bumped
`exo-electronics/opendbc` to current upstream (`e677024b`, 2026-08-10) on a new
branch `port/upstream-bump-byd-chery-jaecoo` (not `master` — `.gitmodules`
pins `branch = master`, so an unverified core bump landing there is exactly
what a future `git submodule update --remote` would silently pull), carrying
forward the fork's one prior patch (BrownPanda Tesla radar, `2cde2462`) via
cherry-pick conflict resolution — including composing BrownPanda's
party-bus-0 radar parser with upstream's now-added native bus-1
`Bus.radar`-DBC radar path in `tesla/radar_interface.py`, since upstream grew
its own generic radar mechanism in the 1261 commits between the two bases.

Ported `opendbc/car/byd/`, `opendbc/car/chery/`, their DBCs, and
`opendbc/safety/modes/{byd,chery}.h` + `chery`'s safety tests onto the bumped
core, fixing real API drift along the way (not a straight copy):
- `car.capnp` `SafetyModel` enum: added `byd @35` — **deliberately matching
  EDP10's own numbering** (`opendbc_repo/opendbc/car/car.capnp` on
  `dev/EDP10`) so a byd-branded safety-model integer decodes the same brand
  on both branches — and `chery @36` (EDP10 has no Chery port yet, so no
  collision to match against).
- `opendbc/car/__init__.py` / `docs_definitions.py`: added `dbc_dict()` and
  `CUSTOM_CAR_PARTS`, small kommuai-only helpers both car ports depend on.
  Added `CarDocs.variant`/`acc_low_speed`/`acc_speed_range`/`acc_stop_and_go`/
  `lkc_torque`/`lkc_speed_range`/`max_steering_angle` (kommuai's own docs-schema
  extension) but dropped `kommu_supported` — a kommu.ai-branding flag with no
  place in this fork.
- Both car ports used `from cereal import car` (pre-migration API) instead of
  `opendbc.car.structs` — this fork's opendbc has no `cereal` dependency at
  all post-migration. Mechanically re-pointed all `car.CarParams`/`car.CarState`
  references.
- `byd.h`'s `AngleSteeringLimits` literal used `max_angle_error`/
  `enforce_angle_error`/`angle_is_curvature`/`inactive_angle_is_zero` fields
  that don't exist on this fork's (newer) C struct. All were no-ops in the
  source config already (`enforce_angle_error: false` etc.) except
  `inactive_angle_is_zero: true`, and this fork's
  `steer_angle_cmd_inactive_check()` always uses the *stricter* of the two
  options it selected between — dropping the fields is not a safety
  regression, verified by reading both implementations, not assumed.
- Several `CarState`/`CarParams` fields byd/chery set
  (`startingState`/`startAccel`/`stoppingDecelRate`, `.brake`) moved into
  `car.capnp`'s `deprecated :group` in this fork's newer core — re-pointed to
  `ret.deprecated.X`. **`startingState`/`startAccel`/`stoppingDecelRate` are
  still live, non-deprecated fields in this repo's own
  `cereal/car.capnp`, actively read by
  `selfdrive/controls/lib/longcontrol.py`** — whether the
  `opendbc.car.structs.CarParams` → `cereal.car.CarParams` conversion this
  project uses actually threads a `deprecated.startingState` value through to
  `longcontrol.py` is unverified and out of scope here (see "Not done" below).
  `personality` (CarState) and `lkaDisabled` (CarState) are kommuai-only
  schema fields this fork's `car.capnp` never had at all and this repo's own
  `cereal/car.capnp` doesn't have either — dropped the assignments (BYD's
  distance-personality UI value is still reachable via the standard
  `buttonEvents`-based path; Chery's isn't, since it had no such fallback —
  purely cosmetic, not wired to any consumer in this project either way).
- `chery/values.py`'s `CarControllerParams` didn't dispatch to
  `MpcLkaCarControllerParams` for BYD's MPC_LKA platform the way
  `test_lateral_limits.py`'s generic `brand.values.CarControllerParams`
  lookup convention expects — added a `__new__` dispatch.
- Found and fixed a real pre-existing test bug while getting `test_chery.py`
  green: `TestCheryOmodaSafety`/`TestCheryOmodaNoTorqueSpoofSafety` asserted
  HUD (`0x387`) forwarding is blocked for Omoda, but `chery_fwd_hook`'s own
  comment says the opposite ("Omoda/iCaur: leave native HUD") — the test
  itself was stale relative to the code it tests. Fixed the assertions to
  match the code, not the reverse.
- `opendbc/car/torque_data/override.toml` needed BYD/Chery entries (missing
  `MAX_LAT_ACCEL_MEASURED` crashes `get_std_params()` for every car);
  `opendbc/car/tests/routes.py` needed the same `non_tested_cars` entries
  kommuai's own routes.py already carries (no comma.ai test route exists for
  either brand yet).

**Verified, not assumed:** full `opendbc/` test suite after the port —
3723 passed, 1718 skipped, 21098 subtests passed. Three known subfailures,
none from this port's own logic:
1–2. `CHERY_JAECOO_J7_PHEV` / `CHERY_TIGGO_8_PRO` fail
`test_can_fingerprint.py` — kommuai's own `chery/fingerprints.py` reuses
JAECOO J7's capture byte-for-byte for Tiggo 8 Pro (`FINGERPRINTS[TIGGO_8_PRO]
= FINGERPRINTS[JAECOO_J7_PHEV]`), so the two aren't uniquely identifiable by
CAN fingerprint alone. Documented in-file; not fabricated a fix — inventing
distinguishing signal data with no real capture behind it would be actively
dangerous for car identification.
3. `test_misra_mutation` fails on missing `cppcheck` binary in this sandbox —
   unrelated to any car brand, a pre-existing environment/tooling gap.

**Not done (explicitly out of scope, do not assume complete):**
- **EOP10's own submodule pin is unchanged** — `opendbc_repo` still points at
  `2cde2462` on `dev/EOP10`. The port lives on
  `exo-electronics/opendbc@port/upstream-bump-byd-chery-jaecoo`; bumping the
  pin (and re-verifying EOP10's own build/tests against the newer core, which
  is a much larger surface than byd/chery) is a separate, deliberate step.
- **No BrownPanda/TC375 firmware wiring.** `byd.h`/`chery.h` exist as C
  safety-mode source in the opendbc fork now, but `~/panda/TC375_BrownPanda`
  (the actual gateway firmware EOP10 builds, itself still "bring-up status,
  not yet flashable" per its own README) vendors its own separate copy of the
  safety framework and hasn't been touched. Nothing here makes BYD/Chery
  drivable on real EOP10 hardware.
- **No generic `car_helpers.get_car()` dispatch wired into EOP10 at all** —
  per `[[eop10-vehicled-socketd-migration]]`, EOP10 currently has a
  Tesla-only custom path (`system/socketd/vehicle/tesla/`), not the standard
  opendbc fingerprint-and-dispatch flow every other brand (including BYD and
  Chery) relies on. Making BYD/Chery actually selectable on EOP10 requires
  either restoring that generic dispatch or building brand-specific adapters
  the same way Tesla got one — out of scope for an opendbc-fork-level port.
- The cross-schema `startingState`/`startAccel`/`stoppingDecelRate` gap noted
  above (this repo's `cereal/car.capnp` vs. opendbc's now-deprecated
  equivalents) is unverified beyond "the values are set correctly on the
  `opendbc.car.structs` side" — whether they actually reach
  `longcontrol.py` was not traced.

No fixes applied to EOP10 itself this pass — all changes are on the
`exo-electronics/opendbc` fork's new branch, not merged to `master`, not
pinned into either `dev/EOP10` or `dev/EDP10`.

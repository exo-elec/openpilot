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

## Update (2026-08-13): MG added, EDP10's own patches ported, full regression clean — `dev/EDP10` submodule conversion still blocked

Full detail lives in `exo-electronics/opendbc`'s own
`docs/BYD_CHERY_JAECOO_PORT.md` (same branch,
[PR #1](https://github.com/exo-electronics/opendbc/pull/1)); this is the
cross-repo summary.

**Added since the 2026-08-12 update:**
- **MG (ZS EV + non-EV/ICE)** ported from dragonpilot's `opendbc_repo`.
  License note: dragonpilot's own original contributions (separate from the
  inherited comma.ai-derived base) carry a non-commercial `LICENSE.md`
  restriction (Copyright (c) 2019, Rick Lan) — this port was authorized
  directly by exo-electronics via a verbal arrangement with dragonpilot's
  author, not a written grant. Worth formalizing given exo-electronics'
  commercial context.
- **Three of `dev/EDP10`'s own hand-authored patches**, identified via a
  full 205-file audit of EDP10's `opendbc_repo` against its real parent
  (`dragonpilot`, not this fork — the lineages diverged ~1.5 years ago, so
  diffing directly against this fork is 1800+ files of noise): the VW PQ
  `HCA_Status` configurability patch, Toyota's DSU-disconnect capability
  (opt-in, gated purely by FW fingerprinting — inert for any Toyota that
  still has a live DSU), and GM Bolt EUV's neural-network lateral
  feedforward.
- **Two items from that same audit deliberately NOT ported**: the generic
  `radar_interface.py`/`u_radar.dbc` universal-radar-retrofit rewrite
  (dragonpilot-licensed, and out of scope — serves Toyota/GM/Chrysler/
  Rivian/Ford/Hyundai/Honda, unrelated to the Chinese-EV brands this port
  targets) and Toyota's ALKA/`zss.py` alternate steering-angle-source path
  (zero footprint anywhere in this fork or in `dev/EOP10`/`dev/NGP10`,
  dragonpilot itself keeps it present-but-disabled upstream for an
  unconfirmed reason — reintroducing it would be new safety-relevant
  complexity with no current consumer).

**A real cross-branch compatibility hazard, caught before merging:** GM's
neural feedforward needed a richer calling convention than this fork's
`torque_from_lateral_accel()` API provides. The first attempt changed that
API's base signature and removed `lateral_accel_from_torque()` entirely,
matching what `dev/EDP10`'s own (already-rewritten) `latcontrol_torque.py`
needs — but `dev/EOP10` and `dev/NGP10`'s `latcontrol_torque.py` still call
the *old* 2-arg convention and still use `lateral_accel_from_torque()` for
`pid.set_limits()`. Shipping that first attempt would have broken both
branches the next time either bumped its `opendbc_repo` submodule pin.
Redone as a strictly additive opt-in accessor
(`torque_from_lateral_accel_neural_fn()`, defaults to `None`) instead — see
the fork's own doc for the full contract. **This means the base API this
fork now exposes satisfies EOP10/NGP10's calling convention, but does
*not* satisfy EDP10's** — EDP10's own controller calls
`torque_from_lateral_accel()` and then invokes the result with the new
3-arg `LatControlInputs` signature directly, not through the opt-in path.

**Full regression, done properly this time:** earlier verification in this
port used `pytest`, which on this sandbox silently resolves to a mismatched
system Python 3.10 install for parts of the suite (masking failures in
anything importing `opendbc.car.rivian.values`, which needs `enum.StrEnum`,
Python 3.11+). This fork's actual CI runner is `unittest-parallel -j4`
(`lefthook.yml`/`test.sh`). Running it for real surfaced 3 genuine
failures, all fixed: the `CHERY_TIGGO_8_PRO`/`CHERY_JAECOO_J7_PHEV`
fingerprint collision noted above (Tiggo 8 Pro removed from CAN-fingerprint
auto-detection — confirmed via web search these are distinct vehicles, not
a rebadge, so merging their `PlatformConfig`s wasn't valid either; forced
selection only now, matching MG's existing pattern) and an MG_ZS ISO
11270 jerk-limit violation (`STEER_DELTA_UP=10`, dragonpilot's own value
ported verbatim, exceeds the jerk bound given MG_ZS's real measured
`MAX_LAT_ACCEL_MEASURED`; dragonpilot's own test suite has the identical
formula so this was a latent bug in the source, not introduced by porting —
reduced to 7). Current state: 4158 tests, 0 failures, 725 skipped.

**`dev/EDP10`'s own submodule conversion is a confirmed blocker, not just
"not yet done."** Checked directly by pointing EDP10's imports at this
fork's tree: at minimum, (1) every `dev/EDP10` brand's `interface.py` uses
the 8-arg `_get_params()` (`dp_params`); this fork uses 7 — every brand
breaks on the swap. (2) `dev/EDP10`'s `car.capnp` has no `deprecated`
group at all — fields this fork nests there (`.brake`, `.startingState`,
`.enableDsu`, etc.) stay top-level on EDP10, the mirror image of the
`AttributeError` hazard the Toyota DSU-disconnect port itself hit and fixed
this pass. (3) `dev/EDP10`'s `latcontrol_torque.py` calls
`torque_from_lateral_accel()` with the new 3-arg signature directly (see
above) — this fork's version of that method still returns the old 2-arg
callable, so EDP10's controller would raise `TypeError` on the first
steering-torque calculation for any car. None of this was attempted this
pass — it's real, scoped EDP10-side controller/schema work, not an opendbc
fork change, and shouldn't be rushed into the same session that found it.

Rest of the "Not done" list from the 2026-08-12 update is unchanged and
still accurate (EOP10's pin, BrownPanda/TC275 firmware wiring,
`car_helpers.get_car()` dispatch).

## Update (2026-08-13, continued): the `torque_from_lateral_accel` blocker investigated further — an adapter is viable, GM needs one confirmed decision first

Followed up on blocker (3) above to see whether it's actually fixable with a
small, local change to `dev/EDP10`'s `latcontrol_torque.py` (an adapter
calling the fork's old 2-arg API and reconstructing the 3-arg calling
convention EDP10 expects) rather than a larger rearchitecture. **The swap
itself was NOT performed — this was investigation and verification only,
run via two separate Python subprocesses (see note below) with results
diffed externally, no files changed in either `dev/EDP10` or the fork.**

**The adapter mechanism is proven correct for every brand except one.**
Checked which brands override the base `torque_from_lateral_accel()` in
either tree: only `opendbc/car/gm/interface.py`, in both. Every other
brand — the overwhelming majority of what `dev/EDP10` supports — inherits
the same base-class linear implementation, and a direct equivalence check
(same test inputs through EDP10's 3-arg native call and the proposed
adapter wrapping the fork's 2-arg call) matched bit-for-bit across 6 input
combinations x2 gravity-adjust states. So this audit is a full accounting
of where drift *can* hide, not a sample.

**GM's neural path (`CHEVROLET_BOLT_EUV`) also matched bit-for-bit** — no
adapter needed there at all, since the fork's opt-in
`torque_from_lateral_accel_neural_fn()` (added this session for the
EOP10/NGP10 compatibility contract, see above) was built by porting
EDP10's own GM neural code in the first place.

**GM's non-linear ("siglin") path — `GMC_ACADIA`/`CHEVROLET_SILVERADO` —
does NOT match, and the cause turned out to be interesting, not an
adapter bug.** Same underlying tuning constants (`[4.78003305, 1.0,
0.3122, 0.05591772]`) in both trees, but EDP10's formula silently drops
the 4th constant (`d`, a constant torque offset) that the fork's formula
still adds. Traced via `git log` on the fork (itself the current comma.ai
upstream, bumped 2026-08-10) — this isn't tuning drift, it's an *orphaned
upstream change*: comma.ai merged "Torque controller: refactor
calculations to be in accel space" (`74bfaa2c`, Shane Smiskol,
2025-08-15) — the exact `LatControlInputs`/`NanoFFModel`/dropped-`d`-term
pattern `dev/EDP10` has — then **reverted it three days later**
(`4e50498a`, Adeeb Shihadeh, 2025-08-18, pushed directly, no PR, no
stated reason in the commit message). `dev/EDP10` appears to have pulled
in `74bfaa2c` without the revert at some point in its history. The bare
revert doesn't explain *why* — that's a real limit on what's known here,
stated plainly rather than assumed — but a same-week revert by comma.ai's
founder is a strong signal something was wrong with it, and it means the
fork's current 2-arg-plus-`d`-term behavior is comma.ai's own considered,
currently-supported design, not something `dev/EDP10` is "ahead" of.

**What this means for the adapter approach:** it's sound, and adopting it
would very likely be a *correction* for GM Acadia/Silverado, not a
regression. But it does change those two cars' actual steering output
(the `d` term is not derivable from what EDP10 has today — it re-appears
in the calculation), and — unlike the Chery/BYD/MG work — GM support is a
mainstream, potentially-deployed brand. **The remaining open question,
which needs a direct answer before this proceeds, is whether `dev/EDP10`
is currently driving real GM Acadia/Silverado vehicles.** If not, this is
straightforward to schedule. If so, those two cars need an explicit,
informed decision before their steering formula changes underneath them.

**Also not yet done, now that the adapter's shape is known:** the adapter
can't actually be written into `dev/EDP10`'s `latcontrol_torque.py` yet —
it calls `CI.torque_from_lateral_accel_neural_fn()`, a method that only
exists in the fork's `interfaces.py`, not in `dev/EDP10`'s currently
vendored one. Writing the adapter and performing the submodule swap have
to happen as one atomic change, not two independent ones; doing only the
adapter first would leave `dev/EDP10` referencing a method that doesn't
exist yet.

## Update (2026-08-13, continued again): blockers (1) and (2) confirmed with exact locations — scope is bigger than the API surface alone

The torque-adapter investigation above only addresses blocker (3). Checked
blockers (1) and (2) directly against `dev/EDP10`'s `selfdrive/`/`system/`
(not just `opendbc_repo`) to find out whether they're contained to the
opendbc tree or reach further:

- **(1) `dp_params` reaches well outside `opendbc_repo`.**
  `selfdrive/car/card.py` builds a `dp_params` bitmask from **7 real,
  user-facing flags** (`structs.DPFlags.LateralALKA`, `.ToyotaLockCtrl`,
  `.ToyotaTSS1SnG`, `.ToyotaStockLon`, `.VagA0SnG`,
  `.VAGPQSteeringPatch`, `.VagAvoidEPSLockout`) and passes it into every
  brand's `_get_params()`/`get_params()` — this is a live, dragonpilot-
  style params-driven car-behavior toggle system, not internal opendbc
  plumbing. `selfdrive/car/tests/test_car_interfaces.py` and
  `test_models.py` also call `get_params(..., dp_params=0, ...)` directly.
  A naive submodule swap doesn't just change one function's arity — it
  deletes the `DPFlags` schema type and the 7 flags it carries, and
  `card.py`'s toggle-building logic has nowhere to route them.
  (Incidentally: `.VAGPQSteeringPatch` is the *exact* feature already
  ported to the fork as `VolkswagenFlags.PQSteeringPatch` — but wired
  there as a plain settable `CarParams.flags` bit, not through a
  `dp_params` toggle system the fork has no equivalent of. The two aren't
  drop-in compatible as-is.)
- **(2) `deprecated`-group fields are read directly in core longitudinal
  control**, not just inside brand `interface.py` files:
  `selfdrive/controls/lib/longcontrol.py` reads `CP.startingState` (lines
  29, 35), `CP.stoppingDecelRate` (line 75), and `CP.startAccel` (line 79)
  as top-level `CarParams` fields; `selfdrive/controls/lib/
  longitudinal_planner.py` reads `CP.vEgoStopping` (line 259) the same
  way. All four live under `.deprecated.` in the fork's schema. This is
  exactly the class of `AttributeError` the Toyota DSU-disconnect port
  hit and fixed this session (`ret.enableDsu` → `ret.deprecated.enableDsu`)
  — except in core stopping/starting deceleration control, not one
  brand's interface.

**Net effect: #19 is not "swap the tree, fix the torque adapter, done."**
It's the torque adapter (scoped and mostly verified above) *plus*
reconciling a real params-driven toggle system in `card.py` *plus* four
top-level-vs-`deprecated` field reads in core longitudinal control. None
of the three is individually large, but they're independent pieces of
`selfdrive/`-level work, not opendbc-fork-level work, and the full set
needs to land together for the swap to be safe. Still gated on the same
GM Acadia/Silverado deployment-status question from the update above
before scheduling any of it.

**Tooling note for future verification like this:** `dev/EDP10`'s and this
fork's `car.capnp` share an original capnp schema ID
(`car.capnp:0: failed: Duplicate ID @0x8e2af1e708af8b8d`) — they cannot
both be imported in the same Python process. Verification comparing the
two trees needs two separate subprocess runs with results serialized
(e.g. to JSON) and diffed externally, not a single script importing both.

# BYD Atto 3 flip + Chery/Omoda/iCAUR/JAECOO port (`dev/EDP10`)

**Status:** BYD Atto 3 lateral control live; Chery/Omoda/iCAUR/JAECOO added,
active from the start. Both vendored inline in this branch's
`opendbc_repo`, not yet a submodule of `exo-electronics/opendbc` — see
"Relationship to the `exo-electronics/opendbc` fork" below for why that
conversion hasn't happened yet.

## BYD Atto 3: dashcamOnly → live lateral control

`opendbc/car/byd/interface.py` was `ret.dashcamOnly = True` /
`SafetyModel.noOutput`. Flipped to `SafetyModel.byd` on explicit request —
`opendbc/safety/modes/byd.h`'s steering enforcement
(`steer_angle_cmd_checks_vm` + zone-LUT backstop) was already audited
complete for `0x1E2`/`0x316` (see
`docs/upstream-audit/NODE_02_byd_panda_safety.md` and
`NODE_06_edp10_net_new.md` on `dev/EOP10`). Longitudinal (`0x32E`) stays
off deliberately: `byd.h`'s `BYD_TX_MSGS` whitelist still only carries
`BYD_MPC_LATERAL_CMD`/`BYD_MPC_STATE`, so
`openpilotLongitudinalControl` would fail closed at panda's generic TX
check rather than transmit — flip that separately, once `0x32E` is added to
the whitelist and reviewed with the same rigor as the lateral path.

**Not driven on a real Atto 3.** Treat the steering constants (max_angle,
zone LUTs, slip_factor) exactly as their own comments flag them — some are
route-driven evidence, some are uncited placeholders.

### Real safety bug found and fixed: `byd_zone_interp` missing upper clamp

While investigating whether the steering backstop's speed-dependent limits
correctly taper both angle *and* rate with speed, `test_angle_violation`'s
speed=50 case surfaced a real bug: `byd_zone_interp()`'s speed-zone
interpolation extrapolated past the top breakpoint instead of clamping,
inverting the backstop and rejecting all valid steering commands above
~130 km/h. Traced empirically with `libsafety_py` (fed real controller
output through `safety_tx_hook`, confirmed reject; not assumed from reading
the code). Fixed with a matching upper clamp.

The same bug, independently, was found and fixed in two other places this
same pass: `exo-electronics/opendbc`'s own `byd.h` (same fork lineage,
same original source, same bug), and `~/panda/TC275_BrownPanda`'s deployed
firmware (`Safety_ZoneInterp()` in `DBC/safety.c`) — the actual gateway
hardware, a completely separate codebase that happened to share the same
interpolation pattern and the same bug.

## Chery/Omoda/iCAUR/JAECOO: new port, active from the start

`opendbc/car/chery/` + `opendbc/safety/modes/chery.h` added fresh — this
branch had no prior Chery support to flip. JAECOO is Chery's sub-brand
(same module, no separate directory). Unlike BYD's port here, kommuai's
source (the origin of this port's structure, via
`exo-electronics/opendbc`'s own parallel port — see below) had **no C-side
max-angle backstop at all**, only rate tapering. Built one from scratch:
`CHERY_ZONE_ANGLE_DEG`, a from-scratch max-angle backstop derived from real
per-model physics (`max_angle = accel * wheelbase * steerRatio / v^2`, ISO
~1.3g lateral-accel margin — the same physics BYD's own zone table uses),
using Omoda 5's wheelbase (2.63m, smallest of the platforms here) for a
conservative bound shared across all of them. Verified via `libsafety_py`
the same way the BYD zone-interp bug above was caught: real controller
output accepted, sudden jumps rejected, correct hold behavior past the top
breakpoint.

## Relationship to the `exo-electronics/opendbc` fork

The *same* BYD/Chery/JAECOO port (plus MG, plus several of this branch's
own patches) was independently ported onto `exo-electronics/opendbc`
(branch `port/upstream-bump-byd-chery-jaecoo`,
[PR #1](https://github.com/exo-electronics/opendbc/pull/1)) — the fork
`dev/EOP10`/`dev/NGP10` already submodule. The eventual goal is converting
*this* branch's vendored `opendbc_repo` to a submodule of that same fork,
so all three branches share one opendbc tree instead of three diverging
vendored copies.

**That conversion has not happened yet, and is confirmed blocked, not
just "not started."** Checked directly by pointing this branch's imports at
the fork's tree instead of its own vendored one — at minimum:

1. **`_get_params()` arg count.** Every `interface.py` on this branch uses
   8 positional args (includes `dp_params`, dragonpilot heritage); the fork
   uses 7. Every brand's `_get_params` breaks on a naive swap.
2. **No `deprecated` group.** This branch's `car.capnp` keeps `.brake`,
   `.startingState`, `.startAccel`, `.stoppingDecelRate`, `.vEgoStopping`,
   `.enableDsu` etc. as plain top-level `CarParams`/`CarState` fields. The
   fork's newer core nests all of these under a `deprecated :group` —
   accessed as `ret.deprecated.X`. Swapping the vendored tree for the
   submodule would silently break every consumer of these fields on this
   branch (`selfdrive/controls/lib/longcontrol.py` reads
   `startingState`/`startAccel`/`stoppingDecelRate` directly, for example).
3. **`torque_from_lateral_accel()` calling convention.** This branch's own
   `selfdrive/controls/lib/latcontrol_torque.py` calls
   `CI.torque_from_lateral_accel()` and then invokes the result with the
   *new* 3-arg `LatControlInputs(lateral_acceleration, roll_compensation,
   vego, aego)` signature directly — this branch's own `interfaces.py` was
   rewritten at some point to need only the forward direction and dropped
   `lateral_accel_from_torque()` entirely. The fork, by contrast,
   deliberately **kept** the old 2-arg signature and `lateral_accel_from_torque()`
   present and unchanged, because `dev/EOP10`/`dev/NGP10`'s own
   `latcontrol_torque.py` still call the old convention — the fork's Bolt
   EUV neural feedforward (which needed the richer convention) was added as
   a strictly additive opt-in accessor instead of changing the shared base
   API. That decision protects EOP10/NGP10 but means this branch's
   controller would raise `TypeError` (unexpected positional arg) on the
   very first steering-torque calculation for any car, immediately after a
   naive submodule swap.

None of this is a reason not to do the conversion eventually — it's the
actual scope of that work. Fixing it requires either adapting this
branch's `latcontrol_torque.py`/`car.capnp` consumers to the fork's
current API shape, or extending the fork with EDP10-compatible opt-in
accessors the same way GM's neural FF was added — a deliberate, separate
pass, not a mechanical `.gitmodules` edit. See
`exo-electronics/opendbc`'s own `docs/BYD_CHERY_JAECOO_PORT.md` for the
fork-side detail, and `dev/EOP10`'s
`docs/upstream-audit/NODE_03_opendbc_submodule_vendoring.md` for the
cross-branch summary.

### Follow-up (2026-08-13): the adapter is provably viable, except for one open question on GM

Investigated (not implemented) whether blocker 3 above can be closed with
a small local adapter in this branch's `latcontrol_torque.py`, wrapping
the fork's 2-arg API to reconstruct the 3-arg calling convention this
branch's controller expects. Verified empirically, in two separate Python
subprocesses with results diffed externally (this branch's and the fork's
`car.capnp` share a capnp schema ID and cannot both be imported in one
process — `car.capnp:0: failed: Duplicate ID @0x8e2af1e708af8b8d`):

- Every brand except GM inherits the identical base-class linear
  implementation in both trees — bit-for-bit match, 12/12 test cases. GM
  is the *only* brand overriding `torque_from_lateral_accel` in either
  tree, so this is a complete accounting of where drift can hide, not a
  sample.
- GM's neural path (`CHEVROLET_BOLT_EUV`) also matches exactly — no
  adapter needed, since the fork's opt-in accessor was built by porting
  this branch's own GM neural code.
- GM's non-linear ("siglin") path — `GMC_ACADIA`/`CHEVROLET_SILVERADO` —
  does **not** match: this branch's formula silently drops a constant
  offset term (`d`) that the fork's formula still adds, using identical
  underlying tuning data. Traced to comma.ai's own history: this branch
  carries PR #2528 ("Torque controller: refactor calculations to be in
  accel space", `74bfaa2c`, 2025-08-15) — the exact
  `LatControlInputs`/dropped-`d`-term pattern this branch has — **without
  its revert three days later** (`4e50498a`, 2025-08-18, pushed directly,
  no stated reason). The fork's current behavior is comma.ai's own
  considered, currently-supported design; adopting it for Acadia/Silverado
  would be a correction, not a regression — but it's still an unverified
  reason for a bare revert, and it does change those two cars' actual
  steering output.

**Not resolved: whether this branch currently drives real GM Acadia or
Silverado vehicles.** If not, the adapter + submodule swap can proceed
once scheduled. If so, those two cars need an explicit, informed decision
before their steering formula changes. The adapter itself can't be
written before the swap either way — it calls
`torque_from_lateral_accel_neural_fn()`, which only exists in the fork's
`interfaces.py`, not in this branch's currently vendored one — so the
adapter and the swap have to land as one atomic change.

## Validation

`opendbc/safety/tests/test_chery.py` and BYD's angle/zone-interp tests
green via direct `libsafety_py` round-trips (accept real controller
output, reject out-of-bounds commands, correct clamp behavior past the top
breakpoint) — this branch has no `unittest-parallel`/CI config of its own
to run a full-suite sweep the way `exo-electronics/opendbc` does, so
verification here was targeted at the specific files changed rather than a
full-repo regression.

# Node 5 — capnp ordinal / `params_keys.h` collision table across branches

## `params_keys.h` — ✅ pass, no collision risk

String-keyed `std::unordered_map`, so there's no positional-collision failure
mode the way capnp ordinals have. Checked the one key that's genuinely
supposed to be identical across all three branches: `ngp_lon_brsc` —
present verbatim (`{"ngp_lon_brsc", {PERSISTENT, BOOL, "1"}}`) on both EDP10
and NGP10, same type, same default. NGP10's other `ngp_*` keys and EDP10's
`dp_*` keys don't overlap in name. No fix needed.

## capnp ordinals — 🟢 downgraded and partially resolved (2026-08-08)

**Update:** ran the discriminating check — does any tool in this repo
actually decode an rlog produced by one branch's build using a *different*
branch's compiled cereal bindings? Searched `tools/replay/`,
`selfdrive/test/process_replay/`, `selfdrive/debug/touch_replay.py`: all are
branch-local (each branch compiles and runs its own copy against its own
schema). No cross-branch log-ingestion/replay tool exists in this repo. The
cross-fleet precedent cited below (`surface_quality_db.py`,
`exopilot02m` tags) is a different mechanism — a tagged database row, not a
raw capnp message decoded with a foreign schema — so it doesn't establish
the same risk. **The collision is real at the schema level (confirmed
below) but currently has no live trigger.**

Applied the cheap, no-coordination-needed part of the fix on EOP10: reworded
`log.capnp`'s BRSC comment from "shared via nagaspilot/controls" (readable
as a wire-compatibility claim) to explicitly state that the **Python source**
is shared byte-identical but the **wire ordinals are not**, with a pointer to
this doc. Did not touch EDP10/NGP10's copies of the same comment (not
checked out in this worktree) or attempt an ordinal renumber (a schema
change like that should be a deliberate one-time move across all three
branches together, not something to do piecemeal from one branch). If a
cross-branch log tool is ever built, reserve a shared ordinal block before
that tool ships — the risk becomes live at that point, not before.

**Original finding (schema-level facts, still accurate):** same field name,
different wire slot, across branches; one confirmed same-slot/different-field
collision.

capnp ordinals (`@N`) determine each field's position in the struct's wire
layout **as compiled by that branch's own schema**. Within a single branch,
building and running end-to-end, this is entirely self-consistent — no
runtime bug on any one branch alone. The risk is specific to **any tool or
process that decodes a log/message produced by one branch's schema using a
different branch's compiled bindings** — a real pattern here, given this
project's existing cross-fleet log-sharing/tagging work (see
`DELTA_AUDIT.md`'s "Fleet-interop code intentionally still references
rk3576/exopilot02m" note, `surface_quality_db.py`, and the VisionPilot
cross-repo naming-consistency effort). If a shared replay/analytics tool
ever reads NGP10 or EDP10 logs with EOP10-compiled cereal bindings (or vice
versa), any field whose ordinal disagrees between the producing and
consuming schema will be silently misread as whatever field occupies that
slot in the *reading* schema.

### Confirmed same-slot collision (not just drift — an actual reused ordinal)

`ControlsState` struct, ordinal **`@67`**:

| Branch | Field at `@67` | Type |
|---|---|---|
| EOP10 | `tjaActive` | `Bool` |
| NGP10 | `ngpAlccActive` | `Bool` |
| EDP10 | *(ControlsState untouched)* | — |

Both are `Bool`, so a cross-schema misread wouldn't crash — it would
**silently substitute the wrong signal** (TJA-active for ALCC-active or vice
versa) with no type error to catch it.

### `ngpBrscActive`/`ngpBrscSpeed`/`ngpBrscRoughness` — same field name, three different ordinals

The capnp comment on all three branches literally says `shared via
nagaspilot/controls` — same framing issue as Node 4's `tja` finding, but at
the wire-schema level instead of the source-file level:

| Branch | `LongitudinalPlan` ordinals |
|---|---|
| EOP10 | `@66 / @67 / @68` |
| EDP10 | `@40 / @41 / @42` |
| NGP10 | `@46 / @47 / @48` |

And each branch's own conflicting occupant of the *other* branches' slots:

| Ordinal | EOP10 | EDP10 | NGP10 |
|---|---|---|---|
| `@40` | `dlonMode` (Text) | `ngpBrscActive` (Bool) | `ngpDlonMode` (Text) |
| `@41` | `dlonE2EEnabled` (Bool) | `ngpBrscSpeed` (Float32) | `ngpDlonE2EEnabled` (Bool) |
| `@42` | `sqscActive` (Bool) | `ngpBrscRoughness` (Float32) | `ngpDlonForceStop` (Bool) |
| `@46` | `vtscSpeed` (Float32) | — | `ngpBrscActive` (Bool) |
| `@47` | `vtscUsingLearned` (Bool) | — | `ngpBrscSpeed` (Float32) |
| `@48` | `mtscActive` (Bool) | — | `ngpBrscRoughness` (Float32) |
| `@66` | `ngpBrscActive` (Bool) | — | — |
| `@67` | `ngpBrscSpeed` (Float32) | — | — |
| `@68` | `ngpBrscRoughness` (Float32) | — | — |

`@40`/`@41` are the worst case: types happen to coincide closely enough
(`Bool`/numeric) between some pairs that a cross-schema decode wouldn't even
throw — it would produce a plausible-looking but wrong value.

### Root cause

Each branch appends new `LongitudinalPlan`/`ControlsState` fields at
"whatever the next free ordinal is" independently — reasonable in isolation
(capnp ordinals only need to be unique *within* one schema, and each branch
today only runs its own build against its own logs), but it means there is
no reserved/shared ordinal range for fields the project has explicitly
documented as cross-branch-shared (BRSC). This is the wire-format analogue
of Node 4's `tja` filename/location fragmentation: the *intent* is "shared,"
the *implementation* is "independently forked and already diverged."

**Recommendation (not applied — schema change needs a decision, and would
require a coordinated bump across all three branches, which is exactly the
kind of cross-branch action this audit should surface rather than take
unilaterally):** either (a) reserve a fixed ordinal block for
nagaspilot-shared fields and renumber `ngpBrscActive`/`Speed`/`Roughness` to
match across all three branches now, while the field count is still small,
or (b) if cross-branch log interop is genuinely never going to happen,
remove the "shared via nagaspilot/controls" wording from the capnp comments
so it stops implying a wire-compatibility guarantee that doesn't exist. If
(a), the same reserved-block treatment should be applied to future
nagaspilot-shared fields (a `tja` capnp wiring, if `tja.py` is ever
consolidated per Node 4, would be the next candidate).

---

**Node status: done.** `params_keys.h`: clean. capnp: 1 confirmed same-slot
collision (`ControlsState.@67`), plus a 3-way ordinal mismatch on fields
explicitly documented as shared, requiring a cross-branch decision to fix.

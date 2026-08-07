# Cross-Branch First-Principles Audit — EDP10 / NGP10 / EOP10

**Goal:** verify every branch-edited line since each branch's "0.10.0" fork point is
correct, load-bearing, and layered in the right place — not just "does it run,"
but "is this the right design." Written one node at a time so partial progress
survives a session boundary. Each node below becomes its own `NODE_*.md` file
(or a section in one, for small nodes) with findings, verdict, and fixes applied.

## Scope note — the three branches are not symmetric

Do not audit these as three equal-sized deltas. Treat them differently:

| Branch | Base tag | Base date | Diff vs base | Existing audit coverage |
|---|---|---|---|---|
| `dev/EDP10` | `base/dev-EDP10-dragonpilot-0.10.0` | 2025-09-30 | 46 files / +3358 / -7 | **none** — first pass |
| `dev/NGP10` | `base/dev-NGP10-openpilot-v0.10.0` | 2025-08-19 | 72 files / +3636 / -19 | **none** — first pass |
| `dev/EOP10` | `base/dev-EOP10-openpilot-v0.10.0` | 2025-08-19 | 1315 files / +149720 / -54285 | **extensive** — see `DELTA_AUDIT.md`, `CONTROLS_AUDIT.md`, `FINAL_AUDIT_2026-06-10/`, `COMMIT_*_REVIEW.md` (dozens of files), memory `eop10-final-audit-in-progress.md` (D1–D41) |

**Important:** "based on 0.10.0" means two different upstreams. EDP10 forked from
**dragonpilot's** 0.10.0 tag; NGP10/EOP10 forked from **comma's** 0.10.0 tag. A
line-count diff between EDP10's and NGP10's version of the same file (e.g.
`longitudinal_planner.py`: +57 vs +101) conflates their actual additions with
dragonpilot-vs-comma baseline drift. Where this matters, compare **added hunks**
or the new-file policy modules (baseline-independent) instead of raw diffstat.

**EOP10 handling:** do NOT re-derive the already-audited 150K-line bulk. This
audit covers only:
1. The commits after `1d5f050ef` (EOP10's last audited commit per `DELTA_AUDIT.md`)
   through current tip `ba0151e03` (~15 commits: BRSC, convoy follow, radar4d
   weather severity, BrownPanda steering-limit refs, dev-PC build fixes, NGP10
   panel doc alignment).
2. The working tree as of 2026-08-08 (10 modified + 5 untracked files — see
   `NODE_08_eop10_delta.md`).

This is an explicit coverage claim, not a silent scope cut — flag to the user if
broader EOP10 re-audit is wanted.

## Base commit resolution

```
EDP10: git merge-base base/dev-EDP10-dragonpilot-0.10.0 dev/EDP10
NGP10: git merge-base base/dev-NGP10-openpilot-v0.10.0 dev/NGP10
EOP10: git merge-base base/dev-EOP10-openpilot-v0.10.0 dev/EOP10
```
All three base tags confirmed ancestors of their branch (checked 2026-08-08).

**Mechanics:** EDP10/NGP10 are read via `git show <branch>:<path>` and
`git diff <base> <branch> -- <path>` from the EOP10 working tree — **no checkout,
no stash**, since EOP10 currently has uncommitted work in progress.

---

## Node list

| # | Node | Branch(es) | Priority | Status |
|---|------|-----------|----------|--------|
| 1 | Index skeleton (this file) | — | — | ✅ done |
| 2 | Panda safety: `byd.h` + `can_common.h` layering | EDP10 | 🔴 highest — physical safety | ✅ done — pass, no bugs |
| 3 | `opendbc_repo` submodule vendoring divergence | EDP10 vs NGP10 | 🟠 high — structural | ✅ done — inherited from base, not a bug |
| 4 | Three-way identity: `ngp_brsc.py`, `speed_zones.py`, `*_tja.py` naming | EOP10/NGP10/EDP10 | 🟠 high | ✅ done — 1 finding (tja fragmentation) |
| 5 | capnp ordinal + `params_keys.h` collision table | EDP10 vs NGP10 vs EOP10 | 🟠 high — silent data corruption risk | ✅ done — 1 finding (ordinal collisions) |
| 6 | EDP10 net-new: BYD car port + `dp_tja` + planner hooks | EDP10 | 🟡 medium | ✅ done — 1 finding (TJA no toggle) |
| 7 | NGP10 net-new: `ngp_suite` controllers + gridd/adaptd wiring | NGP10 | 🟡 medium | ✅ done — 2 findings (dead toggle, untoggleable trigger) |
| 8 | EOP10 delta since `1d5f050ef` + working tree | EOP10 | 🟡 medium (bounded scope, see above) | ✅ done — 1 finding (orphaned tracking hash); partial coverage on working tree |

**Legend:** ✅ done · 🔄 in progress · ⏳ pending · 🔴/🟠/🟡 priority (safety > structural/cross-branch > feature-local)

---

## Findings summary (filled in as nodes complete)

**Nodes 2-5 done (cross-branch consistency sweep). 2 clean, 2 real findings
needing a user decision, 0 blocking safety bugs:**

- ✅ **Node 2 (panda safety, `byd.h`/`can_common.h`):** pass. Correctly
  layered, decode fields cross-verified against DBC + `carstate.py`, safety
  limits exceed their own cited precedent's rigor.
- ✅ **Node 3 (`opendbc_repo` vendoring):** not a bug — EDP10's lack of
  `.gitmodules`/submodules is inherited unchanged from dragonpilot's own
  0.10.0 base, not introduced by branch edits.
- 🟠 **Node 4 (`tja` naming/location fragmentation):** `tja.py` /
  `dp_tja.py` / `ngp_tja.py` are logic-identical but live in 3 different
  paths under 3 different names, despite EOP10's own docstring calling it
  "shared by EOP, EDP, and NGP." Not wired as one file the way `ngp_brsc.py`
  is — a fix to one won't propagate. `TJA.md` is also stale (describes an
  older `longcontrol.py`-integrated design). **Needs a decision**: consolidate
  to one shared file/location, or explicitly document it as intentionally
  forked.
- 🟠 **Node 5 (capnp ordinal collisions):** `ControlsState.@67` is
  `tjaActive` on EOP10 and `ngpAlccActive` on NGP10 — a confirmed same-slot
  collision. `ngpBrscActive/Speed/Roughness` (a field explicitly commented
  "shared via nagaspilot/controls") sits at 3 different `LongitudinalPlan`
  ordinals per branch (EOP10 `@66-68`, EDP10 `@40-42`, NGP10 `@46-48`), each
  colliding with a different real field in the other branches' schemas. Only
  a risk if any tool ever cross-decodes logs between branches, but this
  project has real cross-fleet log-sharing precedent (`surface_quality_db.py`,
  VisionPilot naming-consistency work), so **not hypothetical**. **Needs a
  decision**: reserve a shared ordinal block and renumber now while the
  field count is small, or drop the "shared" wording from the capnp comments.

**All 8 nodes done.** Full results below, ranked by what needs a decision
first.

### Resolved this session (2026-08-08)

- **`speed_limit` DLON trigger — implemented on both branches** (Node 7).
  Root-caused first: vestigial on **both** EOP10 (original `dlon.py`) and
  NGP10 (its port), not a porting omission — initially deleted as dead code.
  User then asked what it was meant to detect (36 m/s cap, or a real mapped
  limit?), which surfaced that this codebase already has real map/nav
  speed-limit infrastructure (`nslc.py`) that DLON never used. Implemented
  `detect_speed_limit_trigger()` on both branches instead of leaving it
  deleted: reads `mapData.speedLimit` (preferred) → `navInstruction.speedLimit`
  (fallback), same source/unit handling as `nslc.py`, fires when the limit
  is >2 m/s below current speed. Wired into the same filter/toggle/priority
  machinery as every other DLON trigger. NGP10's `plannerd.py` additionally
  needed a `mapData` subscription it never had, plus `navInstruction` moved
  into `ignore_alive` to match EOP10's existing pattern. Verified by
  exercising the trigger logic directly on both branches (full pytest
  harness blocked by a pre-existing, unrelated capnp/`params_pyx` build
  issue, confirmed pre-existing on both worktrees). Left uncommitted at
  first, then committed and pushed per explicit request — see below.
- **capnp "shared via nagaspilot/controls" wording** (Node 5) — ran the
  discriminating check: no tool in this repo decodes one branch's logs with
  another branch's cereal bindings (`tools/replay/`, `process_replay`,
  `touch_replay.py` are all branch-local), so the collision risk is real at
  the schema level but not currently live. Reworded EOP10's `log.capnp`
  comment to state plainly that the *Python source* is shared but the *wire
  ordinals* are not — cheap, no cross-branch coordination needed. Same
  branches' comments (EDP10/NGP10) not touched from here. Ordinal renumber
  itself deliberately not attempted — a schema change like that should be
  one deliberate move across all three branches together, not applied
  piecemeal.
- **`DELTA_AUDIT.md`'s orphaned tracking hash** (Node 8) — added a
  prominent note at the top of the STATUS TRACKING section explaining
  `1d5f050ef` is unreachable after a history rewrite and pointing future
  sessions at `858419d7d` (last reachable pre-rewrite topic commit) instead.
- **BLE central msgq single-publisher check** (Node 8 coverage gap, closed)
  — verified `BLECentral.__init__` never constructs a `PubMaster`; it's only
  built inside `start()`, itself gated behind `EOPBluetoothRadarEnabled`
  (default off) at two levels. Grepped for other `radar2d` publishers: none.
  Clean — the exact bug class `CLAUDE.md` records as having crashed msgq
  once does not recur here.

### Findings still requiring a decision (not auto-fixed — cross-branch or design calls)

1. **`tja` naming/location fragmentation** (Node 4) — `tja.py` (EOP10) /
   `dp_tja.py` (EDP10) / `ngp_tja.py` (NGP10) are logic-identical but three
   independently-copy-pasted files despite EOP10's own docstring calling it
   "shared." A fix to one won't propagate to the others. `TJA.md` is stale
   and doesn't describe the current implementation at all — **holding off on
   rewriting it** until the consolidate-or-fork decision is made, since
   documenting the fragmentation as-is would cement it. **Decide:**
   consolidate to one canonical file/location (matching `ngp_brsc.py`'s
   pattern), or document the fork as intentional.
2. **TJA has no user-facing toggle** (Node 6/7) — consistent across both
   branches that implement it (no `dp_lon_tja`/`ngp_lon_tja` param, no panel
   entry), and NGP10's own doc confirms this is intentional ("TJA has no
   backing param on any branch (always active, not user-toggleable)"). Not a
   bug — folds into item 1's `TJA.md` rewrite once that's decided.
3. **`stop_prediction` toggle doesn't actually gate `force_stop`** (Node 7,
   sharpened 2026-08-08) — traced the data flow rather than just noting the
   toggle asymmetry: `force_stop_recommended` depends on
   `_has_traffic_control` and `_should_stop`, and **both** reach
   `detect_stop_prediction()` directly, bypassing
   `self._trigger_enabled['stop_prediction']` entirely. That toggle is only
   checked in one place in the whole file (`_evaluate_auto_mode`'s own E2E-
   mode-switch line). Concrete, verified claim: setting
   `EOPDLONStopPredictionEnabled=0` / `ngp_lon_dlon_stop_prediction=0` does
   **not** stop the car from force-stopping on the same underlying signal —
   confirmed identical on both EOP10 (live production branch) and NGP10.
   The actual `force_stop` feature gate is the separate
   `EOPDLONForceStopsEnabled` master switch, which works correctly. Not
   fixed here — the right resolution (gate `force_stop` behind the same
   toggle? give `traffic_control` its own toggle? rename to clarify scope?)
   is a deliberate call to make on the production branch, not something to
   apply unilaterally mid-audit.
4. **capnp ordinal renumber — declined, not open.** Schema-level facts still
   stand (confirmed same-slot collision at `ControlsState.@67`; `ngpBrsc*`
   at 3 different `LongitudinalPlan` ordinals per branch), and the
   misleading comment wording is fixed on EOP10 (see above). But this
   audit's own discriminating check (Node 5) already found no cross-branch
   log tooling exists in this repo, so a proactive renumber has no present
   payoff — closing this out rather than leaving it listed as open
   busywork. Revisit only if cross-branch log replay/analytics is ever
   actually built.

### Clean passes (no action needed)

- Node 2: `byd.h`/`can_common.h` panda safety — correctly layered, decode
  verified against DBC + `carstate.py`, exceeds its own cited precedent's
  rigor.
- Node 3: `opendbc_repo` submodule vendoring difference — inherited from
  dragonpilot's own base, not introduced by EDP10.
- Node 4: `ngp_brsc.py` — byte-identical across all 3 branches, exactly as
  documented. `speed_zones.py` size difference — legitimate BYD-only
  additions, not drift.
- Node 5: `params_keys.h` — no collision risk (string-keyed), `ngp_lon_brsc`
  consistent across branches.
- Node 6: BYD Atto 3 car port — dashcam-only today (can't reach a real CAN
  bus), unusually rigorous self-documentation of every unverified constant.
  Planner wiring (BRSC/TJA composition into `longitudinal_planner.py`) is
  correctly ordered (`min()`-chained caps, always-compute/conditionally-apply
  split).
- Node 7: ALCC/LCA-auto/road-edge wiring in `controlsd.py`/`desire_helper.py`
  all correct against documented intent; `plannerd.py` flag-read-once
  behavior is documented and intentional, not a bug.
- Node 8: working-tree radar2d corner-fusion math (rotation transform,
  dispatch logic, fail-closed placeholder fallback) and the new BLE central
  module's authorization core — both correct against their own stated design.

### Explicitly out of scope / not exhaustively covered (say so, don't imply otherwise)

- EOP10's pre-existing 150K-line bulk (everything through `858419d7d`) —
  covered by prior sessions' `FINAL_AUDIT_2026-06-10/` + `COMMIT_*_REVIEW.md`
  work, not re-derived here.
- EOP10's 26 committed-but-recent commits (`858419d7d..ba0151e03`) — relied
  on `DELTA_AUDIT.md`'s existing session log rather than re-verifying from
  scratch.
- `ble_central.py`'s D-Bus/GLib connection state machine (~half the file),
  `ncp_session.py`, `protocol.py` — not reviewed this pass.
- NGP10's other 11 controller files beyond `ngp_dlon.py` — not individually
  reviewed line-by-line (spot-checked via integration points instead).
- EDP10's `bydcan.py` (CAN frame packing) — not reviewed; `carcontroller.py`/
  `carstate.py`/`values.py`/`interface.py` were.

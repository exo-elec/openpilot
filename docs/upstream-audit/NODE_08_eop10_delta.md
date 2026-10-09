# Node 8 — EOP10 delta since last audited commit + working tree

## 🔴 Finding: `DELTA_AUDIT.md`'s tracked "last audited commit" hash is orphaned

Per the advisor's instruction, verified `1d5f050ef` (the commit `DELTA_AUDIT.md`'s
Step-progress table records as the most recently audited) against actual git
history rather than trusting the doc or memory (which separately gave a
*third*, also-stale hash, `68fd0377c`):

```
git merge-base --is-ancestor 1d5f050ef ba0151e03   →  NOT an ancestor
git branch --contains 1d5f050ef                    →  (no branches)
```

`1d5f050ef` is a dangling commit, unreachable from any current branch. This
is a direct consequence of the branch history rewrite `DELTA_AUDIT.md`'s own
header describes ("rebuilt the branch as 12 clean topic commits, tree-identical
to the audited target") — everything before that rebuild now lives under
different commit hashes (`063b722ac` … `858419d7d`, the ~13 large topic
commits visible at the bottom of `git log`), and `1d5f050ef` (part of the
pre-rebuild sequence of `[AUDIT-REVERT]`/review commits) was left behind.

**Practical effect:** `git log 1d5f050ef..HEAD` does not compute a meaningful
delta — it silently returns the *entire* branch history, not just what's new.
Anyone (agent or human) re-deriving "what's changed since the last audit" by
hash from `DELTA_AUDIT.md` will get the same false-full-history result. This
audit instead scoped the delta **by content**, cross-referencing
`DELTA_AUDIT.md`'s own most-recent running session-log entries (BRSC, convoy
follow, radar4d weather severity spread, BrownPanda references, dev-PC build
fixes, TC375 radar hardening, socketd/vehicled boundary work) against
`git log --oneline`, and confirmed they line up cleanly with the 26 commits
from `858419d7d` (last old topic commit, still reachable) through `ba0151e03`
(current tip). That range is what "the delta" means below.

**Recommendation:** `DELTA_AUDIT.md` should record a *reachable* boundary
commit (`858419d7d`, or re-anchor on every future rebase) rather than a
review-commit hash that a later rebuild can orphan.

## Committed delta (`858419d7d..ba0151e03`, 26 commits) — not re-verified line-by-line this pass

`DELTA_AUDIT.md`'s own session log already documents this range's content in
detail, including bugs found and fixed at the time (BrownPanda radar parsing,
TC375 stream hardening, socketd vehicle-boundary migration, dev-PC build ABI
fixes, radar4d_geometry RPY rotation fix, BRSC, convoy follow). Given the
effort already spent on nodes 2-7 and this node's working-tree review below,
this pass relied on that existing written record rather than re-deriving each
commit from scratch — **this is a coverage gap, not a clean-pass claim.** If
a from-scratch re-verification of this specific range is wanted, it should be
its own follow-up node.

## Working tree (uncommitted, 2026-08-08) — reviewed

10 modified + 5 untracked files, two features in progress:

### radar2d corner-fusion (`gridd.py`, `radar4d_geometry.py`, `custom.capnp`) — ✅ pass

- `_fuse_radar2d`'s dispatch (`len(radar2d.objects) > 0` → tracked-object
  path, else → legacy zone-presence path) matches the capnp schema comment's
  stated intent exactly ("`objects` carries real corner-tracked objects...
  `returns` stays as the legacy zone-presence fallback"). Checked the edge
  case where a modern node has zero detections this frame and would fall
  through to the legacy path: harmless, since `_fuse_radar2d_returns` already
  gates on `r.present`, so an empty-but-real `returns` list from a modern
  node produces no spurious fusion either way.
- Sensor→vehicle-frame rotation in `_fuse_radar2d_objects`
  (`d_rel = px + sx*cos(yaw) - sy*sin(yaw)`, `y_rel = py + sx*sin(yaw) + sy*cos(yaw)`)
  is the correct standard 2D CCW rotation-then-translate, consistent with the
  comment's stated `yaw_deg CCW` convention and the project's established
  x-forward/y-left vehicle frame.
- `_R2D_CORNER_POSE` placeholder values are explicitly marked as
  placeholders pending real extrinsic calibration — same honesty convention
  praised in Node 2/6's review of `byd.h`/`values.py`.
- `load_corner_poses()` (`radar4d_geometry.py`) fails closed to `None` on
  *any* missing/malformed field for *any* corner (not just the one that's
  bad), which is the conservative choice — a partially-garbled registry
  falls back entirely to the placeholder table rather than mixing real and
  placeholder poses per corner.
- **UPDATE (2026-08-08, later same day): `load_corner_poses()` was defined
  but never called anywhere in `gridd.py` at review time above —
  `_fuse_radar2d_objects` read `self._R2D_CORNER_POSE` (the class-level
  placeholder) unconditionally, so the registry adapter existed but was
  dangling, dead code from `gridd.py`'s perspective; the live/on-road-
  calibrated corner poses `visionpilot` writes were never actually
  reaching this fusion path.** Fixed: `GridD.__init__` now calls
  `load_corner_poses()` once at startup and stores the result (or the
  placeholder table, on `None`) as `self._r2d_corner_pose`, which
  `_fuse_radar2d_objects` reads instead of the class constant directly.
  Same fail-closed, all-or-nothing semantics as before — this only wires
  up what was already there, no change to the loader's own logic.
- **UPDATE (2026-08-10): the move to `self._r2d_corner_pose` above broke
  `test_fuse_radar2d.py`'s `_FuseHost`**, a lightweight test double that
  mirrors `GridD`'s class-level constants to avoid constructing a full
  `GridD` (heavy messaging/costmap setup in `__init__`) — it never gained
  the new instance attribute, so every test using it hit `AttributeError`.
  Fixed by adding `_r2d_corner_pose = GridD._R2D_CORNER_POSE` to
  `_FuseHost`, the same placeholder-table fallback `GridD.__init__` itself
  uses when the shared registry is absent. Caught by re-running
  `selfdrive/gridd/tests/` after this node's fixes, not at review time.

### BLE central for ESP32 corner radars (`ble_central.py`, new, 812 lines) — spot-checked, not exhaustive

This is a large, unusually well-threat-modeled new module (explicit
cross-vehicle-confusion defense: dwell timer + WiFi-MAC-roster identity
check + RSSI-as-advisory-only, matching this codebase's now-familiar pattern
of citing evidence for every non-obvious constant). Given its size, this
pass checked the highest-stakes logic — the authorization predicate — rather
than every line:

- `CornerPairTable.is_allowed()` matches its own docstring's stated 3-way
  rule exactly: paired → allow; pair set empty (bootstrap) → allow;
  otherwise → only if `pairing_open`. An unidentifiable (`None`) address is
  never allowed.
- `check_learn_eligibility()` matches the docstring's stated dwell + roster
  identity factors; RSSI is correctly excluded from the eligibility
  boolean and left purely advisory, matching the docstring's explicit
  justification (BLE RSSI error of 1-10 m makes it useless as a proximity
  gate).
- `CORNER_TO_SIDE` translation table between the ESP32 wire enum
  (0=FL,1=FR,2=RL,3=RR) and `Radar2DReturn.side`'s different enum
  (0=LF,1=LR,2=RF,3=RR) is called out in a code comment as the one place
  this must happen — checked the mapping by hand against both enums'
  documented orderings and it's correct (FL→LF, RL→LR, FR→RF, RR→RR).
- `bluetoothd.py`'s wiring creates `ble_central` **before** the shared
  `NCPSession` specifically so the session can serve `RADAR_PAIR_CONTROL`/
  `RADAR_PAIR_STATUS` against it — correct ordering for that dependency,
  and the module docstring's "MSGQ SINGLE-PUBLISHER WARNING" cross-references
  exactly the class of bug `CLAUDE.md` records as already having bitten this
  project once (multiple `PubMaster` instances for one service).

**Not reviewed in this pass:** the BLE central's D-Bus/GLib connection state
machine, reconnect backoff, and GATT notification callback wiring (roughly
half the file) — flagging as unreviewed rather than claiming a clean pass on
code not actually read line-by-line.

**Update (2026-08-08) — targeted msgq single-publisher check:** the module's
own docstring warns about the exact bug class `CLAUDE.md` records as having
crashed msgq on boot once (multiple `PubMaster` instances for one service).
Checked directly: `BLECentral.__init__` sets `self._pm = None` (comment:
"created in start() — never a publisher while disabled"); `PubMaster(['radar2d'])`
is only constructed inside `start()`, which itself returns early
(`if not self.enabled: return`) before reaching that line when
`EOPBluetoothRadarEnabled` is off (default `"0"`). `bluetoothd.py` additionally
gates the `start()` call itself on `self.ble_central.enabled`. Grepped the
whole tree for other `PubMaster(['radar2d'])` construction sites — none found
(`gridd.py` and `ncp_session.py` only ever subscribe to `radar2d`, never
publish it). **Clean — single publisher, correctly gated, matches the
documented-safe pattern.**

### `ncp_session.py` / `protocol.py` (196 + 14 lines) — not reviewed this pass

Time-boxed out of this audit pass given everything above. `protocol.py`'s
new message-type block is self-documenting about a real constraint worth
carrying forward: `BLIND_SPOT = 0x0602` reclaims "the only reserved v3.x
slot," while `RADAR_PAIR_CONTROL`/`RADAR_PAIR_STATUS` (`0x0610`/`0x0611`)
are stated to reclaim nothing — i.e. this is the NCP protocol's own version
of Node 5's capnp-ordinal concern, and it's evidently already being tracked
carefully (the comment explicitly calls out which slot is a reclaim vs a
fresh allocation). Worth a follow-up node if a full review is wanted.

---

**Node status: done, with acknowledged partial coverage.** 1 real finding
(orphaned audit-tracking commit hash — documentation-integrity issue, not a
code bug). Reviewed content: radar2d fusion math (clean), BLE central's
authorization core (clean, not exhaustive). Explicitly not covered:
committed delta re-verification (26 commits, relied on existing
`DELTA_AUDIT.md` record), BLE central's connection state machine,
`ncp_session.py`, `protocol.py`.

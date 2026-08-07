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

# Node 4 — Three-way identity check on shared nagaspilot files

## `ngp_brsc.py` — ✅ pass, exactly as documented

Byte-identical across `dev/EOP10` (`nagaspilot/controls/ngp_brsc.py`),
`origin/dev/NGP10`, and `origin/dev/EDP10` (same path on both). Matches
CLAUDE.md's claim that this file "ports to `dev/NGP10` and `dev/EDP10`"
verbatim. This is the pattern the project should hold every other
cross-branch-shared module to.

## `speed_zones.py` / `test_speed_zones.py` — ✅ not drift, legitimate divergence

EDP10's version is a strict superset of NGP10's: everything NGP10 has is
present unchanged in EDP10; the extra ~50 lines are `STEER_ZONE_*`
constants/functions that back the BYD panda-side steering angle/rate
backstop LUT (`byd.h` — see Node 2) and only make sense on a branch with a
car port. NGP10 has no car port, so it correctly has no steering LUT. Diff
confirms this is purely additive (no lines changed in the shared portion).
**Verdict: correct — not a bug, not accidental drift.**

## `dp_tja.py` (EDP10) / `ngp_tja.py` (NGP10) / `tja.py` (EOP10) — 🟠 finding: naming/location fragmentation on code that claims to be shared

Diffed all three pairwise. **Logic is byte-identical** except the module
docstring's first line:

| Branch | Path | Docstring first line |
|---|---|---|
| EOP10 | `selfdrive/controls/lib/tja.py` | `"""Low-speed traffic-jam gap policy shared by EOP, EDP, and NGP.` |
| EDP10 | `selfdrive/controls/lib/dp_tja.py` | `"""DragonPilot low-speed traffic-jam gap policy.` |
| NGP10 | `nagaspilot/controls/ngp_tja.py` | `"""NGP low-speed traffic-jam gap policy.` |

EOP10's own docstring **explicitly asserts** this module is "shared by EOP,
EDP, and NGP" — i.e. the intent is the same canonical-shared-module pattern
as `ngp_brsc.py`. But unlike BRSC, it isn't actually one file:

- Three different filenames (bare `tja.py`, dragonpilot-prefixed `dp_tja.py`,
  ngp-prefixed `ngp_tja.py`) — this directly contradicts the project's own
  recorded guidance to check the target branch's real naming convention
  before inventing one (see memory `feedback-naming-convention-check-first.md`,
  which is itself framed around this pattern — BRSC used `ngp_*` deliberately
  *as an exception* to each branch's own prefix rule specifically so it
  stays byte-identical everywhere).
- Two different locations: `selfdrive/controls/lib/` (EOP10, EDP10) vs
  `nagaspilot/controls/` (NGP10) — the shared-module location BRSC
  established.
- Consequence: these are three independently-copy-pasted files that happen
  to currently agree. A bug fix landed on one branch will silently **not**
  propagate to the other two, since nothing treats them as the same file.
  This is exactly the "wrong direction" failure mode the audit is looking for
  — it will not show up as a test failure today, only as silent behavioral
  drift the next time someone touches TJA on any one branch.

**`docs/eop/03_Software/Controllers/TJA.md` is stale and compounds this** —
it still describes the older design ("TJA logic is integrated into
`longcontrol.py`") and never mentions `tja.py`/`dp_tja.py`/`ngp_tja.py` at
all, so there is no doc of record saying this module is meant to be
canonical-shared the way `docs/eop/03_Software/Controllers/BRSC.md` does for
BRSC.

**Recommendation (not applied — cross-branch rename, needs a decision, not
a unilateral edit):** move all three to `nagaspilot/controls/ngp_tja.py`
(NGP10's location/name — already the established shared-module home) or
formally document why TJA is intentionally per-branch-forked (in which case
the "shared by EOP, EDP, and NGP" docstring on EOP10's copy is itself the
bug). Either way, `TJA.md` needs a rewrite to match whichever answer is
chosen.

---

**Node status: done.** 1 clean pass (`ngp_brsc.py`), 1 confirmed non-issue
(`speed_zones.py`), 1 real finding requiring a user decision (`tja`
naming/location fragmentation + stale doc).

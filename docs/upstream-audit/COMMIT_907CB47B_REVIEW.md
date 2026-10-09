# Code Review — Commit `907cb47b8` [fix(coordinationd): eliminate duplicate RoadConstraint class]

**Commit:** `907cb47b842ee630d119851da5e62ef1efa0e5f7`  
**Subject:** fix(coordinationd): eliminate duplicate RoadConstraint class  
**Reviewed:** 2026-05-31  
**Files changed:** 2 (+3 / −13)  
**Method:** line scan + cross-file type consistency  

---

## Summary of Findings

| Severity | Issue | File | Status |
|---|---|---|---|
| **LOW** | `noqa: F401` re-export pattern in `osm_localizer.py` is a maintenance burden if `RoadConstraint` fields ever diverge | `selfdrive/coordinationd/osm_localizer.py` | Open |
| **INFO** | Commit message says "re-export fusion's RoadConstraint from osm_localizer so existing callers still work", but no other callers besides `coordinationd.py` were found in the repo | `selfdrive/coordinationd/osm_localizer.py` | Open |

---

## Detailed Findings

---

### Finding 1 — LOW: `noqa: F401` re-export is technical debt

| | |
|---|---|
| **File** | `selfdrive/coordinationd/osm_localizer.py:23` |
| **Root cause** | `osm_localizer.py` imports `RoadConstraint` from `fusion.py` solely to re-export it, requiring a `# noqa: F401` linter suppression. This preserves the old import API but means `osm_localizer.py` now has a hidden dependency on `fusion.py` for a type it does not use internally. |
| **Failure** | If `RoadConstraint` fields are ever extended in `fusion.py`, `osm_localizer.py` will silently adopt the new shape through the re-export, which may be surprising to maintainers who expect the type to live in `osm_localizer`. The `noqa` comment also trains reviewers to ignore linter warnings. |
| **Fix** | Remove the re-export and update all callers to import from `fusion.py` directly. A quick `grep -r "from.*osm_localizer.*import.*RoadConstraint"` shows only `coordinationd.py` was affected, so the blast radius is minimal. |

**Code:**
```python
# osm_localizer.py:23
from openpilot.selfdrive.coordinationd.fusion import RoadConstraint  # noqa: F401 — re-exported
```

---

### Finding 2 — INFO: No other callers of `osm_localizer.RoadConstraint` found

| | |
|---|---|
| **File** | repo-wide grep |
| **Root cause** | The commit message claims the re-export is needed for "existing callers", but a search of the entire `selfdrive/` and `tools/` trees shows only `coordinationd.py` imports `RoadConstraint` (and it is updated by this commit). |
| **Failure** | None — the re-export is harmless dead weight. |
| **Fix** | Remove the re-export line entirely. If external forks or private branches depend on it, they can update their imports trivially. |

---

## Other Findings

| Finding | Severity | Notes |
|---------|----------|-------|
| Dataclass field alignment verified | ✅ OK | Both old `osm_localizer.RoadConstraint` and `fusion.RoadConstraint` had identical fields (`lat`, `lon`, `road_width`, `confidence`, `source`, `timestamp`). Removal is behavior-preserving. |
| `coordinationd.py` import update | ✅ OK | Imports `RoadConstraint` from `fusion.py` alongside `FusionEngine` and `GNSSMeasurement`, which is the canonical source. |
| No missing tests | ✅ OK | This is a pure type-system refactor with no behavioral change; existing integration tests for `coordinationd` cover it. |

---

## Priority Fix Order

### P3 — Cleanup
1. **`osm_localizer.py`** — Remove the `noqa: F401` re-export and delete the import line. Update `__all__` if present. The re-export is unnecessary and misleading.

---

## Verdict

**Safe to keep.**

Clean, minimal refactor that removes a real runtime type-mismatch bug (`coordinationd` passed `osm_localizer.RoadConstraint` to `fusion.FusionEngine.fuse()` which expected `fusion.RoadConstraint`). The duplicate dataclass was identical, so no field migration is needed. The only nit is the unnecessary `noqa` re-export, which can be cleaned up in a follow-up.

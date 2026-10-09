# Code Review — Commit `73044c6b6` [fix(modeld): add explicit return type annotation]

**Commit:** `73044c6b669bcd01f829161defc98dee3d94a978`  
**Subject:** fix(modeld): add explicit return type annotation  
**Reviewed:** 2026-05-30  
**Files changed:** 1 · scope: selfdrive/modeld/modeld.py  
**Method:** 3-angle review (line scan / removed-behavior / cross-file) + verification

---

## Result: No bugs introduced

The diff is one line: `def main(demo=False):` → `def main(demo=False) -> int:`.

- **Annotation correctness**: All code paths in `main()` return `int`: early-exit paths at lines 370, 375, 378, 395 return `1`; the successful-run path at line 589 returns `0`. No path returns `None`. The annotation is accurate.
- **Callers unaffected**: `system/manager/process.py:40` calls `mod.main()` and discards the return value. `modeld.py:598` passes it to `exit()`, which accepts `int`. No behavioral change at any call site.
- **Angles B and C**: Returned `[]` — no removed invariants, no broken callers.

---

## Other Findings (pre-existing, not introduced by this commit)

| Finding | Severity | Notes |
|---------|----------|-------|
| `modeld.py:387` — `CLContext()` has no try/except; if it raises, the exception propagates out of `main()` rather than returning `1`. Inconsistent with the `return 1` pattern used for NPU/model init failures at lines 370, 378, 395. | Low | Pre-existing. `launcher()` in `process.py` catches and reports to Sentry, so crash is not silent. Not fixed — out of scope for an annotation-only commit. |
| `modeld.py:400–407` — VisionIPC spin-wait loop has no timeout or exit condition; hangs indefinitely if `v4l2d` never starts. | Low | Pre-existing design choice. Not fixed. |

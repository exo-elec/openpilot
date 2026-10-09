# Code Review — Commit `d347fea13` [docs(dev-pc): add comprehensive dev PC testing guide]

**Commit:** `d347fea1359e7b778191310e114d127596727a8e`  
**Subject:** docs(dev-pc): add comprehensive dev PC testing guide  
**Reviewed:** 2026-05-31  
**Files changed:** 2 (`docs/eop/DEV_PC_GUIDE.md`, `docs/eop/INFERENCED_INDEX.md`)  
**Method:** content review + command validation  

---

## Summary of Findings

| Severity | Issue | File | Status |
|---|---|---|---|
| 🟢 LOW | `--confcutdir=selfdrive/controls/tests/` example may not work for all test layouts; some tests deeper in the tree need a more specific cut | `docs/eop/DEV_PC_GUIDE.md` | Open |
| 🟢 LOW | `find . -name "*.so"` backup command includes `.venv/` and `third_party/` in the exclusion list but not `msgq_repo/` or `rednose_repo/` submodules which may also contain ARM `.so` files | `docs/eop/DEV_PC_GUIDE.md` | Open |
| ✅ OK | Cython `.so` architecture problem is accurately documented with clear workaround steps | `docs/eop/DEV_PC_GUIDE.md` | — |
| ✅ OK | ARM-only test skip list is accurate and actionable | `docs/eop/DEV_PC_GUIDE.md` | — |
| ✅ OK | Cross-reference from `INFERENCED_INDEX.md` is correctly linked | `docs/eop/INFERENCED_INDEX.md` | — |

---

## Other Findings

| Finding | Severity | Notes |
|---------|----------|-------|
| Docker command references `Dockerfile.openpilot_base` which may not exist in this fork | Low | Verify Dockerfile name before users run the command. |
| `scons -j$(nproc)` rebuild step does not mention that `scons` must be installed (not in default Ubuntu image) | Low | Minor; most EOP devs already have it. |

---

## Verdict

✅ **Safe to keep.** Documentation-only commit. The dev PC testing guide fills a real operational gap and correctly documents the ARM `.so` / x86_64 mismatch. Minor refinements suggested for the `find` backup path and Docker filename.

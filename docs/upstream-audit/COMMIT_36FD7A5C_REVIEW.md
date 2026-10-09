# Code Review — Commit `36fd7a5cc` [docs: add DEV_PC_GUIDE.md]

**Commit:** `36fd7a5cc5c57f6d96b8bdd0de48d5e34abcef64`  
**Subject:** docs: add DEV_PC_GUIDE.md and update audit timestamp  
**Reviewed:** 2026-05-31  
**Files changed:** 2 (`docs/eop/DEV_PC_GUIDE.md` new, `docs/upstream-audit/DELTA_AUDIT.md` +1/−1)  
**Method:** content review + accuracy cross-check  

---

## Summary of Findings

| Severity | Issue | File | Status |
|---|---|---|---|
| 🟢 LOW | DELTA_AUDIT.md date bump only; no content review or status update for files changed since last audit | `docs/upstream-audit/DELTA_AUDIT.md` | Open |
| ✅ OK | DEV_PC_GUIDE.md accurately documents ONNX inference test, CARLA launch sequence, and backend priority | `docs/eop/DEV_PC_GUIDE.md` | — |
| ✅ OK | Model placement section correctly warns that models are not committed to git | `docs/eop/DEV_PC_GUIDE.md` | — |

---

## Other Findings

| Finding | Severity | Notes |
|---------|----------|-------|
| Guide references `models/onnx/yolo_640.onnx` but does not mention the `download_models.sh dev-pc` command added in the subsequent commit (`06df38d6e`) | Low | Cross-reference the download script in a follow-up edit for discoverability. |
| `--override-ini="addopts="` in the import smoke test command is an internal pytest workaround; may drift if `pytest.ini` changes | Low | Document why it is needed (root conftest pulls ARM .so). |

---

## Verdict

✅ **Safe to keep.** Pure documentation commit. DEV_PC_GUIDE.md is accurate and fills a real gap. Minor suggestion: cross-reference `download_models.sh` and expand the Cython `.so` workaround section (already done in the next commit `d347fea13`).

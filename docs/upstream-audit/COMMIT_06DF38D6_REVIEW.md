# Code Review — Commit `06df38d6e` [feat(models): add dev-pc download type]

**Commit:** `06df38d6e335fdec49b9813b183623d95e521c22`  
**Subject:** feat(models): add dev-pc download type for ONNX models  
**Reviewed:** 2026-05-31  
**Files changed:** 1 (`models/download_models.sh`)  
**Method:** line scan + shell semantics review  

---

## Summary of Findings

| Severity | Issue | File | Status |
|---|---|---|---|
| 🟡 MEDIUM | `git show pre-build:selfdrive/modeld/models/driving_vision.onnx` fails silently if `pre-build` branch does not exist; empty/truncated file may be written | `models/download_models.sh` | Open |
| 🟡 MEDIUM | YOLOv8n export runs `python3 -c "..."` without checking `ultralytics` installation first; silent failure with generic echo | `models/download_models.sh` | Open |
| 🟢 LOW | `DRAGONPILOT_DIR` hardcodes sibling directory `../dragonpilot` — fragile repo layout assumption | `models/download_models.sh` | Open |
| 🟢 LOW | `mkdir -p` creates `models/hef/` even on `dev-pc` download where HEF is not needed | `models/download_models.sh` | Open |
| ✅ OK | Usage help text and fallback echoes are user-friendly | `models/download_models.sh` | — |

---

## Other Findings

| Finding | Severity | Notes |
|---------|----------|-------|
| `du -sh` pipeline inside subshell may fail if `git show` produced an empty file; `du` error is swallowed by `2>/dev/null` on outer subshell | Low | Cosmetic — user just sees no size output. |
| `--python .venv/bin/python` is not used for the YOLO export; it calls system `python3` which may not have `ultralytics` | Low | Minor inconsistency with rest of EOP workflow that prefers `.venv`. |

---

## Verdict

🟢 **Safe to keep with minor robustness notes.** The script is additive only and fails gracefully. Before declaring production-ready, add explicit existence/validity checks for the extracted ONNX files and consider a `which python3` guard or `.venv` invocation for the YOLO export.

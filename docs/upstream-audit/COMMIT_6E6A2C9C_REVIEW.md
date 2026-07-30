# Code Review — Commit `6e6a2c9ca`

## Commit 6e6a2c9ca — fix(monod): use inference_backend() for x86/ONNX compatibility

---

## Files changed

- `selfdrive/monod/monod.py` (+16 / −4)

---

## Review findings

### `selfdrive/monod/monod.py`

- **🟢 LOW** — `_REPO_ROOT = Path(__file__).parents[3]` is fragile. If the file is moved or renamed, the relative parent traversal breaks. The project already has `BASEDIR` in `common.basedir` which is the canonical way to resolve the repo root.

- **🟢 LOW** — `MODEL_PATHS` default fallbacks still return hardcoded `/data/openpilot/models/...` paths even when no model file exists at any of the checked locations. This is pre-existing behavior, but it means downstream code must still guard with `os.path.exists()` or handle load failures.

- **🟢 LOW** — No unit tests added for the new ONNX fallback path resolution. The `next(... if p.exists())` logic is straightforward but untested.

- **✅ OK** — Switching from `self._client.npu()` to `self._client.inference_backend()` correctly enables x86/ONNX compatibility and allows `monod` to start on dev PC without a real NPU.

- **✅ OK** — Commit message and code comment accurately describe the graceful degradation when models are not found (not a fatal startup error).

---

## Verdict

**Safe to keep.** A small, focused fix that unblocks dev PC testing for `monod`. The `_REPO_ROOT` resolution could be hardened in a follow-up, but there are no functional regressions.

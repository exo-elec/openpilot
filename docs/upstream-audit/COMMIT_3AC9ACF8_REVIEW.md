# Code Review — Commit `3ac9acf80`

## Commit 3ac9acf80 — InferenceD Phase 3: Complete daemon integration + docs update

---

## Files changed

- `docs/eop/EOP.md` (+2 / −2)
- `docs/eop/INFERENCED_INDEX.md` (+31 / −10)
- `docs/eop/INFERENCED_TASKS.md` (+66 / −21)
- `docs/eop/PHASE3_SUMMARY.md` (+25 / −5)
- `docs/eop/PHASE6_COMPLETION_REPORT.md` (+3 / −3)
- `docs/eop/README.md` (+6 / −6)
- `docs/eop/SESSION_COMPLETION_SUMMARY.md` (deleted, −321)
- `docs/eop/SESSION_SUMMARY.md` (+40 / −9)
- `docs/eop/SYSTEM_SWITCHING.md` (new, +118)
- `selfdrive/gridd/gridd.py` (+75 / −8)
- `selfdrive/gridd/test_gridd_integration.py` (new, +203)
- `selfdrive/recordd/recordd.py` (+125 / −56)
- `selfdrive/recordd/test_recordd_integration.py` (new, +188)
- `system/inferenced/rockchip_mpp.py` (+135 / −11)
- `system/inferenced/rockchip_rga.py` (+2 / −2)
- `system/inferenced/tests/test_ipc_communication.py` (new, +315)

---

## Review findings

### `selfdrive/gridd/gridd.py`

- **🟡 MEDIUM** — `_preprocess_frame()` caches `self._rga_available = True` at init time but never re-checks if the RGA backend becomes unavailable later (e.g., HAL reinitialization or thermal disable). `gridd` may hold a stale backend reference.
- **🟢 LOW** — `release()` swallows all exceptions with bare `except Exception: pass`. Should at least log a warning so that resource-leak diagnostics are not lost.
- **✅ OK** — RGA → OpenCV fallback path is robust and well-structured. The `PPLITESEG_INPUT_SIZE` constant is clearly documented.

### `selfdrive/recordd/recordd.py`

- **🟡 MEDIUM** — `_encode_frame_inferenced()` falls back to ffmpeg on any exception, including potentially fatal errors (disk full, permission denied). This could mask real operational failures that should bubble up.
- **🟢 LOW** — `_encode_frame_ffmpeg()` performs a lazy ffmpeg start inside the encode hot path. If `_start_ffmpeg()` fails, it returns `False`, but callers in a streaming loop may not handle the gap gracefully.
- **🟢 LOW** — `start()` mutates `self._use_inferenced = False` when running on dev PC. This side effect is surprising if `start()` is called multiple times with different intentions.
- **✅ OK** — Pixel format fix (`I420` → `yuv420p`) and numpy NV12 vectorization (replacing Python loops with `uv[0::2] = u; uv[1::2] = v`) are correct performance improvements.

### `system/inferenced/rockchip_mpp.py`

- **🟡 MEDIUM** — `_h264_encode_ffmpeg()` and `_h264_decode_ffmpeg()` spawn a new `subprocess.run(...)` for every single frame. This is extremely slow (~70 ms per frame as documented) and cannot meet real-time requirements at 20 fps. Acceptable as a dev PC fallback only; must not be used on production hardware.
- **🟢 LOW** — ffmpeg encode/decode return minimal stub bytes on failure. Good resilience, but the silent fallback means video quality drops to zero without explicit alerts.

### `system/inferenced/rockchip_rga.py`

- **✅ OK** — Boolean crash fix (`inputs.get('input') or inputs.get('src')` → explicit `None` check) is correct and necessary. The original code raised `ValueError: The truth value of an array with more than one element is ambiguous` when the value was a numpy array.

### `docs/eop/...`

- **🟢 LOW** — Large documentation reorganization. `SESSION_COMPLETION_SUMMARY.md` is deleted and content migrated. No code impact.
- **🟢 LOW** — `SYSTEM_SWITCHING.md` describes service-based switching between openpilot and VisionPilot, but the referenced `switch.sh` and systemd service templates are not present in the repository. Documentation is slightly ahead of implementation.

### `selfdrive/gridd/test_gridd_integration.py`

- **✅ OK** — 10 tests covering RGA resize, crop, dtype preservation, latency bounds, OpenCV fallback, and `InferenceClient` integration.

### `selfdrive/recordd/test_recordd_integration.py`

- **✅ OK** — 8 tests covering MPP encode/decode round-trip, NV12/BGR input, `VideoEncoder` start/stop lifecycle, multi-segment recording, quality presets, and `InferenceClient` integration.

### `system/inferenced/tests/test_ipc_communication.py`

- **🟢 LOW** — 7 of 14 tests are skipped on dev PC (require cereal shared memory or ACL). The skip reasons are valid, but it means half the IPC surface is not exercised in CI.
- **✅ OK** — Tests cover daemon lifecycle, latency benchmarks (NPU, RGA, MPP, ACL), invalid backend handling, timeout flags, and end-to-end pipelines.

---

## Verdict

**Safe to keep.** This commit is primarily documentation updates and integration tests, plus targeted bug fixes (RGA boolean crash, recordd InferenceClient init, MPP pixel format). The per-frame ffmpeg subprocess fallback in MPP is a known performance limitation acceptable for dev PC only. All new integration tests pass. No critical code regressions.

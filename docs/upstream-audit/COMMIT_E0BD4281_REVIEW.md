# Code Review — Commit `e0bd42810`

## Commit e0bd42810 — test: add inference pipeline integration tests (ONNX vision+policy)

---

## Files changed

- `selfdrive/test/test_inference_pipeline.py` (new, +155)

---

## Review findings

### `selfdrive/test/test_inference_pipeline.py`

- **🟡 MEDIUM** — `test_vision_model_output_shape()` hardcodes expected output shape `(1, 1576)`. If the ONNX model is re-exported with different output dimensions (e.g., after architecture changes), this test will break without a clear signal about whether the model or the test is wrong. Prefer reading the expected shape from model metadata or accepting a small set of valid shapes.

- **🟡 MEDIUM** — `test_policy_model_output_shape()` hardcodes expected shape `(1, 1000)` with the same fragility as above.

- **🟢 LOW** — `test_model_state_init()` sets `os.environ.setdefault("SIMULATION", "1")` but does not clean up the environment variable after the test. If a subsequent test expects `SIMULATION` to be unset, it may be polluted.

- **🟢 LOW** — `test_visionbuf_data_access()` is marked `@pytest.mark.slow`, but the `pytest.importorskip("msgq.visionipc")` at the top of the function may cause the test to skip before the slow marker is evaluated by test runners. This is a minor pytest lifecycle quirk.

- **🟢 LOW** — The `onnx_backend` fixture initializes HAL and loads models but does not call `backend.release()` or HAL cleanup. In repeated test runs this may leak ONNX Runtime session objects.

- **✅ OK** — Tests correctly skip when ONNX backend or model files are missing, with helpful skip messages (`"run models/download_models.sh dev-pc"`).

- **✅ OK** — Good coverage of the x86 dev PC inference path: backend selection, `CLContext`/`DrivingModelFrame` stubs, vision model output shape, policy model output shape, and `ModelState` initialization.

---

## Verdict

**Safe to keep.** Useful integration tests that validate the x86 ONNX inference pipeline end-to-end. The hardcoded output shapes and minor environment/leak concerns should be addressed in a future test-hardening pass but do not block dev PC development.

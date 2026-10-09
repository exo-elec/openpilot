# Code Review — Commit `d2a00ddc4`

## Commit d2a00ddc4 — feat(inferenced): implement daemon job execution + IPC client mode

---

## Files changed

- `cereal/custom.capnp` (+6)
- `system/inferenced/client.py` (+201 / −29)
- `system/inferenced/inferenced.py` (+178 / −43)
- `system/inferenced/tests/test_daemon_execution.py` (new, +376)

---

## Review findings

### `cereal/custom.capnp`

- **🟢 LOW** — `inputDtype @9 :Text` uses a free-form string (e.g., `"float32"`) rather than a dedicated enum. This is flexible but slightly less efficient and provides no schema-level validation. Acceptable for internal IPC.
- **✅ OK** — Adding `inputData`, `inputShape`, `inputDtype` to `InferenceJobRequest` and `outputData`, `outputShape`, `outputDtype` to `InferenceJobResult` completes the data plane for tensor serialization.

### `system/inferenced/client.py`

- **🟡 MEDIUM** — `_next_job_id()` (method name) shadows the class attribute `_next_job_id` (integer). Python resolves the method binding first, so calls work, but the name collision is confusing and brittle. Recommend renaming the method to `_generate_job_id()` or the attribute to `_job_id_seq`.

- **🟡 MEDIUM** — `submit_job()` polls for results with `while time.monotonic() < deadline: self._sm.update(100); time.sleep(0.001)`. This is a busy-spin at ~1000 Hz polling frequency, wasting CPU. Should use a blocking `sm.update()` with the remaining deadline as timeout, or a threading condition.

- **🟢 LOW** — `InferenceClient(use_ipc=True)` silently falls back to direct HAL if `_CEREAL_AVAILABLE` is False. This is correct for dev PC but could mask packaging/deployment errors on target hardware where cereal is expected to be present.

- **🟢 LOW** — `_direct_infer()` unconditionally wraps `input_array` as `{'input': input_array}`. Multi-input models (e.g., policy with `desire_pulse`, `traffic_convention`, `features_buffer`) cannot use this fallback path correctly.

### `system/inferenced/inferenced.py`

- **🟡 MEDIUM** — `_resolve_model_path()` auto-detects platform by checking `os.path.isdir('/sys/bus/platform/devices/rockchip')`. This heuristic may fail in containers, chroots, or on Rockchip boards with different device-tree layouts. Prefer a more robust platform check (e.g., `uname -m` + existing `system/hardware` registry).

- **🟡 MEDIUM** — `_execute_job()` serializes only the first output key (`next(iter(result.outputs))`) for the IPC result message. Multi-output models silently drop all secondary outputs. This breaks policy models that emit both action logits and auxiliary outputs.

- **🟢 LOW** — `MODEL_REGISTRY` is a class-level mutable dict. Environment-variable overrides or runtime mutations affect all `InferenceD` instances. Acceptable because `InferenceD` is intended to be a singleton.

- **🟢 LOW** — `_deserialize_input()` does not validate that `len(input_data)` matches `np.dtype(input_dtype).itemsize * np.prod(input_shape)`. A malformed capnp message could trigger an unhandled `ValueError` during reshape.

- **✅ OK** — Lazy import of `cereal.messaging` inside `InferenceD.__init__` prevents ARM `.so` import errors on dev PC. Good practice.

### `system/inferenced/tests/test_daemon_execution.py`

- **🟢 LOW** — The test mocks cereal at the module level (`sys.modules['cereal.messaging'] = mock_messaging`). This leaks global state; any test running after this that imports `cereal.messaging` will see the mock. Should use `unittest.mock.patch.dict('sys.modules', ...)` for isolation.
- **✅ OK** — 16 tests cover input deserialization, model path resolution (including env override), job execution with mocked backends, result submission with output data, and request parsing. Good coverage for a new IPC path.

---

## Verdict

**Safe to keep with reservations.** The IPC client/daemon data plane is well-designed, thoroughly tested, and correctly falls back to direct HAL on dev PC. The concerns around multi-output serialization, busy-loop polling, and platform heuristics should be addressed in follow-up commits but do not block dev PC usage.

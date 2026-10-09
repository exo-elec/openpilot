# Code Review — Commit `82f453f04` [feat(x86): dual-backend ONNX + RKNN]

**Commit:** `82f453f040c0b2fb66b1f967ea803ba88b92f688`  
**Subject:** feat(x86): dual-backend ONNX Runtime + ARM RKNN inference for dev PC + CARLA  
**Reviewed:** 2026-05-31  
**Files changed:** 10 (+354 / −33)  
**Method:** 3-angle review (line scan / removed-behavior / cross-file) + verification  

---

## Summary of Findings

| Severity | Issue | File | Status |
|---|---|---|---|
| **HIGH** | `commonmodel_pyx.py` stub returns NV12-sized buffer (49 152) instead of model input size (393 216); `.reshape(expected_shape)` crashes at runtime | `selfdrive/modeld/models/commonmodel_pyx.py` | Open |
| **HIGH** | `_resolve_model` prefers `.rknn` before `.onnx`; on x86 with RKNN artifacts present, ONNX backend receives `.rknn` path and fails to load | `selfdrive/modeld/modeld.py` | Open |
| **HIGH** | Policy input dict uses key `'desire'` but ONNX model node is named `'desire_pulse'`; ONNX Runtime fails with missing input | `selfdrive/modeld/modeld.py` | Open |
| **MEDIUM** | `OnnxBackend.infer()` silently skips unknown input keys instead of failing fast | `system/inferenced/onnx_backend.py` | Open |
| **MEDIUM** | Vision metadata claims output size 1536, actual ONNX model outputs 1576; metadata is stale | `selfdrive/modeld/models/driving_vision_metadata.pkl` | Open |
| **MEDIUM** | Fallback default vision input names (`input_imgs`/`big_input_imgs`) do not match ONNX model input names (`img`/`big_img`) | `selfdrive/modeld/modeld.py` | Open |
| **MEDIUM** | `prepare()` docstring claims it applies camera projection, but parameter is ignored — dev PC runs unprojected frames | `selfdrive/modeld/models/commonmodel_pyx.py` | Open |
| **MEDIUM** | `modeld` startup sanity-check calls `client.npu()`; if NPU is disabled, daemon exits even though ONNX fallback is available | `selfdrive/modeld/modeld.py` | Open |
| **MEDIUM** | `client.onnx()` does not cache the backend instance (inconsistent with `npu()`/`acl()` accessors) | `system/inferenced/client.py` | Open |
| **LOW** | `system/inferenced/__init__.py` exports other backends in `__all__` but omits `OnnxBackend` | `system/inferenced/__init__.py` | Open |
| **LOW** | `cereal/custom.capnp` schema comment documents backendType up to 6=Hailo, missing 7=ONNX | `cereal/custom.capnp` | Open |
| **LOW** | `_resolve_model` defines unused `exts` parameter and unused `subdirs` dict | `selfdrive/modeld/modeld.py` | Open |

---

## Bugs Found (detailed)

---

### Bug 1 — HIGH: `commonmodel_pyx.py` stub returns wrong buffer size — `ValueError` on first frame

| | |
|---|---|
| **File** | `selfdrive/modeld/models/commonmodel_pyx.py:49–67` |
| **Root cause** | `DrivingModelFrame.prepare()` extracts raw NV12 bytes from `VisionBuf` (size 128×256×1.5 = 49 152) and returns them as a flat 1-D array. The caller in `modeld.py` then calls `.reshape(expected_shape).astype(np.uint8)` where `expected_shape` is `(1, 12, 128, 256)` (size 393 216). The reshape fails because the buffer is ~8× too small. |
| **Failure** | On dev PC / CARLA, `modeld` crashes immediately on the first camera frame with `ValueError: cannot reshape array of size 49152 into shape (1,12,128,256)`. The commit verified ONNX inference with standalone dummy arrays, but the end-to-end `modeld → VisionIpcClient → DrivingModelFrame` path is untested and broken. |
| **Fix** | Make the stub return a zero-filled buffer of the correct model-input size. Minimal change: replace the NV12 extraction with `np.zeros(1*12*128*256, dtype=np.uint8)` (or derive size from `buf` dimensions if available). The hardware path uses OpenCL and returns a GPU buffer of the correct size; the stub must match that contract. |

**Code:**
```python
# commonmodel_pyx.py:54-61
      raw = np.frombuffer(buf.data, dtype=np.uint8)
      height = getattr(buf, 'height', 128)
      width = getattr(buf, 'width', 256)
      yuv_size = height * width + (height // 2) * width
      frame = raw[:yuv_size].reshape(yuv_size)   # size = 49152
```
Caller in `modeld.py:275`:
```python
            vision_inputs[name] = frame_input.reshape(expected_shape).astype(np.uint8)
```

---

### Bug 2 — HIGH: `_resolve_model` prefers `.rknn` over `.onnx` — ONNX load fails when RKNN artifacts are present

| | |
|---|---|
| **File** | `selfdrive/modeld/modeld.py:52–65` |
| **Root cause** | `_resolve_model` iterates extensions in the order `(".rknn", ".onnx")`. On a dev PC, if an RKNN model file exists anywhere in the search path (e.g. `/data/openpilot/models/rknn/` from a previous device flash, or a local `models/rknn/` directory), the function returns the `.rknn` path. `ModelState._load_models()` passes that path to `self.npu.load_model()` — which on x86 is `OnnxBackend`. `onnxruntime.InferenceSession` cannot load an RKNN file and raises an error. |
| **Failure** | `modeld` fails to start on x86 with `Failed to load vision model` (or a low-level ONNX parse error) whenever RKNN artifacts are present, even though a perfectly valid `.onnx` file exists. |
| **Fix** | Either (a) make `_resolve_model` backend-aware by accepting a preferred extension (or querying `client.inference_backend()` type), or (b) have `OnnxBackend.load_model()` detect a non-`.onnx` path and fall back to `_find_model(config.name)` which searches only `.onnx`. |

**Code:**
```python
# modeld.py:61
        for subdir, ext in (("rknn", ".rknn"), ("onnx", ".onnx"), ("", ".rknn"), ("", ".onnx")):
```

---

### Bug 3 — HIGH: Policy input key `'desire'` mismatches ONNX node name `'desire_pulse'`

| | |
|---|---|
| **File** | `selfdrive/modeld/modeld.py:300–304` |
| **Root cause** | `ModelState.run()` builds the policy inputs dict with hardcoded key `'desire'`. The ONNX policy model (`driving_policy.onnx`) has an input node named `'desire_pulse'`. `OnnxBackend.infer()` silently skips keys that do not match any node name (`if key not in node_map: continue`). Consequently `'desire_pulse'` is never supplied to the session, and `session.run()` raises a missing-input error. |
| **Failure** | Every policy inference call on x86 fails with a generic ONNX Runtime exception. `modeld` logs the error and returns `None`, producing no controls output. The vision model works (its input names `img`/`big_img` match), so this bug is hidden until the policy stage. |
| **Fix** | Rename the dict key to `'desire_pulse'` when the backend is ONNX, or add an alias mapping in `OnnxBackend` (e.g. `desire → desire_pulse`). Prefer fixing the ONNX model input name to match the codebase convention if a model rebuild is possible. |

**Code:**
```python
# modeld.py:300-304
        policy_inputs = {
            'desire': self.numpy_inputs['desire'],
            'traffic_convention': self.numpy_inputs['traffic_convention'],
            'features_buffer': self.numpy_inputs['features_buffer'],
        }
```
ONNX model inputs (verified with `onnx.load`):
```
['desire_pulse', 'traffic_convention', 'features_buffer']
```

---

## Other Findings (documented, not fixed)

| Finding | Severity | Notes |
|---------|----------|-------|
| `OnnxBackend.infer()` silently drops unknown keys | Medium | `if key not in node_map: continue` makes debugging name mismatches extremely hard. Should fail fast with a clear error listing missing / unexpected keys. |
| Vision metadata output size mismatch | Medium | `driving_vision_metadata.pkl` claims output size 1536; actual ONNX model outputs 1576. Slices are within bounds so no crash, but the last 40 elements are silently discarded and `self.vision_output_size` is wrong. |
| Vision fallback defaults use stale input names | Medium | If metadata is missing, fallback uses `input_imgs`/`big_input_imgs`; ONNX model expects `img`/`big_img`. Latent bug — only triggers if `.pkl` files are deleted. |
| `commonmodel_pyx.py` ignores `projection` parameter | Medium | Docstring says "apply projection transform", but `projection` argument is unused. Dev PC / CARLA frames are unprojected, which may affect model accuracy. |
| `modeld` startup NPU check blocks ONNX fallback | Medium | `main()` calls `client.npu()` and returns 1 on `RuntimeError`. If `enable_npu=False`, modeld exits even though `inference_backend()` would fall back to ONNX. Mock NPU currently masks this on dev PC. |
| `client.onnx()` lacks backend caching | Medium | `npu()`, `acl()`, etc. cache in `self._npu`. `onnx()` fetches from HAL every call. Inconsistent and slightly less efficient. |
| `OnnxBackend` not in `__all__` | Low | `system/inferenced/__init__.py` imports `OnnxBackend` but omits it from `__all__`. |
| `custom.capnp` schema comment stale | Low | `backendType` comment lists up to `6=Hailo`; missing `7=ONNX`. |
| Dead code in `_resolve_model` | Low | Unused `exts` parameter and unused `subdirs` dict. |
| Pre-existing `VisionIpcClient` arg mismatch | Low | `modeld.py` passes 4 args to `VisionIpcClient(...)`; the Cython class only accepts 3. Not introduced by this commit. |
| Binary `.pkl` provenance | Low | `driving_vision_metadata.pkl` and `driving_policy_metadata.pkl` are opaque blobs from "dragonpilot 0.10.0" with no hash or build provenance. |

---

## Priority Fix Order

### P0 — Blocks dev PC / CARLA end-to-end run
1. **`commonmodel_pyx.py`** — Return a buffer of size `1×12×128×256` (or derive from model shape) so `.reshape` succeeds.
2. **`modeld.py` policy input naming** — Align `'desire'` dict key with ONNX node name `'desire_pulse'`.
3. **`modeld.py` model path resolution** — Prevent ONNX backend from receiving `.rknn` files on x86.

### P1 — Robustness and correctness
4. **`onnx_backend.py`** — Fail fast on missing / unexpected input keys instead of silently skipping.
5. **`driving_vision_metadata.pkl`** — Regenerate or update to reflect actual ONNX output size (1576).
6. **`modeld.py` fallback defaults** — Update default vision input names to `img`/`big_img`.
7. **`modeld.py` startup check** — Replace `client.npu()` sanity check with `client.inference_backend()` so ONNX-only platforms can start.

### P2 — Polish / cross-file consistency
8. **`system/inferenced/__init__.py`** — Add `OnnxBackend` to `__all__`.
9. **`cereal/custom.capnp`** — Update `backendType` comment to include `7=ONNX`.
10. **`system/inferenced/client.py`** — Cache `self._onnx` to match other backend accessors.
11. **`modeld.py`** — Remove unused `exts` parameter and `subdirs` dict from `_resolve_model`.

---

## Action Required

- **Engineering owner:** Apply P0 fixes before merging any x86/CARLA smoke-test pipeline that exercises the full `modeld` loop.  
- **Model team:** Verify the policy ONNX input name (`desire_pulse` vs `desire`) and regenerate metadata `.pkl` files so output shapes match the exported ONNX graphs.  
- **HAL owner:** Update `custom.capnp` schema documentation and `__init__.py` exports to keep the ONNX backend discoverable.  
- **QA:** Run an end-to-end frame-through-model test on dev PC (not just import tests) to validate `DrivingModelFrame → modeld → ONNX backend → parser` before declaring the x86 path production-ready.

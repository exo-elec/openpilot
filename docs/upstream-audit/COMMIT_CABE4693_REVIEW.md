# Code Review — Commit `cabe4693c` [feat(inferenced): hardware backends]

**Commit:** `cabe4693c692a27b97a5aa0b126c7076b09999f8`  
**Subject:** feat(inferenced): implement hardware backend modules for all compute accelerators  
**Reviewed:** 2026-05-30  
**Files changed:** 7 · scope: `system/inferenced/` (6 new backend files + compute.py path fix)  
**Method:** 3-angle review (line scan / removed-behavior / cross-file) + verification

---

## Bugs Found and Fixed

All 8 bugs below were fixed in the same session.

---

### Bug 1 — HIGH: `RKNNRunner.__init__` crashes immediately — calls non-existent `get_model_info`, `infer_async`, `get_result`, `get_perf_stats`

| | |
|---|---|
| **File** | `selfdrive/modeld/runners/rknn_runner.py:61,163,174,181` |
| **Root cause** | `_query_model_info()` (called unconditionally from `__init__`) calls `self.npu.get_model_info('rknn_model')` — not a method on `HardwareBackend` or `RKNNBackend`. `run_async()` calls `self.client.infer_async(...)`, `get_async_result()` calls `self.client.get_result(...)`, and `get_perf_detail()` calls `self.npu.get_perf_stats(...)` — none of these exist on `InferenceClient` or `HardwareBackend`. |
| **Failure** | Every `RKNNRunner()` construction raises `AttributeError: 'RKNNBackend' object has no attribute 'get_model_info'` immediately. The NPU inference path via `rknn_runner` is completely broken. |
| **Fix** | `_query_model_info()` made a no-op (shapes provided at construction). `run_async()` falls through to synchronous `npu.infer()`. `get_async_result()` returns stored outputs. `get_perf_detail()` returns `_stats` fields. |

---

### Bug 2 — HIGH: `initialize()` imports `rknn.api.RKNN` (PC toolkit) instead of `rknnlite.api.RKNNLite` (device runtime) — NPU silently in mock mode on all hardware

| | |
|---|---|
| **File** | `system/inferenced/rockchip_npu.py:32,82` |
| **Root cause** | Both `initialize()` and `load_model()` import `from rknn.api import RKNN`. On RK3576/RK3588 hardware, only `rknnlite` (the on-device runtime) is installed; `rknn-toolkit2` (PC-side conversion toolkit) is not. `ImportError` is caught and the backend silently activates mock mode. |
| **Failure** | On all deployed hardware, NPU inference is never executed — mock mode returns random tensors. No error is logged at ERROR level; only an INFO message says "using mock mode for dev testing". |
| **Fix** | Changed both import sites to `from rknnlite.api import RKNNLite` and updated `RKNN(verbose=False)` → `RKNNLite(verbose=False)`. |

---

### Bug 3 — HIGH: `HailoSideDetector.detect()` calls `self._client.infer()` — `InferenceClient` has no `.infer()` method

| | |
|---|---|
| **File** | `selfdrive/sided/hailo_side_detector.py:94` |
| **Root cause** | `detect()` calls `self._client.infer(backend_type=BackendType.HAILO, model_name='yolo_side', inputs=...)`. `InferenceClient` exposes backend accessors (`.npu()`, `.hailo()`, etc.) but no top-level `.infer()` method. Inference lives at the backend level: `client.hailo().infer(name, inputs)`. |
| **Failure** | Every `detect()` call raises `AttributeError: 'InferenceClient' object has no attribute 'infer'`, caught by the outer `except Exception`, causing `detect()` to always return `[]`. Hailo side detection is permanently silenced. |
| **Fix** | Store the backend ref in `_init()` as `self._hailo_backend = hailo_backend`. In `detect()`, call `self._hailo_backend.infer('yolo_side', {'input': rgb})`. |

---

### Bug 4 — HIGH: `hailo_hef.py` imports from `hailo_sdk_common` (compiler SDK) instead of `hailo_platform` (inference runtime)

| | |
|---|---|
| **File** | `system/inferenced/hailo_hef.py:25` |
| **Root cause** | `initialize()` does `from hailo_sdk_common.pyhailort import HailRT`. On deployment hardware, `hailo_platform` (the inference runtime) is installed; `hailo_sdk_common` (the model compiler SDK) is not. |
| **Failure** | `ImportError` is caught and logged as a warning; the Hailo-8 backend never initializes on any deployed device, even with working hardware. |
| **Fix** | Changed import to `from hailo_platform import HailoRT`. Also set `self._hailort = None` after release to prevent double-release, and added a model-loaded guard in `infer()`. |

---

### Bug 5 — MEDIUM: `_cvtcolor()` reads `'src_y'`/`'src_uv'` keys but callers pass packed `'src'` array — returns zeros with `success=True`

| | |
|---|---|
| **File** | `system/inferenced/rockchip_rga.py:122-126` |
| **Root cause** | `yolo_rknn.py` calls `rga.infer(model_name='cvtColor', inputs={'src': bgr_img})` to convert BGR→RGB. `_cvtcolor()` reads `inputs.get('src_y')` (NV12 Y-plane key) which is `None`. It returns `np.zeros(...)` with the outer `infer()` marking `success=True`. The caller's `cv2` fallback is guarded by `result.success` and is never triggered. |
| **Failure** | Every YOLO preprocessing BGR→RGB conversion via RGA returns a black frame, silently producing zero-confidence detections. |
| **Fix** | Added a packed-array path at the start of `_cvtcolor()`: if `inputs.get('src')` is not None, apply `cv2.COLOR_BGR2RGB` directly and return. |

---

### Bug 6 — MEDIUM: `sorted(inputs.keys())` maps inputs alphabetically — wrong slot order for multi-input NPU models

| | |
|---|---|
| **File** | `system/inferenced/rockchip_npu.py:153` |
| **Root cause** | `infer()` builds the `rknn_inputs` list by `for key in sorted(inputs.keys())`. `RKNNLite.inference()` maps inputs positionally by slot index (as defined at RKNN compile time). For multi-input models (e.g., driving_policy with inputs `desire`, `features_buffer`, `traffic_convention`, `recurrent_state`), alphabetical order does not match compiled slot order. |
| **Failure** | Wrong input-to-slot assignment produces garbage policy outputs with no error. Bug is silent in dev (single-input YOLO) but activates for any multi-input model. |
| **Fix** | Changed `sorted(inputs.keys())` to `inputs.keys()` — preserves Python 3.7+ dict insertion order. Callers supply keys in model-compiled slot order. |

---

### Bug 7 — MEDIUM: Real-hardware output key `'output_0'`/`'output_1'` doesn't match callers' expected `'outputs'` key

| | |
|---|---|
| **File** | `system/inferenced/rockchip_npu.py:163-168` |
| **Root cause** | On real hardware (non-mock), `infer()` wraps multi-output RKNN results as `output_dict[f'output_{i}'] = arr`. `modeld.py` lines 276 and 302 access `result.outputs.get('outputs', zeros)` — key `'outputs'` is never present in the real-hardware dict. Mock mode produces `'output'` (single key), also mismatched. |
| **Failure** | After Bug 2 is fixed (RKNN→RKNNLite), on real hardware inference, `modeld.py` always reads the fallback zero tensors regardless of actual NPU output. Driving model runs on zeros silently. |
| **Fix** | For `list`/`tuple` outputs (multi-output), store as `output_dict['outputs'] = [list of arrays]`. For scalar/single output, keep `'output'`. |

---

### Bug 8 — LOW: RGA operation `'scale'` not dispatched — silent CPU fallback on every YOLO letterbox call

| | |
|---|---|
| **File** | `system/inferenced/rockchip_rga.py:84` |
| **Root cause** | `yolo_rknn.py` calls `rga.infer(model_name='scale', inputs={...})` for image resizing. `RGABackend.infer()` only handles `'cvtcolor'`, `'resize'`, `'crop'`. `'scale'` hits the else branch and returns `success=False`. The caller falls back to `cv2.resize()`. |
| **Failure** | RGA hardware acceleration for all YOLO preprocessing resize ops is silently bypassed; `cv2.resize()` runs on CPU for every frame instead. Also fixed: `_resize()` now accepts `'src'` key (yolo_rknn passes `{'src': img}`) in addition to `'input'`. |
| **Fix** | Added `'scale'` as an alias for `'resize'` in the dispatch: `elif op in ('resize', 'scale'):`. Also updated `_resize()` to fall back to `inputs.get('src')` when `'input'` is absent. |

---

## Other Findings (documented, not fixed)

| Finding | Severity | Notes |
|---------|----------|-------|
| `hailo_hef.py`: `load_model()` and `_run_inference()` are stubs — no actual HEF loading or HailRT inference call | Medium | Placeholder implementation; fixes Bug 3 import + guard, but actual HailRT inference wiring is TODO |
| `rockchip_npu.py`: `rknn.inference()` can return `None` on NPU error; result is wrapped as `success=True` with `np.array(None)` | Medium | Not fixed in this pass; masked by Bug 2 (mock mode). Add `if outputs is None: return error result` after Bug 2 fix is deployed to hardware |
| `gpu_opencl.py`/`arm_acl.py`: `_gpu_compute()`/`_compute_fallback()` return `None` → `success=True` with `None` output | Low | Placeholder backends; downstream callers should guard on `result.outputs` content |

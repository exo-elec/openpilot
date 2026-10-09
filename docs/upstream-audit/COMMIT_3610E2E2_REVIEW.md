# Code Review — Commit `3610e2e2` [WIP — inferenced refactor]

**Commit:** `3610e2e21` (HEAD at time of review)  
**Subject:** WIP — flatten inferenced submodule hierarchy, add compute_recovery + monitoring  
**Reviewed:** 2026-05-28  
**Files changed:** 51 · scope: `system/inferenced/`, `selfdrive/gridd/costmap.py`, `selfdrive/stereod/stereod.py`  
**Method:** 3-angle review (line scan / removed-behavior / cross-file) + verification

---

## Bugs Found and Fixed

All 8 bugs below were fixed in the same session.

---

### Bug 1 — HIGH: RGA operation name case mismatch — every `cvtColor` call returns "Unknown operation"

| | |
|---|---|
| **File** | `system/inferenced/rockchip_rga.py:77` |
| **Root cause** | The refactor renamed the internal operation dispatch from `'cvtColor'` to `'cvtcolor'` (all-lowercase). Callers in `selfdrive/modeld/vision/yolo_rknn.py:269` and `selfdrive/steamd/video_utils.py:45` still pass `'cvtColor'` (camelCase). The string comparison `model_name == 'cvtcolor'` never matches. |
| **Failure** | Every call to `rga.infer('cvtColor', ...)` returns `InferenceResult(success=False, error_message="Unknown RGA operation: cvtColor")`. NV12→RGB conversion always fails silently; callers fall back to OpenCV on the critical path. |
| **Fix** | Normalize the dispatch key with `op = model_name.lower()` before comparison. Handles both callers without touching them. |

---

### Bug 2 — HIGH: `self._rknn` (shared RKNN initializer) never released — context leak on HAL restart

| | |
|---|---|
| **File** | `system/inferenced/rockchip_npu.py:49–61` |
| **Root cause** | `initialize()` creates `self._rknn = RKNN(verbose=False)` as a shared initializer instance. `release()` iterates `self._loaded_models.values()` (per-model instances) and calls `rknn.release()` on each, but never releases `self._rknn`. Each time the HAL restarts (e.g., after a thermald-triggered recovery), a new RKNN context is allocated and the old one leaks. |
| **Failure** | On repeated HAL init/release cycles (test harness, error recovery, daemon restart), RKNN driver contexts accumulate. On RK3576/RK3588 with limited NPU driver resources, this causes eventual init failure or kernel OOM. |
| **Fix** | Added `self._rknn.release()` after the per-model release loop in `release()`. Also set `self._rknn = None` after release. |

---

### Bug 3 — HIGH: `timeout_ms or default` falsy-zero — timeout=0 silently applies 1000 ms cap

| | |
|---|---|
| **File** | `system/inferenced/compute.py:412` |
| **Root cause** | `timeout_sec = (timeout_ms or self.config.inference_timeout_ms) / 1000.0`. Python `or` treats `0` as falsy, so passing `timeout_ms=0` (no timeout / wait forever) instead uses `inference_timeout_ms=1000.0`. |
| **Failure** | A caller explicitly requesting non-blocking or uncapped inference with `timeout_ms=0` gets a 1-second hard cap instead. Pre-load operations or background jobs that are intentionally unbounded get silently killed. |
| **Fix** | `timeout_sec = (timeout_ms if timeout_ms is not None else self.config.inference_timeout_ms) / 1000.0` |

---

### Bug 4 — HIGH: Hung backend job blocks single-worker executor permanently

| | |
|---|---|
| **File** | `system/inferenced/compute.py:413–416` |
| **Root cause** | The executor uses `max_workers=1`. `future.result(timeout=timeout_sec)` raises `FutureTimeoutError`, but the underlying thread is still running the hung backend call. Since there is only one worker, all subsequent `submit()` calls queue behind the hung thread — no inference can complete until it unblocks. |
| **Failure** | A single NPU/RGA call that hangs longer than `inference_timeout_ms` (e.g., a kernel driver stall on hardware fault) permanently blocks the inference path. All ADAS features relying on inference degrade simultaneously. No recovery without daemon restart. |
| **Fix** | On `FutureTimeoutError`, shut down the existing executor with `wait=False` and immediately replace it with a fresh `ThreadPoolExecutor`. The hung thread runs to completion in the background without blocking new submissions. |

---

### Bug 5 — MEDIUM: Bare `backend_monitors[name]` subscript — KeyError if monitor not registered

| | |
|---|---|
| **File** | `system/inferenced/compute.py:427, 429, 442, 463` |
| **Root cause** | `infer()` directly subscripts `self._recovery_manager.backend_monitors[backend_type.name]` to call `record_success()` / `record_failure()`. `_setup_recovery()` registers monitors only for backends that successfully initialized. If a backend initializes after `_setup_recovery()` has run (or monitor registration was skipped due to an exception), the key is absent. |
| **Failure** | `KeyError: 'NPU'` (or any backend name) raised mid-inference, converting a backend failure into an unhandled exception in the HAL — a harder crash than the original backend error. |
| **Fix** | Replaced all 4 bare subscripts with `.get()` + `if monitor is not None:` guards. |

---

### Bug 6 — MEDIUM: `FallbackStrategy("ACL", "ACL_CPU")` references non-existent backend

| | |
|---|---|
| **File** | `system/inferenced/compute.py:284` |
| **Root cause** | `_setup_recovery()` registers a `FallbackStrategy("ACL", "ACL_CPU")`. `"ACL_CPU"` is not a `BackendType` member — there is no separate CPU backend; ACL handles GPU/CPU selection internally. Additionally, `get_fallback_backend()` is never called from `infer()`, making the strategy dead code. |
| **Failure** | No immediate crash (the strategy is never invoked), but `get_fallback_backend()` would return `"ACL_CPU"` as a string if ever called — callers expecting a `BackendType` would get a runtime `KeyError` or `AttributeError`. Misleading documentation. |
| **Fix** | Removed the `FallbackStrategy` registration; replaced with a comment noting ACL handles its own GPU/CPU selection internally. |

---

### Bug 7 — MEDIUM: `CostmapGenerator.__init__` try/except both branches call `client.acl()` — no real fallback

| | |
|---|---|
| **File** | `selfdrive/gridd/costmap.py:55–60` |
| **Root cause** | Copy-paste error: the `except RuntimeError:` branch assigns `self.backend = client.acl()` — identical to the try branch. If `client.acl()` raises `RuntimeError` (GPU unavailable), the except block raises the same error again, crashing `__init__`. The "Using CPU backend" log line is never reached. |
| **Failure** | Any system without an ACL/GPU backend available raises `RuntimeError` in `CostmapGenerator.__init__`, crashing `gridd`. Since gridd is a non-critical EOP daemon, this causes its process to die on dev hardware or hardware without Mali GPU. |
| **Fix** | `except RuntimeError:` branch now sets `self.backend = None` with a warning log. `generate()` guards the `backend.infer()` call with `if self.backend is not None:`, falling through to the existing `_generate_cpu()` numpy implementation. |

---

### Bug 8 — MEDIUM: `HAL.__init__` creates executor without checking if already constructed — leaks on repeated calls

| | |
|---|---|
| **File** | `system/inferenced/compute.py:201–214` |
| **Root cause** | `__init__` guards on `if self._initialized:` — but `_initialized` is only set to `True` inside `initialize()`, not `__init__`. If `HAL()` is called multiple times before `initialize()` (e.g., in tests or via `get_hal()` retries), each call creates a new `ThreadPoolExecutor` and overwrites `self._executor`, leaking the previous one. |
| **Failure** | Each leaked executor holds an idle thread. On a test harness that calls `HAL()` N times, N-1 thread pools are leaked. On the edge device with limited threads/memory, this degrades over time. |
| **Fix** | Changed guard to `if self._initialized or hasattr(self, '_executor'): return`. First construction sets `_executor`; all subsequent calls before `initialize()` are no-ops. |

---

## Other Findings (documented, not fixed)

| Finding | Severity | Notes |
|---------|----------|-------|
| `stereod.py`: `self.gpu = None` in ACL except branch, `_init_sgm()` checks `if self.gpu is None:` | Info | Review candidate, but already correctly handled in current code — no fix needed. |
| `monitoring.py`: `get_all_metrics()` returned a shallow-copy dict with shared `PerformanceMetrics` objects | Low | Concurrent reads of `avg_latency_ms` etc. while `record_operation()` writes are technically racy. Fixed by returning `copy.deepcopy(self.metrics)`. |
| `compute_recovery.py`: `get_fallback_backend()` is never called from `infer()` | Info | Dead code. The fallback mechanism is partially built but not wired up. The strategy registration was removed (Bug 6); `get_fallback_backend()` left in place for future wiring. |
| Single-worker executor with `wait=False` shutdown on timeout creates orphaned threads | Low | On repeated timeouts, each `shutdown(wait=False)` + new executor creates an orphaned thread. Threads complete eventually but are not reaped until GC. For ADAS runtime, timeout rate should be low; acceptable for now. |

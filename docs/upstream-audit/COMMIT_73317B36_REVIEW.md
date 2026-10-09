# Code Review — Commit `73317b36c` [feat(inferenced): job execution framework]

**Commit:** `73317b36c01c81332c7ecdf04ef85448443e2d5e`  
**Subject:** feat(inferenced): implement job execution framework with backend dispatch  
**Reviewed:** 2026-05-30  
**Files changed:** 1 · scope: `system/inferenced/inferenced.py` (+75/-7), `system/inferenced/compute.py` (+1)  
**Method:** 3-angle review (line scan / removed-behavior / cross-file) + verification

---

## Bugs Found and Fixed

All 5 bugs below were fixed in the same session.

---

### Bug 1 — HIGH: `timeout_ms` capnp UInt32 defaults to 0 — every job fails as "Timed out" when client omits the field

| | |
|---|---|
| **File** | `system/inferenced/inferenced.py:204` |
| **Root cause** | `InferenceJobRequest.timeout_ms @6 :UInt32` has no explicit default in the capnp schema, so unset fields read as 0. The timeout check `if exec_time_ms > job.timeout_ms:` compares a non-zero float (even 0.001 ms for the current stub) against 0, which is always True. `success` is then overridden to `False` and the client receives a Timeout failure on every job. |
| **Failure** | Any daemon that submits an `inferenceJobRequest` without setting `timeout_ms` has all its jobs permanently fail with `"Timeout: 0.0ms > 0ms"`. In the current stub implementation (stub returns in ~0.001 ms), this means literally all jobs fail. |
| **Fix** | Added a guard: `if job.timeout_ms > 0 and exec_time_ms > job.timeout_ms:`. Value 0 is treated as "unlimited" (no timeout enforcement). |

---

### Bug 2 — HIGH: `BackendType` IntEnum skips value 3; capnp schema documents `backendType=3` as CPU — all CPU jobs get `ValueError`

| | |
|---|---|
| **File** | `system/inferenced/compute.py:50` |
| **Root cause** | `BackendType` enum: NONE=0, NPU=1, ACL=2, (gap), RGA=4, MPP=5, HAILO=6. The capnp schema (`custom.capnp:559`) documents `backendType=3` as CPU in the field comment: `# 1=NPU, 2=GPU, 3=CPU, 4=RGA, 5=MPP, 6=Hailo`. Any client sending `backendType=3` triggers `BackendType(3)` → `ValueError` in `_execute_job`, caught and returned as `(False, "Invalid backend type: 3")`. |
| **Failure** | The CPU inference path via InferenceD IPC is permanently broken for any client following the schema comment. `BackendType(3)` confirmed to raise `ValueError` at runtime. |
| **Fix** | Added `CPU = 3` to `BackendType` enum. ACL (value 2) already handles both GPU and CPU selection internally; `CPU = 3` is an alias that satisfies the schema contract so clients sending `backendType=3` reach the ACL backend correctly. |

---

### Bug 3 — MEDIUM: Post-hoc timeout overrides `success=True` — a correctly completed job is reported as failed

| | |
|---|---|
| **File** | `system/inferenced/inferenced.py:203–207` |
| **Root cause** | The timeout check runs after `_execute_job()` already returned `(True, "")`. If `exec_time_ms > job.timeout_ms`, the code overwrites `success = False` and `error_reason = "Timeout: ..."`. Since the job completed and produced output, the client is incorrectly told the job failed. A synchronous backend cannot be cancelled mid-flight; the timeout can only be checked after the fact. |
| **Failure** | A backend completes inference in 150 ms against a 100 ms limit. `_execute_job` returns `(True, "")`. Post-hoc check overrides to `success=False`. `_tasks_failed` incremented instead of `_tasks_completed`. Client discards correct results and may retry. |
| **Fix** | Timeout now only logs a warning; it no longer overrides `success`. The `executionTimeMs` field in the result lets the caller decide whether the latency is acceptable. |

---

### Bug 4 — MEDIUM: `_queue_lock` held for entire queue drain including `_execute_job()` calls — `sm.update()` starved during execution

| | |
|---|---|
| **File** | `system/inferenced/inferenced.py:180` |
| **Root cause** | `with self._queue_lock: while self._job_queue: ... _execute_job(job)` holds the queue lock for all job executions. `sm.update(0)` is called before this block and not again until all jobs complete + `_publish_status()` + `rk.keep_time()`. With real inference backends (10–100 ms per job), a queue of N jobs holds the lock for N × inference_time. `_process_job_request()` also acquires `_queue_lock` to enqueue, meaning new requests cannot be added while the queue drains. |
| **Failure** | With real NPU inference (~20 ms/job) and 5 queued jobs, the lock is held for ~100 ms. New `inferenceJobRequest` messages arriving during this window cannot be enqueued until the lock is released. At 10 Hz daemon rate (100 ms cycle), a busy queue can cause consecutive cycles to miss incoming requests. |
| **Fix** | Replaced the `with self._queue_lock: while ...` pattern with a snapshot: take `pending = list(self._job_queue); self._job_queue.clear()` under the lock (brief), then execute each job in `pending` outside the lock. `_process_job_request` can now enqueue during execution. |

---

### Bug 5 — LOW: `_process_job_request` silently drops jobs without sending a response — client blocks indefinitely

| | |
|---|---|
| **File** | `system/inferenced/inferenced.py:119` |
| **Root cause** | If any exception occurs during `InferenceJob` construction (e.g. capnp field name changes, type error, missing attribute), the `except` at line 119 logs a warning and returns. The client that submitted the `inferenceJobRequest` never receives an `inferenceJobResult` and has no way to distinguish "still executing" from "dropped". Neither `_tasks_failed` nor `_tasks_completed` reflects the drop — it is invisible in status metrics. |
| **Failure** | A future capnp field rename or type mismatch causes `AttributeError` during job construction. `_process_job_request` swallows it. Client blocks on its result-wait until its own deadline with no signal. `_tasks_failed` not incremented; the status message shows incorrect metrics. |
| **Fix** | Added a fallback in the `except` block: attempt to send `_submit_job_result(req.jobId, success=False, error_reason=...)` and increment `_tasks_failed`. Wrapped in its own try/except so errors in the fallback path cannot propagate. |

---

## Other Findings (documented, not fixed)

| Finding | Severity | Notes |
|---------|----------|-------|
| `_execute_job` is a stub returning `(True, "")` without doing any inference | — | By design per TODO comments; actual inference wiring is future work |
| Outer `except` in job processing loop is effectively dead code | Low | `_submit_job_result` never raises (internal try/except); stat increments can't raise; making the outer handler unreachable. Harmless — guards against future code additions |

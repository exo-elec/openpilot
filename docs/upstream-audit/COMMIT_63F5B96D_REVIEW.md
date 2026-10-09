# Code Review — Commit `63f5b96dc`

## Commit 63f5b96dc — fix(cereal): add inferenceJobRequest/Result to services.py

---

## Files changed

- `cereal/services.py` (+5 / −3)

---

## Review findings

### `cereal/services.py`

- **🟢 LOW** — `inferenceJobRequest` and `inferenceJobResult` are registered at 100 Hz. This is high for a job-submission channel: most inference jobs take 10–100 ms, so 10–20 Hz would suffice. 100 Hz is harmless but slightly wasteful on `msgq` shared-memory bandwidth.

- **✅ OK** — This is a critical fix. Without registering these services, `PubMaster(['inferenceJobRequest'])` and `SubMaster(['inferenceJobResult'])` raise `KeyError` at initialization, causing `InferenceD` to crash at startup. The commit correctly identifies and resolves the root cause.

- **✅ OK** — The diff is minimal and focused: only adds the two missing service entries with appropriate comments.

---

## Verdict

**Safe to keep.** Critical fix that unblocks the InferenceD IPC path. The 100 Hz rate is a minor tuning point that can be revisited when real hardware latency profiles are available.

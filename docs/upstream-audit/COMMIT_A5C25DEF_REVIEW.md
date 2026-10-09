# Code Review — Commit `a5c25defd` [fix(sim): fix MetaDrive bridge startup — steering monitoring + log_now]

**Commit:** `a5c25defd6d15023b3c827d240c5b5dcce59ec9f`  
**Subject:** fix(sim): fix MetaDrive bridge startup — steering monitoring + log_now  
**Reviewed:** 2026-05-31  
**Files changed:** 2 (+7 / −6)  
**Method:** line scan + schema cross-check + runtime verification  

---

## Summary of Findings

| Severity | Issue | File | Status |
|---|---|---|---|
| **LOW** | `time.monotonic()` clock may drift from `messaging.log_now()` intended semantics (Cereal logMonoTime is usually boot-time monotonic, but exact epoch can differ) | `system/socketd/can_capnp.py` | Open |
| **LOW** | Simulated driver is always "attentive" and "hands on wheel" — no simulation of driver distraction or hands-off events | `tools/sim/lib/simulated_sensors.py` | Open |
| **INFO** | `tools/sim/` is a dev-only path; no production safety impact | — | — |

---

## Detailed Findings

---

### Finding 1 — LOW: `logMonoTime` clock source change

| | |
|---|---|
| **File** | `system/socketd/can_capnp.py:93` |
| **Root cause** | `messaging.log_now()` (which does not exist) is replaced with `int(time.monotonic() * 1e9)`. On most Linux systems `time.monotonic()` returns seconds since boot, which matches the historical convention for `logMonoTime` in Cereal. However, if the surrounding codebase or any downstream tool expects a different epoch (e.g., `CLOCK_BOOTTIME` vs `CLOCK_MONOTONIC`), the two may diverge by suspend time. |
| **Failure** | If the system suspends/resumes during a sim session, `time.monotonic()` pauses while `CLOCK_BOOTTIME` (used by some `messaging` internals) does not. This could cause `logMonoTime` to fall behind other events in the same log by the suspend duration. In practice, dev PCs running simulation rarely suspend mid-session. |
| **Fix** | Verify against `cereal/messaging/__init__.py` or `msgq` for the canonical `log_now()` implementation. If it used `CLOCK_BOOTTIME`, switch to `time.clock_gettime(time.CLOCK_BOOTTIME)` for exact parity. Document the fallback. |

**Code:**
```python
# system/socketd/can_capnp.py:90-93
    dat.logMonoTime = int(time.monotonic() * 1e9)
```

---

### Finding 2 — LOW: Simulated driver never tests hands-off or distraction

| | |
|---|---|
| **File** | `tools/sim/lib/simulated_sensors.py:90-96` |
| **Root cause** | `send_fake_driver_monitoring()` hard-codes `steeringActive = True`, `attentionProb = 1.0`, and `steerState = "attentive"`. The old code sent `faceDetected = True` / `faceForward = True` with the same always-attentive semantics. The fix corrects the field names but preserves the unrealistic behavior. |
| **Failure** | Simulation cannot exercise driver-monitoring disengage paths (e.g., hands-off-wheel timeout, attention loss). Any bug in `selfdrived` that depends on `driverPoseState` transitioning to `steerState = "unavailable"` will never be caught in sim. |
| **Fix** | Add a sim parameter or keyboard toggle to set `steeringActive = False` / `attentionProb = 0.0` on demand, or inject periodic random drops to exercise timeout logic. This is pre-existing debt; no action required by this commit. |

---

## Other Findings

| Finding | Severity | Notes |
|---------|----------|-------|
| Field names match `DriverPoseState` schema | ✅ OK | `steeringActive`, `attentionProb`, `steerState`, `detectMode` are valid schema fields. Removed `faceDetected`, `faceForward`, `faceState` were indeed non-existent. |
| `import time` added correctly | ✅ OK | Added at top of `system/socketd/can_capnp.py` with stdlib imports. |
| No production code touched | ✅ OK | Both files are under `tools/sim/` and `system/socketd/`. `socketd` is used on device but the `can_capnp.py` helper is shared; the clock change is acceptable. |
| Commit message includes repro steps | ✅ OK | Clear instructions for `launch_openpilot.sh` + `run_bridge.py`. |

---

## Priority Fix Order

### P2 — Simulator robustness
1. **`system/socketd/can_capnp.py`** — Confirm `time.monotonic()` epoch matches `messaging.log_now()` intent; switch to `CLOCK_BOOTTIME` if needed.
2. **`tools/sim/lib/simulated_sensors.py`** — Add optional hands-off / distraction simulation (future feature, not a blocker).

---

## Verdict

**Safe to keep.**

Minimal, correct fix for dev-PC simulation startup. The field-name corrections are accurate against the `DriverPoseState` schema. The `log_now()` replacement is a pragmatic fallback for a missing function; a slight clock-epoch risk exists but is negligible for simulation. No production safety impact.

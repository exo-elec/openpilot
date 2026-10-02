# openpilot Topics 01–06 Integration Guide

Step-by-step instructions for integrating ALCC, Live Steer Delay, NNFF prep, DLON, Speed Limit Offset & Policy, and Layered Params into the EOP openpilot fork.

---

## Table of Contents

1. [Prerequisites](#1-prerequisites)
2. [Topic 01 — ALCC (Always Lane Centering Control)](#2-topic-01--alcc)
3. [Topic 02 — Live Steer Delay](#3-topic-02--live-steer-delay)
4. [Topic 03 — NNFF Prep](#4-topic-03--nnff-prep)
5. [Topic 04 — DLON (Dynamic Longitudinal Profile)](#5-topic-04--dlon)
6. [Topic 05 — Speed Limit Offset & Policy](#6-topic-05--speed-limit-offset--policy)
7. [Topic 06 — Layered Params](#7-topic-06--layered-params)
8. [Build & Verify](#8-build--verify)

---

## 1. Prerequisites

Before starting any topic, ensure:

- `cereal/gen/` exists and is up to date. If not:
  ```bash
  cd cereal && scons -j$(nproc)
  ```
- Python `capnp` module is installed:
  ```bash
  pip install pycapnp
  ```
- You have a working EOP openpilot tree at `/home/admin/pilot/openpilot/`.

---

## 2. Topic 01 — ALCC

### Goal
Enhance the existing ALCC (Always Lane Centering Control) with a state machine, event replacement, and Panda lateral mismatch detection.

### Background
Your fork already had basic ALCC in `controlsd.py` — a boolean that keeps `latActive` true even when stock openpilot is disengaged. This topic merges MADS-derived state-machine logic into ALCC so lateral control has proper:
- State transitions (disabled → enabled → overriding → paused → disabled)
- Event replacement (door open / seatbelt → paused instead of full disable)
- Panda `controlsAllowedLateral` mismatch counter
- Per-brand overrides (Hyundai LDA button, Tesla)

### Step 1: Add ALCC params (if not present)

**File:** `common/params_keys.h`

Your fork already has these ALCC params:

```cpp
{"EOPLatALCC", {PERSISTENT, BOOL, "1"}},
{"EOPALCCAllowAlways", {PERSISTENT, BOOL, "0"}},
{"EOPALCCHoldAtStandstill", {PERSISTENT, BOOL, "0"}},
{"EOPALCCBrakeMode", {PERSISTENT, STRING, "Maintain"}},
```

Add the new ones for merged MADS behavior:

```cpp
{"EOPALCCSteeringModeOnBrake", {PERSISTENT, INT, "1"}},   // 0=remain, 1=pause, 2=disengage
{"EOPALCCUnifiedEngagement", {PERSISTENT, BOOL, "0"}},
```

### Step 2: Create `AlccController`

**File:** `selfdrive/controls/lib/alcc.py`

Key components:

| Class/Enum | Purpose |
|------------|---------|
| `AlccState` | `disabled`, `paused`, `enabled`, `softDisabling`, `overriding` |
| `SteeringModeOnBrake` | `REMAIN_ACTIVE`, `PAUSE`, `DISENGAGE` |
| `AlccController` | Main controller with state machine, event filtering, mismatch counter |

Core method signatures:

```python
def update(self, CS, CS_prev, events, panda_states,
           stock_enabled, stock_active,
           calibrated, gear_ok, safety_ok,
           disengage_on_accelerator) -> AlccStatus
```

Behavior:
- When stock is disengaged and ALCC is on, door/seatbelt/wrong-gear events **pause** ALCC instead of disabling it.
- `lateral_mismatch_counter` increments when Panda reports `controlsAllowedLateral=False` but ALCC wants to be active.
- Per-brand `allow_always` lets Hyundai (LDA button) and Tesla engage without cruise main.

### Step 3: Wire into `controlsd.py`

**File:** `selfdrive/controls/lib/alcc.py` is imported and used in `controlsd.py`.

Replace the old simple ALCC boolean logic:

```python
# Old:
alcc_active = alcc_enabled and calibrated and gear_ok and safety_ok and \
              (self.sm['selfdriveState'].enabled or alcc_always)

# New:
alcc_status = self.alcc.update(
    CS=CS, CS_prev=self.CS_prev,
    events=self.events,
    panda_states=self.sm['pandaStates'] if self.sm.valid.get('pandaStates', False) else [],
    stock_enabled=self.sm['selfdriveState'].enabled,
    stock_active=self.sm['selfdriveState'].active,
    calibrated=calibrated, gear_ok=gear_ok, safety_ok=safety_ok,
    disengage_on_accelerator=self.disengage_on_accelerator
)

CC.latActive = (self.sm['selfdriveState'].active or alcc_status.active) and \
               not CS.steerFaultTemporary and not CS.steerFaultPermanent and \
               (not standstill or self.CP.steerAtStandstill or alcc_status.state.hold_at_standstill)
```

---

## 3. Topic 02 — Live Steer Delay

### Goal
Cache the learned lateral actuator delay so it survives process restarts and can be consumed by `modeld`/`controlsd`.

### Step 1: Add Lagd params

**File:** `common/params_keys.h`

```cpp
{"LagdToggle", {PERSISTENT, BOOL, "1"}},
{"LagdToggleDelay", {PERSISTENT, FLOAT, "0.2"}},
{"LagdValueCache", {PERSISTENT, FLOAT, "0.0"}},
```

### Step 2: Cache learned delay in `lagd.py`

**File:** `selfdrive/locationd/lagd.py`

In the main loop (running at 20 Hz), add a periodic cache write:

```python
if sm.frame % 5 == 0:
    lag_learner.update_estimate()
    lag_msg = lag_learner.get_msg(sm.all_checks(), DEBUG)
    pm.send('liveDelay', lag_msg.to_bytes())
    # Cache to params every 60 seconds (1200 frames @ 20 Hz)
    if sm.frame % 1200 == 0:
        params.put_nonblocking("LiveDelay", lag_msg.to_bytes())
```

Also restore on startup in `__init__` or `retrieve_initial_lag()`:

```python
saved = params.get("LiveDelay")
if saved:
    # deserialize and seed the estimator
    ...
```

> `modeld.py` and `controlsd` already subscribe to `liveDelay` and use `liveDelay.lateralDelay`. No further wiring is needed.

---

## 4. Topic 03 — NNFF Prep

### Goal
Prepare `LatControlTorque` to accept a learned delay parameter so a future NNFF (neural-network feedforward) extension can use it without changing the call signature again.

### Step 1: Extend the update signature

**File:** `selfdrive/controls/lib/latcontrol_torque.py`

Change:

```python
def update(self, active, CS, VM, params, steer_limited_by_safety,
           desired_curvature, calibrated_pose, curvature_limited, lat_delay=0.0):
```

Store `lat_delay` as an instance variable for later use:

```python
self.lat_delay = lat_delay
```

> **Note:** Actual NNFF model loading and inference is not implemented yet. This is a preparatory change to minimize future interface churn.

---

## 5. Topic 04 — DLON

### Goal
Enhance the existing DLON (`dlon.py`) with additional E2E triggers ported from CEM: stop prediction, navigation-based switching, and per-trigger enable toggles.

### Background
Your fork already has `dlon.py` — Dynamic Longitudinal Profile — which switches between Chill (ACC) and Experimental (E2E) modes. This topic merges the missing CEM triggers into DLON instead of running a second controller.

### Step 1: Add DLON trigger params

**File:** `common/params_keys.h`

```cpp
{"EOPDLONEnabled", {PERSISTENT, BOOL, "0"}},
{"EOPDLONMode", {PERSISTENT, STRING, "Chill"}},
{"EOPDLONCurvesEnabled", {PERSISTENT, BOOL, "1"}},
{"EOPDLONSlowLeadEnabled", {PERSISTENT, BOOL, "1"}},
{"EOPDLONLowSpeedEnabled", {PERSISTENT, BOOL, "1"}},
{"EOPDLONStopPredictionEnabled", {PERSISTENT, BOOL, "1"}},
{"EOPDLONNavigationEnabled", {PERSISTENT, BOOL, "1"}},
{"EOPDLONSignalEnabled", {PERSISTENT, BOOL, "1"}},
{"EOPDLONSpeedLimitEnabled", {PERSISTENT, BOOL, "1"}},
```

### Step 2: Enhance `dlon.py`

**File:** `selfdrive/controls/lib/dlon.py`

Add missing CEM triggers:

1. **Stop prediction** — `modelV2.action.shouldStop`
   ```python
   def detect_stop_prediction(self, model_v2) -> bool:
       if not model_v2 or not hasattr(model_v2, 'action'):
           return False
       return getattr(model_v2.action, 'shouldStop', False)
   ```

2. **Navigation trigger** — `navInstruction.maneuverDistance < 50m`
   ```python
   def detect_nav_trigger(self, sm) -> bool:
       if 'navInstruction' not in sm.valid or not sm.valid['navInstruction']:
           return False
       nav = sm['navInstruction']
       if hasattr(nav, 'maneuverDistance'):
           return nav.maneuverDistance < self.STOP_PREDICTION_DISTANCE
       return False
   ```

3. **Per-trigger enable toggles** — read from params in `update_params()`
   ```python
   self._trigger_enabled['curves'] = self.params.get_bool("EOPDLONCurvesEnabled")
   self._trigger_enabled['slow_lead'] = self.params.get_bool("EOPDLONSlowLeadEnabled")
   # ... etc
   ```

4. **Exit debounce** — 2.0 s hold before switching back to ACC
   ```python
   now = time.monotonic()
   if use_e2e and not self._active:
       if now - self._enter_ts > 0.5:
           self._active = True
       self._exit_ts = now
   elif not use_e2e and self._active:
       if now - self._exit_ts > self.EXIT_DEBOUNCE:
           self._active = False
       self._enter_ts = now
   ```

5. **Update `_evaluate_auto_mode`** to respect per-trigger toggles:
   ```python
   if self._trigger_enabled['stop_prediction'] and self._should_stop:
       return True
   if self._trigger_enabled['navigation'] and self._nav_trigger:
       return True
   if self._trigger_enabled['slow_lead'] and self.detect_slower_lead(radar_state, v_ego):
       return True
   # ... etc
   ```

> DLON's existing `ModeTransitionManager` with confidence + hysteresis is preserved. The CEM triggers feed into it as additional inputs.

---

## 6. Topic 05 — Speed Limit Offset & Policy

### Goal
Unify MSLC and NSLC outputs under a user-configurable offset and source policy, and publish the resolved limit to the UI.

### Step 1: Add speed-limit params

**File:** `common/params_keys.h`

```cpp
{"SpeedLimitPolicy", {PERSISTENT, INT, "4"}},      # 0=none, 2=map, 3=nav, 4=both
{"SpeedLimitOffsetType", {PERSISTENT, INT, "0"}},  # 0=absolute (m/s), 1=percentage
{"SpeedLimitOffset", {PERSISTENT, FLOAT, "0.0"}},
```

### Step 2: Add `SpeedLimitState` to schema

**File:** `cereal/custom.capnp`

Replace `CustomReserved3`:

```capnp
struct SpeedLimitState @0xda96579883444c35 {
  source @0 :SpeedLimitSource;
  limitMps @1 :Float32;
  offsetMps @2 :Float32;
  finalLimitMps @3 :Float32;
  distanceToChangeM @4 :Float32;
  active @5 :Bool;

  enum SpeedLimitSource {
    none @0;
    car @1;
    map @2;
    nav @3;
  }
}
```

**File:** `cereal/log.capnp`

In `Event` union:

```capnp
speedLimitState @109 :Custom.SpeedLimitState;
```

**File:** `cereal/services.py`

```python
"speedLimitState": (True, 4., 1),
```

### Step 3: Create `SpeedLimitResolver`

**File:** `selfdrive/controls/lib/speed_limit_resolver.py`

Responsibilities:
1. Read `SpeedLimitPolicy`, `SpeedLimitOffset`, `SpeedLimitOffsetType` from params.
2. Take `mslc_limit_mps` and `nslc_limit_mps` as inputs.
3. Apply policy selection:
   - `0` → ignore all limits
   - `2` → use MSLC only
   - `3` → use NSLC only
   - `4` → use `min(mslc, nslc)`
4. Apply offset:
   - Absolute: `final = raw + offset`
   - Percentage: `final = raw * (1 + offset)`
5. Return a `ResolvedLimit` dataclass.

### Step 4: Wire into `longitudinal_planner.py`

**File:** `selfdrive/controls/lib/longitudinal_planner.py`

1. Import:
   ```python
   from openpilot.selfdrive.controls.lib.speed_limit_resolver import SpeedLimitResolver
   ```

2. In `__init__`:
   ```python
   self.sl_resolver = SpeedLimitResolver()
   self.sl_resolved = None
   ```

3. In `update()`, **after** MSLC/NSLC calls but **before** TLSC/SQSC/RCD:
   ```python
   self.sl_resolved = self.sl_resolver.update(
       mslc_limit_mps=self.mslc_v_target,
       nslc_limit_mps=self.nslc_v_target,
       v_ego=v_ego,
       distance_to_change_m=getattr(self.mslc, 'distance_to_limit', 0.0) or getattr(self.nslc, 'distance_to_limit', 0.0),
   )
   v_cruise = self.sl_resolver.apply_to_v_cruise(v_cruise, self.sl_resolved)
   ```

4. In `publish()`:
   ```python
   if self.sl_resolved is not None:
       sl_send = messaging.new_message('speedLimitState')
       sl = sl_send.speedLimitState
       sl.source = self.sl_resolved.source
       sl.limitMps = float(self.sl_resolved.limit_mps)
       sl.offsetMps = float(self.sl_resolved.offset_mps)
       sl.finalLimitMps = float(self.sl_resolved.final_limit_mps)
       sl.distanceToChangeM = float(self.sl_resolved.distance_to_change_m)
       sl.active = self.sl_resolved.active
       pm.send('speedLimitState', sl_send)
   ```

---

## 7. Topic 06 — Layered Params

### Goal
Provide a `Params` wrapper that reads from a hierarchy of storage layers (volatile → cache → persistent) and writes to the first writable layer.

### Step 1: Create `LayeredParams`

**File:** `common/layered_params.py`

Constructor signature:

```python
LayeredParams(layers: List[Tuple[str, bool]])
```

Each tuple is `(path, writable)`:
- `path` → passed to `Params(path)`; `""` means default persistent storage.
- `writable` → whether `put`/`remove` target this layer.

Usage example:

```python
from openpilot.common.layered_params import LayeredParams

lp = LayeredParams([
    ("/dev/shm/params", True),   # runtime / memory-backed
    ("/cache/params", True),     # semi-persistent cache
    ("", False),                  # default persistent (fallback read-only)
])

val = lp.get("EOPLatALCC")          # tries /dev/shm → /cache → default
lp.put_bool("EOPLatALCC", True)     # writes to /dev/shm/params
```

Implemented methods:
- `get()`, `get_bool()`, `get_float()`, `get_int()` — read with fallback
- `put()`, `put_bool()`, `put_nonblocking()` — write to first writable layer
- `remove()` — delete from first writable layer
- `clear_all()` — clear all writable layers

---

## 8. Build & Verify

### 8.1 Rebuild cereal

```bash
cd /home/admin/pilot/openpilot/cereal
scons -j$(nproc)
```

This regenerates `cereal/gen/` with the new `SpeedLimitState` struct.

### 8.2 Syntax check all new/modified Python files

```bash
cd /home/admin/pilot/openpilot
python3 -m py_compile \
  selfdrive/controls/lib/alcc.py \
  selfdrive/controls/lib/dlon.py \
  selfdrive/controls/lib/speed_limit_resolver.py \
  common/layered_params.py \
  selfdrive/controls/controlsd.py \
  selfdrive/selfdrived/selfdrived.py \
  selfdrive/locationd/lagd.py \
  selfdrive/controls/lib/latcontrol_torque.py
```

### 8.3 Runtime verification checklist

| Check | How |
|-------|-----|
| ALCC state machine | Open door while ALCC active; verify lateral pauses instead of disabling. Close door; verify lateral resumes. |
| ALCC mismatch counter | Induce Panda lateral mismatch; verify counter increments in `controlsState` debug. |
| LiveDelay caching | Kill `lagd`, restart; verify estimate resumes near previous value. |
| DLON stop prediction | Enable DLON AUTO; approach a stop sign; verify `dlonE2EEnabled` flips true. |
| DLON exit debounce | Clear stop condition; verify E2E stays active for ~2 s before returning to ACC. |
| Speed limit offset | Set `SpeedLimitOffset=5.0` (m/s); verify `speedLimitState.finalLimitMps` = raw + offset. |
| Layered Params | Write to `/dev/shm/params`, read back; reboot; verify fallback to persistent layer. |

---

## Appendix: What was removed / merged

| Removed | Merged into | Why |
|---------|-------------|-----|
| `selfdrive/mads/` (MADS) | `selfdrive/controls/lib/alcc.py` | ALCC already handled always-on lateral; MADS state machine was merged into it. |
| `conditional_experimental_mode.py` (CEM) | `selfdrive/controls/lib/dlon.py` | DLON already handled Chill/Experimental switching; CEM triggers were merged into it. |
| `MadsState` in `custom.capnp` | Removed | No longer needed — ALCC state is internal to `controlsd.py`. |
| `madsState` service | Removed | No longer published as a separate message. |

## Appendix: File Inventory

| File | Status |
|------|--------|
| `cereal/custom.capnp` | Modified — `SpeedLimitState` (restored `CustomReserved2`) |
| `cereal/log.capnp` | Modified — `speedLimitState @109` (removed `madsState`) |
| `cereal/services.py` | Modified — `speedLimitState` service (removed `madsState`) |
| `common/params_keys.h` | Modified — ALCC, DLON trigger, Lagd, SpeedLimit keys (removed MADS/CEM keys) |
| `common/layered_params.py` | **Created** |
| `selfdrive/controls/lib/alcc.py` | **Created** — merged MADS logic into ALCC |
| `selfdrive/controls/lib/dlon.py` | Modified — merged CEM triggers |
| `selfdrive/controls/lib/speed_limit_resolver.py` | **Created** |
| `selfdrive/controls/lib/longitudinal_planner.py` | Modified — `SpeedLimitResolver` integration |
| `selfdrive/controls/lib/latcontrol_torque.py` | Modified — `lat_delay` parameter |
| `selfdrive/controls/controlsd.py` | Modified — uses `AlccController` |
| `selfdrive/selfdrived/selfdrived.py` | Modified — removed MADS/CEM |
| `selfdrive/selfdrived/events.py` | Modified — hardware fault alerts, ALCC event comments |
| `selfdrive/locationd/lagd.py` | Modified — periodic param cache |

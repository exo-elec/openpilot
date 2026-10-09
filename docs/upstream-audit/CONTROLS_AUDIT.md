# Controls Audit — Step 15

**Scope:** `selfdrive/controls/` — all files that differ from upstream `c085b8af1`  
**Reviewer:** Claude Code  
**Date:** 2026-05-24  
**Status:** 🔴 CRITICAL bugs found — do not merge to production until fixes land

---

## Overview

37 files total changed vs upstream. Split into two categories:

| Category | Count | Verdict |
|---|---|---|
| **Group A** — net-new EOP lib files (pure additions) | 25 files | ✅ Keep, no audit needed |
| **Group B** — upstream files modified | 7 files | See per-file sections below |
| **Group C** — new EOP tests | 3 files | ✅ Keep |

---

## Group A: Net-new EOP lib files (no upstream equivalent)

All 25 files are pure additions. No upstream behavior modified. Classification: **KEEP**.

| File | Feature | Status |
|---|---|---|
| `lib/aeb.py` | Autonomous Emergency Braking | ✅ Keep |
| `lib/alcc.py` | Always Lane Centering Control | ✅ Keep |
| `lib/bsd.py` | Blind Spot Detection | ✅ Keep |
| `lib/cat.py` | Car Adaptive Tuning | ✅ Keep |
| `lib/cslb.py` | Curve Speed Look-ahead Braking | ✅ Keep |
| `lib/ddsc.py` | Dynamic Disengage Speed Control | ✅ Keep |
| `lib/dlat.py` | Dynamic Lateral Profile | ✅ Keep |
| `lib/dlon.py` | Dynamic Longitudinal Profile | ✅ Keep |
| `lib/driver_prefs.py` | Driver preference cache | ✅ Keep |
| `lib/eop_utils.py` | EOP utility helpers | ✅ Keep |
| `lib/following_distance.py` | Following distance controller | ✅ Keep |
| `lib/lc_lead_handoff.py` | Lane Change Lead Handoff | ✅ Keep |
| `lib/mslc.py` | Map Speed Limit Control | ✅ Keep |
| `lib/mtsc.py` | Map Turn Speed Control | ✅ Keep |
| `lib/nnlc/__init__.py` | NNLC package init | ✅ Keep |
| `lib/nnlc/helpers.py` | NNFF model path resolution | ✅ Keep |
| `lib/nnlc/model.py` | NNTorqueModel wrapper | ✅ Keep |
| `lib/nslc.py` | Navigation Speed Limit Control | ✅ Keep |
| `lib/rcd.py` | Road Condition Detection | ✅ Keep |
| `lib/red.py` | Road Edge Detection | ✅ Keep |
| `lib/speed_limit_resolver.py` | Speed limit policy resolver | ✅ Keep |
| `lib/sqsc.py` | Surface Quality Speed Controller | ✅ Keep |
| `lib/surface_quality_db.py` | Surface quality data store | ✅ Keep |
| `lib/tlsc.py` | Traffic Light Speed Control | ✅ Keep |
| `lib/vtsc.py` | Vision Turn Speed Control | ✅ Keep |

---

## Group B: Modified upstream files

### Legend

| Severity | Meaning |
|---|---|
| 🔴 CRITICAL | Will crash at runtime or safety-incorrect behavior |
| 🟠 HIGH | Wrong result (no crash), behavioral regression, or uninitialized state |
| 🟡 MEDIUM | Code quality, dead code, misleading comment |
| 🟢 LOW | Style, minor inconsistency |

---

### B1 — `selfdrive/controls/controlsd.py`

**Summary of changes vs upstream:**
- `opendbc.car.interfaces` / `VehicleModel` → `vehicled.car` (Tesla fork decision — necessary)
- `driverMonitoringState` removed from SubMaster
- Added EOP messages to SubMaster/PubMaster
- Added CAT / DLAT / RED / BSD / AEB integrations
- ALCC integration
- `pid_accel_limits` replaced from CI method to hardcoded Tesla constants
- `cs.forceDecel` simplified (removed driver monitoring check)

---

#### 🔴 B1-1: `self.alcc` never initialized

**File:** `controlsd.py:224`  
**Status:** 🔴 CRITICAL — runtime crash

```python
# __init__ defines: self.cat, self.dlat, self.red, self.bsd, self.aeb
# Missing: self.alcc = ALCC(self.CP)

# state_control() line 224:
alcc_status = self.alcc.update(...)  # AttributeError: 'Controls' has no attribute 'alcc'
```

`ALCC` class exists at `selfdrive/controls/lib/alcc.py` but is never imported and never instantiated in `__init__`. Every call to `state_control()` crashes here.

**Fix required:**
```python
# In imports:
from openpilot.selfdrive.controls.lib.alcc import ALCC

# In __init__:
self.alcc = ALCC(self.CP)
self.alcc_status = None  # initialized before first publish()
```

---

#### 🔴 B1-2: `self.CS_prev`, `self.events`, `self.disengage_on_accelerator` never initialized

**File:** `controlsd.py:225–231`  
**Status:** 🔴 CRITICAL — runtime crash (same call as B1-1)

```python
alcc_status = self.alcc.update(
  CS=CS, CS_prev=self.CS_prev,          # AttributeError
  events=self.events,                    # AttributeError
  ...
  disengage_on_accelerator=self.disengage_on_accelerator  # AttributeError
)
```

None of `self.CS_prev`, `self.events`, or `self.disengage_on_accelerator` appear in `__init__`. All three are state the ALCC controller needs on every frame.

**Fix required:**
```python
# In __init__:
self.CS_prev = None
self.events = []
self.disengage_on_accelerator = self.params.get_bool("DisengageOnAccelerator")

# At the end of state_control(), before return:
self.CS_prev = CS
```

---

#### 🔴 B1-3: `HardwareCapability` and `HARDWARE` not imported

**File:** `controlsd.py:177`  
**Status:** 🔴 CRITICAL — NameError whenever BSD is enabled

```python
hailo_present = HardwareCapability.HAILO in HARDWARE.get_capabilities()
# NameError: name 'HardwareCapability' is not defined
# NameError: name 'HARDWARE' is not defined
```

Both symbols are used unconditionally inside the BSD-enabled block but neither is imported at the top of the file.

**Fix required:**
```python
# Add to imports:
from openpilot.system.hardware import HARDWARE
from openpilot.system.hardware.base import HardwareCapability
```
(Verify exact import path from `system/hardware/` for EOP's HAL.)

---

#### 🟠 B1-4: Variable `p` shadowed inside actuator NaN check loop

**File:** `controlsd.py:315`  
**Status:** 🟠 HIGH — silent wrong behavior

```python
p = self._cached_eop_params   # line 143 — dict of EOP params

# ... 170 lines later ...
for p in ACTUATOR_FIELDS:     # line 315 — shadows the dict!
  attr = getattr(actuators, p)
```

After the loop, `p` is the last field name string (e.g. `"accel"`), not the params dict. Any code after the loop that calls `p.get('EOPAEBEnabled')` would crash with `AttributeError: 'str' object has no attribute 'get'`. Currently the loop is near the end of `state_control()` so there's no call after it in this method, but this is fragile and will break if code is reordered.

**Fix required:** Rename the loop variable.
```python
for field_name in ACTUATOR_FIELDS:
  attr = getattr(actuators, field_name)
  ...
  setattr(actuators, field_name, 0.0)
```

---

#### 🟡 B1-5: `driverMonitoringState` removed from SubMaster, `forceDecel` simplified

**File:** `controlsd.py:47, 389`  
**Status:** 🟡 MEDIUM — intentional but undocumented safety trade-off

Upstream `forceDecel`:
```python
cs.forceDecel = bool((self.sm['driverMonitoringState'].awarenessStatus < 0.) or
                     (self.sm['selfdriveState'].state == State.softDisabling))
```

EOP version:
```python
cs.forceDecel = bool(self.sm['selfdriveState'].state == State.softDisabling)
```

Driver monitoring awareness check was removed. This is a deliberate product decision (EOP uses its own monitoring daemons), but it should be documented with an explicit comment explaining which daemon now handles driver inattention escalation, and that this is a conscious safety trade-off.

---

#### 🟡 B1-6: Blank line in `state_control` after blinker block

**File:** `controlsd.py:256`  
**Status:** 🟡 LOW — cosmetic

Extra blank line inserted after the blinker if-block. Trivial but adds noise to the diff.

---

#### Summary for B1

| ID | Severity | Issue | Fixed? |
|----|----------|-------|--------|
| B1-1 | 🔴 CRITICAL | `self.alcc` never initialized | ☐ |
| B1-2 | 🔴 CRITICAL | `self.CS_prev`, `self.events`, `self.disengage_on_accelerator` not init | ☐ |
| B1-3 | 🔴 CRITICAL | `HardwareCapability`, `HARDWARE` not imported | ☐ |
| B1-4 | 🟠 HIGH | `p` variable shadowed by loop over `ACTUATOR_FIELDS` | ☐ |
| B1-5 | 🟡 MEDIUM | `forceDecel` drops driver monitoring check — add comment | ☐ |
| B1-6 | 🟢 LOW | Extra blank line at line 256 | ☐ |

---

### B2 — `selfdrive/controls/lib/desire_helper.py`

**Summary of changes vs upstream:**
- `LANE_CHANGE_SPEED_MIN`: `20 * CV.MPH_TO_MS` (8.94 m/s) → `11.0` m/s
- Added LCA (gap check, lane width check, nudgeless auto mode, BSD mid-maneuver abort)
- Added `one_lane_change` flag
- Added turn desires at low speed (blinker + below lane change speed + not standstill)
- `update()` signature extended with optional `model_v2`, `radar_state`, `blind_spot_alert`
- Upstream keepLeft/keepRight pulse suppression logic removed

---

#### 🔴 B2-1: Mid-maneuver BSD abort commands sudden direction flip

**File:** `desire_helper.py` (laneChangeStarting block)  
**Status:** 🔴 CRITICAL — potentially dangerous lateral maneuver

```python
elif self.lane_change_state == LaneChangeState.laneChangeStarting:
  blindspot_detected = self._blindspot_blocked(...)
  if blindspot_detected:
    # Abort back to original lane
    self.lane_change_direction = (
      LaneChangeDirection.left if self.lane_change_direction == LaneChangeDirection.right
      else LaneChangeDirection.right
    )
    self.lane_change_state = LaneChangeState.laneChangeFinishing
```

Problems:
1. `laneChangeFinishing` in upstream means "car has moved to new lane, fade back to lane-keep". Entering `laneChangeFinishing` with the direction flipped sends the model a sudden return-to-original-lane command while the vehicle is mid-movement.
2. The correct abort is to immediately go to `LaneChangeState.off` and let the model re-center, not `laneChangeFinishing`.
3. The direction flip combined with `laneChangeFinishing` could command a snap maneuver at highway speed.

**Fix required:**
```python
if blindspot_detected:
  # Abort: return to lane-keep immediately
  self.lane_change_state = LaneChangeState.off
  self.lane_change_direction = LaneChangeDirection.none
  self.lane_change_ll_prob = 1.0   # reset fade-out
  self.lane_change_delay_start = 0.0
```

---

#### 🟠 B2-2: Upstream keepLeft/keepRight suppression logic removed

**File:** `desire_helper.py` (bottom of update)  
**Status:** 🟠 HIGH — behavioral regression vs upstream

Original upstream logic (last 2 lines of preLaneChange block):
```python
elif self.desire in (log.Desire.keepLeft, log.Desire.keepRight):
  self.desire = log.Desire.none
```

This suppresses the keepLeft/keepRight desire once per second during `preLaneChange` (keep_pulse_timer > 1s). Without this, the model receives continuous keepLeft/keepRight during the pre-change phase, which changes how aggressively it holds the lane boundary.

The EOP diff removes these two lines entirely — this is a silent behavioral change to upstream lane change behavior for all users, not just EOP features.

**Fix required:** Restore the two lines, or document the intentional change.

---

#### 🟠 B2-3: Possible yRel sign convention mismatch between radar and modelV2

**File:** `desire_helper.py:_evaluate_gap()`  
**Status:** 🟠 HIGH — potentially wrong gap evaluation direction

```python
# Radar leads (if available)
if direction == 'left' and lead.yRel < -1.5:   # negative = left?
    adjacent_leads.append(...)

# Vision leads from modelV2 (pure-camera fallback)
if direction == 'left' and lead.y[0] > 1.5:    # positive = left (comment says so)
```

The comment on the vision path says "positive = left". If that is correct, then for radar `yRel < -1.5` (negative = left) is the opposite sign convention. Verify the sign of `radarState.leadOne.yRel`: in upstream openpilot `yRel` is positive left (driver's left is positive in the ego frame). If that is the case:
- Radar `yRel < -1.5` → checks for RIGHT-side leads while the code intends LEFT
- Vision `y[0] > 1.5` → correctly checks for LEFT-side leads

**Action required:** Confirm the radarState yRel sign convention in `selfdrive/controls/radard.py` and fix whichever branch is wrong.

---

#### 🟡 B2-4: `_load_params` docstring says "Cached — refresh no more than once per second" but `__init__` bypasses the timer

**File:** `desire_helper.py:_load_params()`  
**Status:** 🟡 MEDIUM — misleading (works correctly, confusing to read)

`__init__` calls `self._load_params()` with `_last_param_update = 0.0`. Since `time.monotonic()` at startup is >> 1.0 on any normally-running system, the guard `if now - self._last_param_update < self._param_update_interval` is False and loading proceeds correctly. Technically fine, but fragile on a system with extremely short uptime (< 1s).

More importantly, the `update()` method also calls `self._load_params()` at the very top before any business logic. The `__init__` call is redundant (it's just eagerly warming the cache). This is fine but worth noting.

---

#### 🟡 B2-5: `LANE_CHANGE_SPEED_MIN` changed without comment

**File:** `desire_helper.py:line 5`  
**Status:** 🟡 MEDIUM — undocumented behavioral change

```python
# Upstream:
LANE_CHANGE_SPEED_MIN = 20 * CV.MPH_TO_MS   # ≈ 8.94 m/s

# EOP:
LANE_CHANGE_SPEED_MIN = 11.0  # ~40 km/h, EOP 3-zone spec...
```

The constant was increased from ~8.94 m/s to 11.0 m/s. The comment references "EOP 3-zone spec" but there's no link to that spec. Add a reference to the relevant requirements doc.

---

#### Summary for B2

| ID | Severity | Issue | Fixed? |
|----|----------|-------|--------|
| B2-1 | 🔴 CRITICAL | BSD mid-maneuver abort uses wrong state + direction flip | ☐ |
| B2-2 | 🟠 HIGH | keepLeft/keepRight suppression logic silently removed | ☐ |
| B2-3 | 🟠 HIGH | Radar yRel sign vs modelV2 y sign convention — verify | ☐ |
| B2-4 | 🟡 MEDIUM | `_load_params` in `__init__` is redundant, docstring misleading | ☐ |
| B2-5 | 🟡 MEDIUM | `LANE_CHANGE_SPEED_MIN` changed without spec reference | ☐ |

---

### B3 — `selfdrive/controls/lib/latcontrol_torque.py`

**Summary of changes vs upstream:**
- `CI` allowed to be `None` (necessary — opendbc removed)
- `opendbc.car.lateral.get_friction` / `FRICTION_THRESHOLD` removed, replaced with local reimplementation
- Added NNFF (Neural Network FeedForward) lateral control, sourced from FrogPilot
- `update()` no longer has early `return 0., 0., pid_log` block — now falls through directly

---

#### 🟠 B3-1: Local `get_friction` is missing sign and uses wrong formula

**File:** `latcontrol_torque.py:get_friction()`  
**Status:** 🟠 HIGH — asymmetric friction compensation (wrong result, no crash)

Upstream `get_friction` (from `opendbc.car.lateral`):
```python
def get_friction(lateral_accel_error, lateral_accel_deadzone, friction_threshold, torque_params):
  friction_compensation = interp(
    abs(lateral_accel_error) - lateral_accel_deadzone,
    [0, friction_threshold],
    [0, torque_params.friction]
  )
  return sign(lateral_accel_error) * friction_compensation
```

EOP reimplementation:
```python
def get_friction(lateral_accel_error: float, lateral_accel_deadzone: float, friction_tolerance: float) -> float:
  friction = max(0.0, lateral_accel_error - lateral_accel_deadzone)
  friction = min(friction, friction_tolerance)
  return friction
```

Three problems:
1. **Missing `sign(lateral_accel_error)` multiplication** — friction compensation is always ≥ 0. It only helps overcome static friction when turning in the positive lateral accel direction. Turning the other way gets no friction compensation at all. This makes the torque controller asymmetric.
2. **`max(0.0, error - deadzone)` vs `interp(abs(error) - deadzone, ...)`** — the upstream version uses `abs` before comparing to deadzone, ensuring the deadzone is symmetric. The EOP version only subtracts deadzone from the raw (possibly negative) error, then clips to 0, effectively ignoring negative errors entirely.
3. **`FRICTION_THRESHOLD` constant removed** — upstream uses it for the interpolation range. The EOP version clips directly to `friction_tolerance` = `torque_params.friction`, skipping the interpolation ramp.

**Fix required:** Either restore the import and remove the local reimplementation, or reproduce the upstream formula correctly:
```python
def get_friction(lateral_accel_error: float, lateral_accel_deadzone: float,
                  friction_threshold: float, torque_params) -> float:
  from openpilot.common.numpy_fast import interp
  friction_compensation = interp(
    abs(lateral_accel_error) - lateral_accel_deadzone,
    [0.0, friction_threshold],
    [0.0, torque_params.friction]
  )
  return (1.0 if lateral_accel_error > 0 else -1.0 if lateral_accel_error < 0 else 0.0) * friction_compensation
```

**Note:** Since opendbc is removed, restoring the import isn't an option. Fix the local reimplementation.

---

#### 🟡 B3-2: NNFF input validation only checks `ff_in` size, not `sp_in` or `meas_in`

**File:** `latcontrol_torque.py:_build_nn_inputs` usage in `update()`  
**Status:** 🟡 MEDIUM — silent wrong output if other inputs are wrong size

```python
if len(ff_in) == self.nn_model.input_size:
  pid_log.error = float(self.nn_model.evaluate(sp_in) - self.nn_model.evaluate(meas_in))
  ...
  nn_torque = float(np.clip(self.nn_model.evaluate(ff_in), ...))
```

If `sp_in` or `meas_in` have the wrong size, `nn_model.evaluate()` may raise an exception or return nonsense. The try/except block will catch it and fall back to PID, which is safe, but the check should cover all three inputs.

**Fix:**
```python
if (len(ff_in) == self.nn_model.input_size and
    len(sp_in) == self.nn_model.input_size and
    len(meas_in) == self.nn_model.input_size):
```

---

#### 🟡 B3-3: Dead `upper_idx` computation doesn't match usage

**File:** `latcontrol_torque.py` (NN jerk block)  
**Status:** 🟡 MEDIUM — misleading + inefficient

```python
upper_idx = next((i for i, t in enumerate(ModelConstants.T_IDXS) if t > lookahead_s),
                 len(ModelConstants.T_IDXS) - 1)
predicted = _get_predicted_lateral_jerk(acc.y, _T_DIFFS)
desired_jerk = (float(np.interp(0.1, ModelConstants.T_IDXS, acc.y)) - desired_lateral_accel) / 0.1
lookahead_jerk = _get_lookahead_value(predicted[1:upper_idx], desired_jerk)
```

`predicted[1:upper_idx]` — `upper_idx` is derived from `ModelConstants.T_IDXS` (which has 33 elements), but `predicted` has length `len(acc.y) - 1` (typically much shorter). When `len(acc.y) < upper_idx`, the slice silently truncates to the end of `predicted`. The name `upper_idx` is misleading since it rarely acts as an effective upper bound. Rename to `t_lookahead_idx` and add a `min()` guard:
```python
t_lookahead_idx = min(
  next((i for i, t in enumerate(ModelConstants.T_IDXS) if t > lookahead_s), len(ModelConstants.T_IDXS) - 1),
  len(predicted)
)
lookahead_jerk = _get_lookahead_value(predicted[1:t_lookahead_idx], desired_jerk)
```

---

#### 🟢 B3-4: `_roll_pitch_adjust` is dead code in the non-NN path

**File:** `latcontrol_torque.py`  
**Status:** 🟢 LOW — dead helper (no crash)

`_roll_pitch_adjust(roll, pitch)` is only called inside `_build_nn_inputs`. If NNFF is disabled (default), it's never invoked. It's defined at module level unnecessarily. Move it inside `_build_nn_inputs` or remove if NN-only is clearly the intent.

---

#### Summary for B3

| ID | Severity | Issue | Fixed? |
|----|----------|-------|--------|
| B3-1 | 🟠 HIGH | `get_friction` missing sign + wrong formula → asymmetric torque | ☐ |
| B3-2 | 🟡 MEDIUM | NNFF input size check only covers `ff_in`, not `sp_in`/`meas_in` | ☐ |
| B3-3 | 🟡 MEDIUM | `upper_idx` misleading name; slice may silently truncate | ☐ |
| B3-4 | 🟢 LOW | `_roll_pitch_adjust` dead in non-NN path | ☐ |

---

### B4 — `selfdrive/controls/lib/longcontrol.py`

**Summary of changes vs upstream:**
- Added TJA (Traffic Jam Assist): standstill hold timeout + smooth start ramp
- `LongCtrlState.starting`: was `self.CP.startAccel`; now `_apply_tja_ramp(a_target, ...)`

---

#### 🟠 B4-1: TJA disabled path silently changes starting acceleration

**File:** `longcontrol.py:update()`  
**Status:** 🟠 HIGH — unintentional behavioral change for all configurations

Upstream:
```python
elif self.long_control_state == LongCtrlState.starting:
  output_accel = self.CP.startAccel
  self.reset()
```

EOP (with TJA disabled):
```python
elif self.long_control_state == LongCtrlState.starting:
  output_accel = self._apply_tja_ramp(a_target, CS.vEgo, self.long_control_state)
  self.reset()
```

`_apply_tja_ramp` with `_tja_enabled = False` returns `a_target` unchanged (the planner target). Upstream used the fixed `CP.startAccel` (a small constant from `CarParams`). These are different values — the planner's `a_target` is not the same as `CP.startAccel`, especially at the moment of standstill-to-moving transition.

This changes starting behavior even when TJA is off. If the intent is "use planner accel instead of CP.startAccel always", that's valid but must be documented. If not, add a fallback:
```python
def _apply_tja_ramp(self, a_target, v_ego, long_control_state):
  if not self._tja_enabled:
    return self.CP.startAccel   # restore upstream behavior when TJA off
  ...
```

---

#### 🟡 B4-2: `_update_tja_params` comment contradicts rate-limiting logic

**File:** `longcontrol.py:update()`  
**Status:** 🟡 MEDIUM — misleading comment

```python
self._update_tja_params()  # Refresh cached TJA params once per frame
```

The comment says "once per frame" (100 Hz → every 10ms). But `_update_tja_params` has:
```python
if now - self._last_tja_param_update < self._tja_param_interval:  # 1.0 second
  return
```

It actually updates at most once per second. Fix the comment:
```python
self._update_tja_params()  # Refresh cached TJA params (rate-limited to 1 Hz)
```

---

#### 🟡 B4-3: `tja_resume_required` state never exposed or consumed

**File:** `longcontrol.py`  
**Status:** 🟡 MEDIUM — dead state

`self.tja_resume_required` is set to `True` when the hold timeout expires, but it is never read outside of `_check_tja_hold_timeout`. No message is published, no flag is checked by any caller, and `override_should_stop` is the only mechanism (`True` forces stopping state). The field can be removed or must be wired up to a TTS/alert system.

---

#### 🟢 B4-4: Spurious blank line added in `long_control_state_trans`

**File:** `longcontrol.py:52`  
**Status:** 🟢 LOW — cosmetic diff noise

A blank line was inserted after `long_control_state_trans`. Trivial, but shows up in the upstream diff and makes `git blame` harder.

---

#### Summary for B4

| ID | Severity | Issue | Fixed? |
|----|----------|-------|--------|
| B4-1 | 🟠 HIGH | TJA-disabled path changes `startAccel` behavior silently | ☐ |
| B4-2 | 🟡 MEDIUM | `_update_tja_params` comment says "per frame", actually 1 Hz | ☐ |
| B4-3 | 🟡 MEDIUM | `tja_resume_required` field set but never read/published | ☐ |
| B4-4 | 🟢 LOW | Spurious blank line in `long_control_state_trans` | ☐ |

---

### B5 — `selfdrive/controls/lib/longitudinal_planner.py`

**Summary of changes vs upstream:**
- `opendbc.car.interfaces.ACCEL_MIN/ACCEL_MAX` → `vehicled.tesla.values.CarControllerParams` (necessary)
- `selfdrive.car.cruise` → `vehicled.car.cruise` (necessary)
- Acceleration profiles: normal / eco / sport (new feature)
- `A_CRUISE_MAX_BP` tuning values changed
- 14 EOP speed controllers wired in

---

#### 🟡 B5-1: Comment mischaracterizes upstream ACCEL limits

**File:** `longitudinal_planner.py:13`  
**Status:** 🟡 MEDIUM — wrong comment misleads future auditors

```python
# EOP-CLEANUP: Was hardcoded Tesla limits. Now using vehicled car params.
```

This is backwards. The **upstream** code used `ACCEL_MIN/ACCEL_MAX` from `opendbc.car.interfaces` which are generic per-car limits. The **EOP** code now hardcodes Tesla limits from `vehicled.tesla.values.CarControllerParams`. The comment has the story inverted.

**Fix:**
```python
# Upstream used generic opendbc.car.interfaces.ACCEL_MIN/ACCEL_MAX (per-car).
# EOP uses Tesla CarControllerParams directly (single-vehicle fork).
```

---

#### 🟡 B5-2: `A_CRUISE_MAX_BP` breakpoint change undocumented

**File:** `longitudinal_planner.py`  
**Status:** 🟡 MEDIUM — silent tuning change

```python
# Upstream:
A_CRUISE_MAX_BP = [0., 10.0, 25., 40.]

# EOP:
A_CRUISE_MAX_BP = [0., 11.0, 22.0, 36.0]
```

The breakpoints shifted. This changes the maximum cruise acceleration profile at intermediate speeds for all profiles. The change is plausible (metric-round numbers instead of imperial-ish values) but isn't documented. Add a comment or link to the relevant tuning decision.

---

#### 🟢 B5-3: Eco/sport profile constants are defined but the profile-select logic is not shown in the diff

**File:** `longitudinal_planner.py`  
**Status:** 🟢 LOW — code completeness question

The dict `_A_CRUISE_PROFILES` is declared but the diff doesn't show where the profile is read from a param or applied. Verify that `normal`, `eco`, and `sport` profiles are actually wired up and that `normal` is the default when the param is unset, to ensure no regression on first startup.

---

#### Summary for B5

| ID | Severity | Issue | Fixed? |
|----|----------|-------|--------|
| B5-1 | 🟡 MEDIUM | Comment inverts the upstream vs EOP story on ACCEL limits | ☐ |
| B5-2 | 🟡 MEDIUM | `A_CRUISE_MAX_BP` tuning values changed without documentation | ☐ |
| B5-3 | 🟢 LOW | `_A_CRUISE_PROFILES` wiring not visible in diff — verify default | ☐ |

---

### B6 — `selfdrive/controls/plannerd.py`

**Summary of changes vs upstream:**
- `config_realtime_process(5, ...)` → `set_daemon_affinity("plannerd")` + `config_realtime_process(DT_MDL, ...)`
- Added EOP messages to PubMaster and SubMaster

---

#### 🟢 B6-1: All changes are additive and correct

The `config_realtime_process` signature change is intentional — EOP changed the function's first argument from a CPU core number to a `dt` float (documented in `common/realtime.py:58`). The CPU affinity is now handled by `set_daemon_affinity`. This is consistent across all daemon startup functions.

SubMaster additions (`mapData`, `navInstruction`, `stereoObjects`, `surfaceStatus`, `liveLocationKalman`, `enhancedTrajectory`) and PubMaster additions (`speedLimitState`, `ttsRequest`) all correspond to real EOP services.

**Status:** ✅ Clean — no issues.

---

### B7 — `selfdrive/controls/radard.py`

**Summary of changes vs upstream:**
- `config_realtime_process(5, ...)` → `set_daemon_affinity("radard")` + `config_realtime_process(DT_MDL, ...)`

**Status:** ✅ Clean — same pattern as B6, no issues.

---

---

### B0 — `selfdrive/controls/lib/alcc.py` (new finding during fix pass)

#### 🔴 B0-1: `alcc.py` imports `opendbc.car` which is removed from this fork

**File:** `alcc.py:20`  
**Status:** 🔴 CRITICAL — import error at startup

```python
from opendbc.car import structs   # opendbc submodule removed in commit 1f35f3e56
```

`structs.CarState` is used only as type annotations throughout the file. The fix is to replace the import with the vehicled equivalent or remove the annotations.

**Fix:**
```python
# Replace line 20 with:
from openpilot.selfdrive.vehicled.car import structs  # or remove annotation entirely
```
Also remove the Hyundai-specific block (lines 85–88) since it also imports from opendbc:
```python
from opendbc.car.hyundai.values import HyundaiFlags  # will NameError at runtime
```
This block is guarded by `try/except` so it silently passes, but it should be removed or gated on opendbc availability.

| ID | Severity | Issue | Fixed? |
|----|----------|-------|--------|
| B0-1 | 🔴 CRITICAL | `alcc.py` imports removed `opendbc.car` | ✅ |

---

## Priority fix order

Work these in sequence — each is a blocking prerequisite for the next category.

### P0 — Crash blockers (must fix before any testing)

| ID | File | Fix |
|----|------|-----|
| B1-1 | `controlsd.py` | Import `ALCC`; add `self.alcc = ALCC(self.CP)` in `__init__` |
| B1-2 | `controlsd.py` | Init `self.CS_prev`, `self.events`, `self.disengage_on_accelerator` |
| B1-3 | `controlsd.py` | Add `HardwareCapability`, `HARDWARE` imports |
| B2-1 | `desire_helper.py` | Fix BSD abort: `laneChangeFinishing` + flip → `off` + reset |

### P1 — Behavioral correctness (fix before tuning)

| ID | File | Fix |
|----|------|-----|
| B1-4 | `controlsd.py` | Rename `p` loop variable to avoid shadowing |
| B2-2 | `desire_helper.py` | Restore keepLeft/keepRight suppression or document removal |
| B2-3 | `desire_helper.py` | Verify radar yRel sign convention; fix wrong branch |
| B3-1 | `latcontrol_torque.py` | Fix `get_friction`: add sign, use `abs`, add interpolation |
| B4-1 | `longcontrol.py` | Fix TJA-off path to use `CP.startAccel` not `a_target` |

### P2 — Quality / dead code (fix in cleanup pass)

| ID | File | Fix |
|----|------|-----|
| B1-5 | `controlsd.py` | Add comment on driver monitoring removal |
| B2-4 | `desire_helper.py` | Remove redundant `_load_params` from `__init__` |
| B2-5 | `desire_helper.py` | Add spec reference for `LANE_CHANGE_SPEED_MIN = 11.0` |
| B3-2 | `latcontrol_torque.py` | Validate all three NN input sizes |
| B3-3 | `latcontrol_torque.py` | Rename `upper_idx`; add `min()` guard |
| B3-4 | `latcontrol_torque.py` | Remove or relocate `_roll_pitch_adjust` |
| B4-2 | `longcontrol.py` | Fix "once per frame" comment |
| B4-3 | `longcontrol.py` | Wire `tja_resume_required` to alert system or remove |
| B5-1 | `longitudinal_planner.py` | Fix inverted ACCEL comment |
| B5-2 | `longitudinal_planner.py` | Document `A_CRUISE_MAX_BP` breakpoint change |
| B5-3 | `longitudinal_planner.py` | Verify `_A_CRUISE_PROFILES` default wiring |

---

## Fix tracking checklist

```
P0 — Crash blockers
[x] B0-1  alcc.py: opendbc import removed; cereal car types used; pedal_pressed_non_gas fixed
[x] B1-1  controlsd: import AlccController + AlccStatus; init self.alcc
[x] B1-2  controlsd: init CS_prev (car.CarState.new_message()), events, disengage_on_accelerator
[x] B1-3  controlsd: import HARDWARE, HardwareCapability from system.hardware
[x] B2-1  desire_helper: fix BSD abort — off+reset instead of flip+laneChangeFinishing

P1 — Behavioral correctness
[x] B1-4  controlsd: rename 'p' loop var → field_name
[x] B2-2  desire_helper: restore keepLeft/keepRight suppression (2 lines)
[x] B2-3  desire_helper: fix radar yRel sign (positive=left, same as modelV2)
[x] B3-1  latcontrol_torque: fix get_friction — sign-preserving interp, FRICTION_THRESHOLD
[x] B4-1  longcontrol: TJA-off path now returns self.CP.startAccel

P2 — Quality
[x] B1-5  controlsd: added comment on forceDecel / driver monitoring removal
[x] B1-6  controlsd: removed spurious blank line at line 256
[x] B2-4  desire_helper: removed redundant _load_params() from __init__; clarified docstring
[x] B2-5  desire_helper: LANE_CHANGE_SPEED_MIN now references docs/eop/lane_change_assist.md
[x] B3-2  latcontrol_torque: all three NN input sizes now validated
[x] B3-3  latcontrol_torque: renamed upper_idx → t_lookahead_idx + min() guard
[x] B3-4  latcontrol_torque: _roll_pitch_adjust moved to staticmethod inside class
[x] B4-2  longcontrol: _update_tja_params comment fixed to "rate-limited to 1 Hz"
[x] B4-3  longcontrol: tja_resume_required kept; TODO comment added for wiring
[x] B4-4  longcontrol: spurious blank line removed
[x] B5-1  longitudinal_planner: ACCEL comment corrected (was inverted)
[x] B5-2  longitudinal_planner: A_CRUISE_MAX_BP change documented
[x] B5-3  longitudinal_planner: _A_CRUISE_PROFILES defaults to 'normal' — confirmed ✅

[x] B0-1  alcc.py: opendbc import removed; car.CarParams/car.CarState annotations; pedal_pressed_non_gas rewritten using CS.brakePressed/CS.gasPressed
```

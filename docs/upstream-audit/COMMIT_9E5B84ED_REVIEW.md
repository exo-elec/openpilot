# Code Review — Commit `9e5b84ed2` [SELFDRIVE]

**Commit:** `9e5b84ed26f2a4e04e7e1c1a5e3fa0d08f3e9c3a`  
**Subject:** `[SELFDRIVE] Daemons, controls, UI logic for EOP edge platform`  
**Reviewed:** 2026-05-27  
**Files changed:** 400 · **Python hunks:** 354  
**Method:** 3-angle review (line scan / removed-behavior / cross-file) + verification

---

## Bugs Found and Fixed

All 6 bugs below were fixed in the same session.

---

### Bug 1 — HIGH: `AEB.enabled` stale flag — AEB silently does nothing when enabled mid-session

| | |
|---|---|
| **File** | `selfdrive/controls/lib/aeb.py:460` |
| **Root cause** | `self.enabled` is read once in `__init__` from `EOPAEBEnabled` param and never refreshed in `update()`. `controlsd` correctly re-reads the param each second and calls `aeb.update()` when enabled, but `AEB.update()` short-circuits at `if not self.enabled` using the stale init-time value. |
| **Failure** | User enables AEB while driving. `controlsd` starts calling `aeb.update()`. AEB always returns `AEBLevel.NONE` because `self.enabled` is still `False`. Emergency braking never fires. No warning logged. |
| **Fix** | Added `self.enabled = self.params.get_bool("EOPAEBEnabled")` at the top of `update()` before the guard. |

---

### Bug 2 — HIGH: MTSC and MSLC speed targets not reset on `mapData` loss — stale limit applied indefinitely

| | |
|---|---|
| **File** | `selfdrive/controls/lib/longitudinal_planner.py:352,442` |
| **Root cause** | `self.mtsc_v_target` and `self.mslc_v_target` are only assigned inside `if sm.updated['mapData']:` but applied to `v_cruise` unconditionally every frame. If `mapd` dies or GPS is lost after the targets are set, the last computed values persist forever. |
| **Failure** | Vehicle exits a mapped curve. `mapd` loses connectivity. `mtsc_v_target` stays at, e.g., 11 m/s. Driver sets cruise to 90 km/h on a motorway; vehicle is capped at 40 km/h indefinitely. Driver must disable ACC to recover. |
| **Fix** | Reset `self.mtsc_v_target = None` and `self.mslc_v_target = None` unconditionally at the top of each update cycle, before the `if sm.updated['mapData']:` block. |

---

### Bug 3 — HIGH: RED road-edge repulsion sign inverted + `edge_side` missing from return dict

| | |
|---|---|
| **Files** | `selfdrive/controls/controlsd.py:311`, `selfdrive/controls/lib/red.py` |
| **Root cause** | Two independent issues: (1) `RED.update()` never returned `edge_side` in its dict, so `red_output.get('edge_side', 0)` always returned `0`. (2) The sign logic was inverted: for a right edge (`edge_side > 0`), the code produced `edge_sign = -1`, applying a negative curvature delta that pushes the vehicle right — into the edge. |
| **Failure** | When RED detects a right-side road edge closer than 1.0 m and `dlat_use_laneless=True`, the repulsive force steers the vehicle toward the edge instead of away. |
| **Fix** | Added `edge_side` computation and key to `RED.update()` return dict (sign based on closest edge's y-position in vehicle frame). Fixed controlsd sign: `edge_sign = 1 if edge_side > 0 else -1`. |

---

### Bug 4 — MEDIUM: BSD mid-maneuver abort doesn't reset `lane_change_completed` → re-attempt permanently blocked

| | |
|---|---|
| **File** | `selfdrive/controls/lib/desire_helper.py:314` |
| **Root cause** | When BSD aborts a `laneChangeStarting` maneuver, `lane_change_state` goes to `off` and `lane_change_direction` to `none`, but `self.lane_change_completed` (set to `self.one_lane_change` at maneuver start) is not cleared. |
| **Failure** | `EOPOneLaneChange=True`. BSD aborts mid-maneuver. Driver holds blinker and tries again. `preLaneChange` immediately exits at `if self.lane_change_completed:` (line 272). Vehicle cannot re-attempt a lane change until driver cycles the blinker fully off. |
| **Fix** | Added `self.lane_change_completed = False` to the BSD abort block. |

---

### Bug 5 — MEDIUM: EOP-added `plannerd` sockets not in `ignore_alive` — liveness failures on minimal boots

| | |
|---|---|
| **File** | `selfdrive/controls/plannerd.py:25` |
| **Root cause** | `plannerd` subscribes to `mapData`, `navInstruction`, `stereoObjects`, `surfaceStatus`, `liveLocationKalman`, `enhancedTrajectory` without adding them to `ignore_alive`. If any of those daemons is absent (hardware without stereo, no GPS lock, dev machine), SubMaster continuously flags them as not-alive. |
| **Failure** | On any boot without `mapd`, `stereod`, `surfaced`, or `globald` running, `plannerd` logs perpetual not-alive warnings. Any code path that calls `sm.all_alive()` covering all services will fail, potentially degrading or blocking plan publication. |
| **Fix** | Added all 6 EOP-only sockets to the `ignore_alive` list in the `SubMaster` constructor. |

---

### Bug 6 — MEDIUM: NNFF `err_in` built with 4 elements but model expects 18 — error blend permanently dead

| | |
|---|---|
| **File** | `selfdrive/controls/lib/latcontrol_torque.py:150` |
| **Root cause** | `_build_nn_inputs()` returns `error_in = [v_ego, setpoint-measurement, jerk_diff, 0.0]` (4 elements). The model's `input_size` is 18. The guard `if blend > 0.0 and len(err_in) == self.nn_model.input_size:` is always `False`. |
| **Failure** | The high-lateral-accel error blending (`err_torque` blend) never executes regardless of driving conditions. NN error correction at sharp turns is silently weaker than designed. |
| **Fix** | Restructured `error_in` to match the 18-element format: `[v_ego, err_delta, err_jerk, roll_adj] + [err_delta] * pf_len + common` — same structure as `ff_in`/`setpoint_in`. |

---

## Other Findings (documented, not fixed)

| Finding | Severity | Notes |
|---------|----------|-------|
| `latcontrol_torque.py` friction compensation omits `latAccelFactor` scaling | Low | Architecturally more correct than upstream; causes calibration regression on cars with `latAccelFactor > 1.0`. Intentional design choice — leave for calibration tuning. |
| `longitudinal_planner.py:124` `ramp_off` hardcodes 0.5 at 1 m/s breakpoint | Low | Sport profile gets same near-setpoint cap as eco. When `delta=0`, `max_accel=0` briefly. Sensor noise dependency — document for tuning. |
| `feedbackd` moved to `system/ui/feedback/` but not in `process_config.py` | Info | `userBookmark` / `audioFeedback` features silently disabled. Add to process_config when re-enabling. |
| `driverMonitoringState` removed with no hard fallback | Info | `selfdrived` uses `driverPoseState` from `driverd` instead. If `driverd` absent, events silently skipped. Already addressed by ignore_alive pattern. |

# Node 9 — Full migration-chain audit: dragonpilot → EDP10 → NGP10 → EOP10

## Scope and method

Unlike nodes 2-8 (which audit each branch against its own `0.10.0` base tag),
this node audits **step-to-step regressions along the actual migration
lineage** the user stated explicitly (2026-08-11): `dragonpilot → dev/EDP10 →
dev/NGP10 → dev/EOP10 → visionpilot`. Each step is a real, deliberate rewrite
(rebrand, hardware-target change, feature trim) — not expected to be
line-identical — so the question per step was narrower than nodes 2-8's
"is every line correct": *did this rewrite silently break something the
previous step already had working?*

Calibration case that set the pattern for the whole node: `selfdrive/ui/qt/widgets/controls.h`'s
`ParamSpinBoxControl`/`ParamDoubleSpinBoxControl` were missing a
`setSpecialValueText()` call (an "Off" placeholder never rendered). NGP10,
which copied the same classes from EOP10, had independently fixed this but
the fix was never ported back. Every other finding below follows the same
shape: shared-lineage code where a rewrite step dropped a line, flipped an
operator, or misused a type that an earlier or sibling step had correct.

Method per step:
- `dev/EOP10` vs `dev/NGP10`: direct `git diff` (same repo, same lineage).
- `dev/EDP10` vs `dev/NGP10`: direct `git diff` (same repo).
- `dragonpilot` (separate repo, `~/pilot/dragonpilot`, non-commercial license
  — read-only reference, never copied verbatim) vs `dev/EDP10`: path-matched
  file comparison, since git history isn't shared across the two repos.
- `dev/EOP10` vs `~/pilot/visionpilot` (branch `EVP09`): checked whether any
  shared-lineage file comparison was even possible — it wasn't (see below).

Full same-path file diff was run first (~1500+ common paths per step-pair,
reduced to the actual-differing subset — 53 for EDP10↔NGP10, 176 for
dragonpilot↔EDP10 — after excluding binary assets/translations), then
triaged by three parallel background agents per step-pair plus direct manual
verification of every finding before any fix was applied. Nothing below was
applied on agent say-so alone — every finding was independently reproduced
(git diff read directly, or a runtime check) before editing.

## `dev/EOP10 → ~/pilot/visionpilot`: no shared source lineage, nothing to audit

`visionpilot` (branch `EVP09`) has no `selfdrive/` tree at all — its layout
(`_audit_autoware_compliance.py`, `src/`, ROS-shaped dirs) indicates an
Autoware-based rewrite, not an openpilot-lineage codebase. "EOP10 and
visionpilot share the exopilot HAL repo" (per the user, 2026-08-11) is true
only at the BSP/hardware layer (`~/pilot/exopilot`), not the driving-policy
code layer. No file-level comparison is possible or meaningful here.

## Findings — all fixed, none left open

### 🔴 `selfdrive/controls/lib/alcc.py` — ALCC state machine's enable/disable detection was dead code

Highest-severity finding. Not a cross-branch port (ALCC/MADS-style state
machine is EOP10-original, no dragonpilot/EDP10/NGP10 equivalent) — found by
directly auditing the flagged `controlsd.py` ALCC wiring after the EDP10↔NGP10
agent flagged EDP10's simpler `alka_active` gate looked stronger.

`EventName.*` values are plain Python `int` (verified: `EventName.doorOpen ==
3`). `update_state_machine()` compared them against **string literals**:

```python
has_user_disable = 'userDisable' in events.names      # always False
has_imm_disable  = 'immediateDisable' in events.names  # always False
has_soft_disable = 'softDisable' in events.names       # always False
has_enable       = 'lkasEnable' in events.names or 'buttonEnable' in events.names  # always False
```

Worse: `'userDisable'`/`'immediateDisable'`/`'softDisable'` aren't even valid
`EventName` members — they're `ET` (event-*type*) constants from
`selfdrive/selfdrived/events.py`, meant for `events.contains(ET.X)`, a
different lookup API entirely. Same bug in `_has_event()`/`_remove_event()`
and every caller (`PAUSE_ALLOWED_EVENTS`, the door/seatbelt/gear pause-vs-
disable conversion, the pre-enable event cleanup).

**Effect:** `has_enable` always `False` → `update_state_machine()` could
never transition ALCC out of `AlccState.disabled` → `alcc_status.active`
always `False` → `CC.latActive`'s `or alcc_status.active` term never
contributed. ALCC was fully scaffolded (panel toggle, params, capnp message,
docstring) but had never actually engaged.

**Fix:** `PAUSE_ALLOWED_EVENTS` and every event-name literal → `EventName.X`
enum members; `has_user_disable`/`has_imm_disable`/`has_soft_disable` →
`events.contains(ET.X)`, matching the pattern `selfdrived/state.py` already
uses correctly; `has_enable` → real `EventName.lkasEnable`/`buttonEnable`
ints. Verified live: `EventName.doorOpen in events.names` now `True` where
the string check was always `False`.

### 🟠 `selfdrive/selfdrived/state.py` + `selfdrive/selfdrived/selfdrived.py` — WARNING alerts suppressed during ALCC-only engagement

Found by the EDP10↔NGP10 background agent, verified independently.

EDP10's `StateMachine.__init__(self, alka=False)` stored a static per-drive
"is ALKA enabled" flag and appended `ET.WARNING` alerts (e.g.
`belowSteerSpeed`) whenever `active or self.alka` — so WARNING-tier alerts
stayed visible during lateral-only (ALKA) engagement, not just full
engagement. NGP10's comma-3 port dropped the parameter and wiring entirely
(`StateMachine()`, `if active:` only) while carrying the underlying feature
forward everywhere else under a new name — a silent drop during a rename,
not an intentional simplification. EOP10 inherited the gap unchanged from
NGP10 (its own `alcc.py` didn't exist yet as an EDP10-lineage concept, but
the same "lateral engaged without full active state" scenario applies to
EOP10's ALCC).

**Fix:** restored the exemption, renamed to match EOP10's ALCC feature —
`StateMachine(alcc_enabled=False)`, `self.alcc_enabled` read once via
`EOPLatALCC` param (same pattern as neighboring `is_metric`/`is_ldw_enabled`
reads in `selfdrived.py`, no new capnp subscription needed).

### 🟠 `selfdrive/ui/qt/onroad/hud.cc` — cruise "MAX" speed always showed "–"

Found by the NGP10↔EOP10 background agent (UI/C++ batch), verified by direct
diff against both `dev/NGP10` and `dev/EDP10` (EDP10 has the correct version
too — this was an EOP10-only regression, introduced in the large rebrand
commit `d1ae8ef80`, not inherited from the chain).

```cpp
// before (EOP10 only)
set_speed = controls_state.getVCruiseDEPRECATED();
// after (restored, matches EDP10 and NGP10)
set_speed = car_state.getVCruiseCluster() == 0.0 ? controls_state.getVCruiseDEPRECATED() : car_state.getVCruiseCluster();
```

`controlsState.vCruiseDEPRECATED` is never populated anywhere in EOP10's
`controlsd.py`; `carState.vCruiseCluster` is (`system/socketd/vehicle/car/card.py:204`).
`set_speed` was therefore always `0.0` → HUD always rendered the placeholder
dash regardless of actual cruise state. Visible every drive.

### 🟠 `selfdrive/ui/ui.h` + `selfdrive/ui/ui.cc` + `selfdrive/ui/qt/onroad/onroad_home.cc` — no ALCC status indicator in the production UI

Found via the EDP10↔NGP10 agent's low-confidence "legacy Qt UI lost ALKA
indication" flag, re-investigated after confirming EOP10's actual running UI
process is the compiled Qt `./ui` binary (`system/manager/process_config.py`
`NativeProcess("ui", ...)`), not the Python raylib stack — so this finding
was more consequential than the agent's low-confidence rating suggested,
especially given the `alcc.py` fix above means ALCC can now actually engage.

EDP10 had `STATUS_ALKA` in `bg_colors[]`, `scene.alka_active` populated from
a (dragonpilot-lineage) `dpControlsState.alkaActive` field, and
`onroad_home.cc` picking that color whenever `alka_active && status ==
STATUS_DISENGAGED`. Dropped entirely during the NGP10/EOP10 rewrite, never
renamed.

**Fix:** added `STATUS_ALCC` to `UIStatus`/`bg_colors[]`, `scene.alcc_active`
populated from EOP10's own `alccState.active` (already published by
`controlsd.py`, just not subscribed by the UI), same
`alcc_active && status == STATUS_DISENGAGED` rule in `onroad_home.cc`.

### 🟡 `selfdrive/gridd/lazy_bev.py` — int-truncation instead of floor corrupts lateral grid bins near centerline (3 sites)

Found by the NGP10↔EOP10 background agent (daemon/common batch), verified
numerically.

```python
# before
col = np.clip((x / self.resolution_m).astype(np.int32) + self.half_w, 0, self.grid_w - 1)
# after
col = np.clip(np.floor(x / self.resolution_m).astype(np.int32) + self.half_w, 0, self.grid_w - 1)
```

`x` (lateral position) can be negative; `.astype(np.int32)` truncates toward
zero, not toward `-inf`. For `x` in `(-resolution_m, 0)` this lands in the
same column as `x` in `[0, resolution_m)`, silently narrowing the column
straddling the vehicle centerline by up to a full cell-width. This grid feeds
cut-in detection and the `SAFE_WIDTH_M` safety corridor. NGP10's
`NGPLazyBEV.update()` uses `math.floor()` correctly. Verified: before the
fix, lateral samples -0.3m to +0.3m all collapsed into column 10; after, the
negative-side sample correctly lands in column 9. Same fix applied at all
three call sites (`update_multi_camera`, `update`, `get_cell_probability`).

### 🟡 `selfdrive/locationd/helpers.py` — copy/paste bug in `PoseCalibrator.build_calibrated_pose()`

Found by the dragonpilot↔EDP10 background agent (core-control batch),
verified against dragonpilot directly and confirmed present in current
EOP10.

```python
# before
velocity_calib = self._transform_calib_from_device(pose.angular_velocity)  # duplicates the line above
# after
velocity_calib = self._transform_calib_from_device(pose.velocity)
```

`Pose.velocity` was silently populated with transformed angular-velocity
data instead of velocity data. No current caller reads
`calibrated_pose.velocity` (all consumers use `.orientation`/
`.angular_velocity`/`.acceleration`), so this was dormant, not
actively wrong-in-production — but it's a live correctness defect in a
helper shared by torqued/paramsd/lagd/controlsd/selfdrived, and any future
consumer would silently get wrong data.

### 🟡 `selfdrive/locationd/torqued.py` — dropped `livePose` validity gate

Found by the dragonpilot↔EDP10 background agent, verified against EOP10's
`cereal/log.capnp` schema (`LivePose.inputsOK`/`.sensorsOK`/`.posenetOK`/
`.angularVelocityDevice.valid`/`.orientationNED.valid` all present).

```python
# before
elif which == "livePose":
    if len(self.raw_points['steer_torque']) == self.hist_len:
# after
elif which == "livePose":
    is_valid = msg.angularVelocityDevice.valid and msg.orientationNED.valid and msg.inputsOK and msg.sensorsOK and msg.posenetOK
    if is_valid and len(self.raw_points['steer_torque']) == self.hist_len:
```

Without this gate, torqued could feed livePose samples into the live
steering-torque calibration estimator during degraded input/sensor health or
a detected posenet std-spike — the outer `sm.all_checks()` loop guard only
reflects `msg.valid` (filter-initialized), not these independent health
flags.

### 🟡 `selfdrive/controls/lib/desire_helper.py` — `lane_change_direction` set one cycle late on `off → preLaneChange`

Found by the dragonpilot↔EDP10 background agent, verified by reading the
state-transition code directly.

```python
# before
if self.lane_change_state == LaneChangeState.off and one_blinker and not self.prev_one_blinker and not below_lane_change_speed:
    self.lane_change_state = LaneChangeState.preLaneChange
    self.lane_change_ll_prob = 1.0
# after
    self.lane_change_state = LaneChangeState.preLaneChange
    self.lane_change_direction = LaneChangeDirection.left if carstate.leftBlinker else LaneChangeDirection.right
    self.lane_change_ll_prob = 1.0
```

Direction was previously only set inside the separate `elif ...
preLaneChange:` branch, which — being an `elif` — does not run in the same
cycle the state transitions into `preLaneChange`. For one frame,
`self.lane_change_direction` held its stale prior value (`.none`), so
`self.desire = DESIRES[LaneChangeDirection.none][...]` fed the wrong desire
into modeld for that frame.

### 🟡 `selfdrive/controls/lib/latcontrol_torque.py` — `get_friction()` was a hand-rolled approximation of a function EOP10 already has

Self-directed finding (not from an agent) — the local reimplementation's
comment claimed to reproduce `opendbc.car.lateral.get_friction` because that
module is "unavailable" in this fork's opendbc pin. That premise was false:
EOP10's own vendored `opendbc_repo` submodule has the real function at
`opendbc.car`/`opendbc.car.interfaces` (a different module path, not a
different opendbc, since this fork pins an older version than upstream's
`opendbc.car.lateral` split). dragonpilot, EDP10, and NGP10 all import the
real function directly; only EOP10 had a local reimplementation, and it had
diverged from the real curve shape (continuous ramp from the deadzone edge
vs. real upstream's deadzone-then-fixed-window interpolation).

**Fix, following the user's explicit correction to align on the *previous*
step rather than invent a new fixed local formula:** deleted the local
`get_friction()` entirely, imported the real one:

```python
from opendbc.car import get_friction
from opendbc.car.interfaces import FRICTION_THRESHOLD
...
ff += get_friction(desired_lateral_accel - actual_lateral_accel, lateral_accel_deadzone, FRICTION_THRESHOLD,
                   self.torque_params, friction_compensation=True)
```

Net diff: -10/+4 lines — smaller than a corrected local reimplementation,
and now genuinely the real function instead of an approximation of it.

### 🟢 `selfdrive/ui/onroad/alert_renderer.py` — dropped stale-alert guard (raylib UI, not currently a registered process)

Found by the dragonpilot↔EDP10 background agent, verified against
dragonpilot directly.

```python
# before
if not sm.updated['selfdriveState']:
    recv_frame = sm.recv_frame['selfdriveState']
    ...
# (no staleness check before the final return)
# after
recv_frame = sm.recv_frame['selfdriveState']   # now computed unconditionally
if not sm.updated['selfdriveState']:
    ...
...
if recv_frame < ui_state.started_frame:
    return None
```

Without this, the first frames of a new onroad session (before
`selfdriveState` updates at least once) could render leftover alert
text/status from the *previous* onroad session.

### 🟢 `selfdrive/ui/onroad/cameraview.py` + `selfdrive/ui/ui_state.py` — stale camera frame on disconnect, no reconnect-on-resume (raylib UI)

Found by the dragonpilot↔EDP10 background agent, verified against
dragonpilot directly.

1. One-frame stale-buffer gap: added `elif not self.client.is_connected():
   self.frame = None` after a failed non-blocking `recv()`, matching
   dragonpilot — previously a mid-frame disconnect could render one stale
   frame instead of the placeholder.
2. Missing offroad→onroad reconnect: dragonpilot force-recreates the
   `VisionIpcClient` on every offroad→onroad transition (via
   `ui_state.add_offroad_transition_callback`) to flush stale buffered
   frames from a previous session; EOP10 had no such callback mechanism at
   all. Added the minimal version — `_offroad_transition_callbacks` list +
   `add_offroad_transition_callback()` on `UIState`, fired from the existing
   onroad/offroad transition block (no new state-tracking), and
   `CameraView` registers a callback that recreates its `VisionIpcClient`.

**Caveat, worth flagging explicitly:** these two raylib-UI files are not
currently wired to a registered process in
`system/manager/process_config.py` (only the compiled Qt `./ui` binary is —
see the `ui.h`/`ui.cc`/`onroad_home.cc` finding above, which *is* the
production path). `selfdrive/ui/watch3.py` does import `CameraView` from
this file directly, so it's not fully dead, but its practical impact on
EOP10 today is much lower than the Qt-side findings. Fixed for correctness
and because it was cheap/safe to do, not because it's high-priority.

### 🟡 `selfdrive/controls/radar3d.py` — dropped asymmetric lead-probability hysteresis (revised: this one *is* portable)

Originally found by the dragonpilot↔EDP10 background agent as a `radard.py`
finding, initially ruled out here as "EOP10 has no `radard.py`, replaced by a
different `radar4d`/`radar3d` architecture, not a minimal port." That
verdict was wrong — re-investigated per the user's correction (2026-08-11)
that a hardware-platform change via `../exopilot`'s HAL shouldn't be treated
as blocking alignment of the *policy* layer above it. `radar3d.py` (which
handles BrownPanda/Tesla ARS4-B lead tracking, not the corner-radar
`radar4d.py`) turned out to be the same `RadarD`/`get_lead()` lineage as
`radard.py` — same class names, same structure, same `Track`/`KalmanParams`
helpers — just fed from a different `RadarData` source. The hysteresis
logic itself has zero hardware dependency; only its input (`leadsV3[i].prob`)
does, and that input already flows into `radar3d.py` identically to how it
flows into `radard.py`.

```python
# RadarD.__init__: added
self.lead_prob_filters = [FirstOrderFilter(0.0, 0.2, DT_MDL) for _ in range(2)]

# RadarD.update(): before
self.radar_state.leadOne = get_lead(self.v_ego, self.ready, self.tracks, leads_v3[0], model_v_ego, low_speed_override=True)
self.radar_state.leadTwo = get_lead(self.v_ego, self.ready, self.tracks, leads_v3[1], model_v_ego, low_speed_override=False)
# after
for i in range(2):
  lead_prob = leads_v3[i].prob
  if lead_prob > self.lead_prob_filters[i].x:
    self.lead_prob_filters[i].x = lead_prob
  else:
    self.lead_prob_filters[i].update(lead_prob)
self.radar_state.leadOne = get_lead(self.v_ego, self.ready, self.tracks, leads_v3[0], model_v_ego, self.lead_prob_filters[0].x, low_speed_override=True)
self.radar_state.leadTwo = get_lead(self.v_ego, self.ready, self.tracks, leads_v3[1], model_v_ego, self.lead_prob_filters[1].x, low_speed_override=False)
```

`get_lead()` and `get_RadarState_from_vision()` signatures changed to take
`lead_prob: float` explicitly instead of reading `lead_msg.prob` internally
— same signature change dragonpilot made. Jumps up instantly on rising
confidence, decays slowly (`FirstOrderFilter` tau=0.2s) on falling
confidence, so a lead seen with high confidence isn't dropped the instant
`prob` dips near the 0.5 threshold — reduces `leadOne`/`leadTwo` flicker.
122/122 radar-related tests pass after the change; no other call site in the
tree references the changed function signatures.

**Lesson for future nodes:** "different hardware platform" is not the same
test as "different policy logic" — check the actual function/class shape
before ruling a finding out as architecturally non-portable.

### 🟢 `selfdrive/ui/qt/widgets/controls.h` — the finding that set the pattern for this whole node

`ParamSpinBoxControl`/`ParamDoubleSpinBoxControl` were missing
`spin->setSpecialValueText(placeholder)` — an "Off" placeholder passed to
three EOP10 panel toggles (LCA speed, HUD-hide speed, auto-shutdown timer)
never rendered; the spinbox showed the raw number at minimum instead.
NGP10, which copied these classes from EOP10's `controls.h`, had
independently added the missing call; ported back verbatim (now
byte-identical between the two branches' class bodies).

## Findings considered and explicitly not ported

- **`selfdrive/ui/onroad/driver_state.py` sign flip** (dragonpilot↔EDP10
  finding): file doesn't exist in EOP10 — replaced by EOP10's own rear/side
  UVC camera switcher. Not applicable.
- **`selfdrive/selfdrived/selfdrived.py`'s `laneChangeBlocked` losing the
  road-edge-detection factor** (EDP10↔NGP10 finding, low-medium confidence):
  the underlying safety behavior is preserved (edge detection still blocks
  the lane change via `desire_helper.py`'s `blindspot_detected`/
  `low_lane_confidence`, just no longer duplicated into the alert-text
  condition) — UX-only, likely an intentional simplification once edge
  detection moved fully into `modeld.py`. Not applied.
- **ESP32_RADAR BLE tracked-objects contract** (`~/radar/ESP32_RADAR` vs
  `system/bluetoothd/ble_central.py`): investigated per user question, not a
  "chain" step but a real cross-repo hardware/software contract. Verified
  byte-for-byte: `wire_format.h`'s `ble_radar_datagram_header_t`/
  `ble_radar_object_wire_t` structs match `ble_central.py`'s
  `HEADER_STRUCT '<BBHI'` / `OBJECT_STRUCT '<IhhhhBBH'` exactly (field
  order, cm/×100/×10 scaling, `existence_prob` 0-100 convention). No bug
  found — this integration is correctly built on both sides, both
  param-gated off by default pending real-hardware validation.

## Verification

All fixes syntax/compile-checked individually at the time. Full regression
pass after all fixes applied:

```
pytest selfdrive/ui/tests/ selfdrive/controls/ selfdrive/locationd/ selfdrive/selfdrived/ selfdrive/gridd/
→ 345 passed, 7 failed, 11 skipped
```

All 7 failures reproduced identically on a clean `git stash` (pre-existing,
unrelated to this node): `test_torqued.py::test_cal_percent` (capnp schema
missing `CarParams.brand`, in `torqued.py:75`'s `__init__`, not the line
this node touched), `test_nslc.py::test_get_nslc_speed_helper`,
`test_depth_validation.py::TestDepthWithCalibrationFile::test_with_real_calibration`
(missing `/data/calibration/stereo_intrinsics.npz` dev-PC fixture file),
`test_raylib_ui.py::test_raylib_ui`, and 3 `test_translations.py` cases
(Nederlands/Polski completeness).

`radar3d.py`'s hysteresis port (added after the initial full-suite pass) was
verified separately: 122/122 radar-related tests pass. Zero regressions
across the full 17-file changeset.

## Status

17 files changed, all fixes applied, all verified, **uncommitted** in the
working tree as of this writing — commit is a separate explicit decision,
not yet made.

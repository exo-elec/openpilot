# Node 9 backport — migration-chain regression fixes from dev/EOP10

**Source of truth:** `dev/EOP10`'s `docs/upstream-audit/NODE_09_migration_chain_audit.md`
documents the full audit (method, all findings, what was and wasn't ported,
verification). This file only records what changed on `dev/EDP10` specifically
and why — read the EOP10 doc for the complete investigation.

## Background

EOP10 audited the actual migration lineage `dragonpilot -> dev/EDP10 ->
dev/NGP10 -> dev/EOP10 -> visionpilot` for regressions: cases where a later
rewrite step silently broke something an earlier step had working. Several
findings originated at the EDP10 step itself (found by diffing dragonpilot
against EDP10) and were confirmed still present in EDP10's own current
source before being ported here, on `2026-08-12`.

## What was fixed on this branch

- `selfdrive/controls/lib/desire_helper.py`: `lane_change_direction` was set
  one cycle late on the `off -> preLaneChange` transition (only in the
  sibling `elif` branch, which doesn't run the same cycle the state
  transitions).
- `selfdrive/controls/radard.py`: dropped the asymmetric lead-probability
  hysteresis (jump up instantly on rising confidence, decay slowly on
  falling confidence) that reduces `leadOne`/`leadTwo` flicker near the 0.5
  threshold. `get_lead()`/`get_RadarState_from_vision()` now take an
  explicit `lead_prob` parameter (a filtered value) instead of reading
  `lead_msg.prob` directly.
- `selfdrive/locationd/helpers.py`: `PoseCalibrator.build_calibrated_pose()`
  computed `velocity_calib` from `pose.angular_velocity` instead of
  `pose.velocity` — a copy/paste bug from the line above.
- `selfdrive/locationd/torqued.py`: dropped the `livePose` validity gate
  (`angularVelocityDevice.valid`/`orientationNED.valid`/`inputsOK`/
  `sensorsOK`/`posenetOK`) before feeding samples into the live
  steering-torque calibration estimator.
- `selfdrive/ui/onroad/alert_renderer.py`: dropped the "don't render a stale
  alert from a previous onroad session" guard.
- `selfdrive/ui/onroad/cameraview.py` + `selfdrive/ui/ui_state.py`: added a
  stale-camera-frame guard for a mid-frame VisionIpcClient disconnect, and
  the offroad->onroad reconnect callback (`add_offroad_transition_callback`)
  that flushes stale buffered frames.

## What was explicitly NOT touched

`selfdrive/ui/qt/widgets/controls.h`, `selfdrive/ui/qt/onroad/hud.cc`, and
`selfdrive/controls/lib/latcontrol_torque.py`'s `get_friction()` were the
other EOP10 findings — all three were already correct on `dev/EDP10` (in
fact, `controls.h`'s `setSpecialValueText()` call and `hud.cc`'s
`vCruiseCluster` fallback were where the EOP10 fix was originally sourced
from). Not modified.

`selfdrive/selfdrived/state.py`'s ALCC/ALKA warning-alert visibility gate
was also not touched here — EDP10 already has it correct (`alka` parameter,
`if active or self.alka:`). This is in fact the branch NGP10's version of
the fix was restored *from* — see `dev/NGP10`'s own backport note.

`selfdrive/controls/lib/alcc.py` (EOP10's `EventName`/`ET` type-confusion
bug in its MADS-style ALCC state machine) has no equivalent here — that
module is EOP10-original code with no EDP10 counterpart (EDP10's ALKA gate
is the simple inline `controlsd.py` boolean this file's own git history
shows, not a separate state-machine module).

## Verification

All 7 changed files syntax-checked clean (`ast.parse`). Full `pytest`
verification not available in this worktree (`/tmp/openpilot-dev-EDP10`,
submodules/capnp codegen not set up for a full build) — same limitation
documented in prior cross-branch sessions on this branch
(`nagaspilot`/BRSC porting work, see `dev/EOP10`'s project memory).

# Migration-chain regression fixes backported from dev/EOP10 (2026-08-12)

**Source of truth:** `dev/EOP10`'s `docs/upstream-audit/NODE_09_migration_chain_audit.md`
documents the full audit (method, all findings, what was and wasn't ported,
verification). This file records what changed on `dev/NGP10` specifically —
read the EOP10 doc for the complete investigation, and `dev/EDP10`'s
`docs/upstream-audit/NODE_09_migration_chain_backport.md` for the EDP10-side
backport (several of these bugs originated at that step and were inherited
here unchanged).

## Background

EOP10 audited the actual migration lineage `dragonpilot -> dev/EDP10 ->
dev/NGP10 -> dev/EOP10 -> visionpilot` for regressions. Confirmed each
finding still present in NGP10's own current source before applying —
NGP10 forked from EDP10, so most of these were simply never fixed along
the way, not reintroduced by NGP10's own comma-3 port work.

## What was fixed on this branch

Six ported unchanged in spirit from the EDP10 backport (same bug, same
fix shape, present here too):

- `selfdrive/controls/lib/desire_helper.py`: `lane_change_direction` set one
  cycle late on `off -> preLaneChange`.
- `selfdrive/controls/radard.py`: dropped asymmetric lead-probability
  hysteresis. `get_lead()`/`get_RadarState_from_vision()` now take an
  explicit `lead_prob` parameter instead of reading `lead_msg.prob`
  directly.
- `selfdrive/locationd/helpers.py`: `velocity_calib` computed from
  `pose.angular_velocity` instead of `pose.velocity` (copy/paste bug).
- `selfdrive/locationd/torqued.py`: dropped the `livePose` validity gate.
- `selfdrive/ui/onroad/alert_renderer.py`: dropped stale-alert guard.
- `selfdrive/ui/onroad/cameraview.py` + `selfdrive/ui/ui_state.py`: added
  the stale-camera-frame guard and offroad->onroad reconnect callback.
  Note: `ui_state.py` (the raylib UI stack) already had its own
  `ngpAlccActive` status handling independently written — only the
  unrelated offroad-transition callback mechanism was added here, nothing
  about the existing ALCC logic was touched.

One fix EDP10 did **not** need (it already had it correct, as `alka`) but
NGP10 dropped during its own comma-3 port — restored using NGP10's own
naming:

- `selfdrive/selfdrived/state.py` + `selfdrive/selfdrived/selfdrived.py`:
  WARNING-tier alerts (e.g. `belowSteerSpeed`) were suppressed whenever the
  car was steering via ALCC-only engagement but `SelfdriveState` wasn't
  "active". Restored `StateMachine(alcc_enabled=...)`, wired from NGP10's
  own `ngp_lat_alcc` param — same static per-drive-enablement-flag shape as
  EDP10's original `alka`, not a live per-frame active flag (matches the
  existing `is_metric`/`is_ldw_enabled` read-once-at-init pattern already in
  `selfdrived.py`).

One finding that needed NGP10-specific adaptation rather than a straight
port:

- `selfdrive/ui/ui.h` + `selfdrive/ui/ui.cc`: added a `STATUS_ALCC`
  indicator to NGP10's **production** Qt UI (the compiled `./ui` binary
  registered in `system/manager/process_config.py` — same as EOP10). The
  raylib UI stack's `ui_state.py` already reads `controlsState.ngpAlccActive`
  correctly, but that's not the UI that actually runs; the Qt side had
  nothing. Wired directly off the already-subscribed `controlsState`
  message (`ngpAlccActive`, a `ControlsState` field on this branch — unlike
  EOP10, which has a dedicated `alccState` message and needed a new
  subscription).

## What was explicitly NOT touched

`selfdrive/ui/qt/widgets/controls.h`, `selfdrive/ui/qt/onroad/hud.cc`, and
`latcontrol_torque.py`'s `get_friction()` were already correct on
`dev/NGP10` (same as EDP10) — not modified.

`selfdrive/controls/lib/alcc.py` (EOP10's MADS-style state-machine
`EventName`/`ET` type-confusion bug) has no equivalent — NGP10's ALCC gate
is a simple inline boolean in `controlsd.py` (`self.alcc_active = ...`,
`ngp_lat_alcc` param), not a separate state-machine module.

## Verification

All 9 changed Python files syntax-checked clean (`ast.parse`). For the Qt
UI change, the capnp field name (`ngpAlccActive`) was confirmed directly
against `cereal/log.capnp`, but a full C++ compile could not be completed:
generating `cereal/gen/cpp/*.capnp.h` in this freshly recreated
`/tmp/openpilot-dev-NGP10` worktree hit a pre-existing, unrelated capnp
schema error (`Car.RadarData.ErrorDEPRECATED` missing from
`opendbc_repo`'s `car.capnp` at this branch's pinned submodule commit) —
already documented in project memory from prior cross-branch sessions in
this same worktree (BRSC porting work). Not something this change
introduced or could fix without touching the opendbc submodule pin, which
is out of scope here.

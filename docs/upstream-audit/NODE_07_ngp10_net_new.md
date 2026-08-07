# Node 7 — NGP10 net-new: `ngp_suite` controllers + gridd/adaptd wiring

Full review of `ngp_dlon.py` (largest new file, 457 lines) plus the
integration points (`controlsd.py`, `desire_helper.py`, `plannerd.py`,
`ngp_suite.py`). Did not do a line-by-line pass of every one of the 13
controller files given the size of this branch's surface — prioritized the
largest/most complex file and the cross-cutting wiring points where a bug
would have the widest blast radius.

## `ngp_dlon.py` — 🟢 resolved on both branches: `speed_limit` trigger implemented (2026-08-08)

**Timeline of this finding:**

1. **First pass:** found `_trigger_enabled['speed_limit']` read from its
   param every second but never consulted anywhere — dead toggle.
2. **Root-caused:** checked EOP10's original `dlon.py` (the file NGP10's own
   docstring says this is "the comma 3 migration of") — identical dead-toggle
   bug there too. Not an NGP10 porting omission; NGP10 faithfully copied a
   pre-existing EOP10 bug.
3. **Initially deleted** the dead scaffolding on both branches (clean
   deletion — no test or UI panel referenced either param key).
4. **User asked what "speed_limit" was supposed to mean** — 36 m/s or a
   real mapped speed limit? Checked: this codebase already has real
   map/nav speed-limit infrastructure (`selfdrive/controls/lib/nslc.py`,
   reading `mapData.speedLimit` km/h → `nav_instruction.speedLimit` m/s
   fallback) that `dlon.py` never touched. The trigger was a real,
   never-implemented feature, not meaningless scaffolding.
5. **Implemented it on both branches**, mirroring the existing `navigation`
   trigger's shape (same "read `sm`, check `sm.valid`, return bool" pattern
   as `detect_nav_trigger`) and NSLC's exact source-preference/unit logic:

```python
def detect_speed_limit_trigger(self, sm, v_ego) -> bool:
    limit_ms = None
    if 'mapData' in sm.valid and sm.valid['mapData']:
      map_limit = sm['mapData'].speedLimit  # km/h
      if map_limit > 0:
        limit_ms = map_limit * CV.KPH_TO_MS
    if limit_ms is None and 'navInstruction' in sm.valid and sm.valid['navInstruction']:
      nav_limit = sm['navInstruction'].speedLimit  # m/s
      if nav_limit > 0:
        limit_ms = nav_limit
    if limit_ms is None:
      return False
    return limit_ms < v_ego - self.SPEED_LIMIT_TRIGGER_MARGIN_MS  # 2 m/s margin, avoid boundary chatter
```

Wired into the same places every other trigger is wired: the EMA filter in
`update()`, the `_trigger_enabled['speed_limit']`-gated check in
`_evaluate_auto_mode()` (medium priority, alongside `low_speed`), the debug
`triggers`/`confidences` dicts, and `_calculate_confidence()`.

**EOP10:** `dlon.py` + `EOPDLONSpeedLimitEnabled` restored to
`params_keys.h`. `plannerd.py` already subscribed `mapData`+`navInstruction`
correctly (both in `ignore_alive`) — no wiring changes needed there.

**NGP10** (applied via the existing local worktree at
`/tmp/openpilot-dev-NGP10`, left uncommitted): `ngp_dlon.py` +
`ngp_lon_dlon_speed_limit` restored to `params_keys.h` +
`NGP10_FEATURE_MATRIX.md` updated to describe the real trigger.
**Also fixed `plannerd.py`**, which was missing a `mapData` subscription
entirely (map-based limits could never have worked there even after
implementing the trigger) and had `navInstruction` outside `ignore_alive`
(inconsistent with EOP10's treatment of the same two optional/intermittent
services) — added `mapData` to the subscription list and put both into
`ignore_alive`.

**Verified:** both files parse; `detect_speed_limit_trigger`'s logic
exercised directly in isolation on both branches (map-preferred, nav
fallback, zero-value fallback, and the 2 m/s margin all behave correctly)
since the full pytest harness hits a pre-existing, unrelated
`params_pyx.so`/capnp build issue on both worktrees (confirmed pre-existing
by reproducing it with all changes stashed).

<details><summary>Original dead-toggle finding (superseded by implementation above)</summary>

`_trigger_enabled['speed_limit']` is initialized to `True`, and
`update_params()` faithfully re-reads it from the real backing param
`ngp_lon_dlon_speed_limit` every second — but **it is never read anywhere
else in the class.** Grepped the full file: the only 2 non-init references
are the initialization and the `update_params()` read. `_evaluate_auto_mode()`
checks every *other* trigger's toggle (`curves`, `slow_lead`, `low_speed`,
`stop_prediction`, `navigation`, `signal`) before acting on it, but there is
no `speed_limit`-gated trigger anywhere in the file at all — no
`detect_speed_limit_*` method exists, and nothing in `_evaluate_auto_mode`
or `update()` references speed limits.

This means `ngp_lon_dlon_speed_limit` is a **fully inert param**: setting it
either way has zero effect on behavior. `NGP10_FEATURE_MATRIX.md` documents
this key as one of "DLON's eight individual per-trigger sub-toggles"
(deliberately not UI-exposed, matching EOP10's `eop_panel.cc` precedent) —
that doc is describing a real, functioning trigger the same way it
describes `_stop_prediction`/`_navigation`/etc., so the dead code isn't a
documented "not implemented yet" gap, it reads as an oversight (the trigger
implementation was likely dropped or never written, while its toggle
scaffolding and doc entry were left behind).

Initial resolution (superseded above): deleted rather than implemented,
since inventing new trigger logic mid-audit without knowing what it was
meant to detect would have been guessing. The user's follow-up question
("36 m/s or map-based?") supplied the missing intent — real map/nav speed
limit data — which turned this from "invent a feature" into "port a small,
well-specified trigger using infrastructure (`nslc.py`) that already exists
in this codebase," so it was implemented instead.

</details>

## `stop_prediction` toggle doesn't gate `force_stop` — 🟠 sharpened finding, verified by trace (2026-08-08)

**Original framing (below) was too soft — traced the actual data flow instead
of just noting the toggle asymmetry.** Confirmed on both EOP10's `dlon.py`
and NGP10's `ngp_dlon.py` (identical except whitespace):

`detect_traffic_control(model_v2, radar_state, v_ego)` calls
`self.detect_stop_prediction(model_v2)` **directly** — not through
`self._trigger_enabled['stop_prediction']` — and its result feeds
`self._has_traffic_control` (via `traffic_filter`). Separately, `update()`
also computes `should_stop = self.detect_stop_prediction(model_v2)`
**directly** (again bypassing the toggle) and stores it as `self._should_stop`
(via `stop_filter`). `force_stop_recommended` — the condition that, after a
1.0s continuous-hold requirement, sets `force_stop = True` and can bring the
car to a stop — is:

```python
force_stop_recommended = (
    self._has_traffic_control and   # ← reaches detect_stop_prediction() directly
    self._should_stop and           # ← ALSO reaches detect_stop_prediction() directly
    not has_lead
)
```

Neither factor is gated by `self._trigger_enabled['stop_prediction']`. That
toggle is checked in exactly **one** place in the whole file:
`_evaluate_auto_mode()`'s own `if self._trigger_enabled['stop_prediction']
and self._should_stop: return True` line, which only controls whether
stop-prediction can switch the planner into E2E mode — a *different*,
weaker effect than force-stopping the car.

**Concrete behavioral claim:** a user (or engineer) who sets
`EOPDLONStopPredictionEnabled=0` (or `ngp_lon_dlon_stop_prediction=0`)
expecting to disable "the car sometimes force-stops itself when it thinks
it sees a stop sign/light" will find **it still happens** — the same
`model.action.shouldStop` signal reaches `force_stop` through two other,
untoggled paths. The only thing that actually gates `force_stop` as a
feature is the separate master switch `EOPDLONForceStopsEnabled`
(`force_stops_enabled`) — which does work correctly (checked at the top of
the `if self.force_stops_enabled:` block).

This is a naming/scope mismatch, not dead code like the (now-removed)
`speed_limit` toggle — `stop_prediction` *does* do something, just narrower
than its name implies once `force_stop` and `traffic_control` are in the
picture. Not fixed here — the right resolution isn't obvious (gate
`force_stop`'s stop-prediction contribution behind the same toggle? give
`traffic_control` its own toggle? rename `EOPDLONStopPredictionEnabled` to
clarify its actual narrower scope?) and this sits on EOP10, the live
production branch, so it's a decision worth making deliberately rather than
unilaterally during an audit.

<details><summary>Original softer framing (superseded by the trace above)</summary>

Unlike the 8 documented per-trigger toggles, `_has_traffic_control` (used
directly, unconditionally, in `_evaluate_auto_mode` and in the `force_stop`
calculation) has **no** corresponding entry in `_trigger_enabled` at all —
it can't be disabled even in principle. `NGP10_FEATURE_MATRIX.md`'s
enumerated list of 8 sub-toggles doesn't include `traffic_control` either,
which is at least *consistent* with the doc (unlike `speed_limit`, which the
doc lists as if functional). Softer finding than `speed_limit` since there's
no live-but-broken param here — just an asymmetry worth confirming is
intentional, given `traffic_control` is arguably the highest-impact trigger
(it feeds `force_stop`, which can bring the car to a full stop).

</details>

## Integration wiring — ✅ pass

- **`controlsd.py` ALCC** (`ngp_lat_alcc`): `alcc_active` allows
  `CC.latActive` to be true even when `selfdriveState.active` is false
  (`lat_active = selfdriveState.active or self.alcc_active`) — this looked
  concerning in isolation (steering active without full engagement) but
  matches the feature's own name/description exactly ("Always-on Lane
  Centering Control" — dp_panel.cc tooltip). Correctly gated behind an
  explicit opt-in toggle, `cruiseState.available`, `not standstill`, and
  `gearShifter != reverse`. Fault checks (`steerFaultTemporary/Permanent`)
  and `steerAtStandstill` still apply downstream unconditionally. Consistent
  with the documented feature intent, not a bug.
- **`desire_helper.py`** (`ngp_lca_speed_mph`, `ngp_lca_auto_sec`,
  `left_edge_detected`/`right_edge_detected`): auto-lane-change timer only
  forces `torque_applied = True` inside the `else` branch of the
  blindspot-detected check, so the auto-trigger is structurally incapable
  of firing while a blindspot/road-edge is flagged. `below_lane_change_speed`
  correctly treats `ngp_lca_speed <= 0` as "feature off" (never activates),
  matching `NGP10_FEATURE_MATRIX.md`'s documented 0-mph-means-off UI
  convention.
- **`plannerd.py`**: `ngp_flags` (DLON/COASTING/COASTING_DOWNHILL/BRSC) are
  computed once at process start, not re-read in the main loop — looked
  like a bug at first (toggling a setting while driving would do nothing
  until restart) but `NGP10_FEATURE_MATRIX.md` explicitly documents this as
  intentional ("Live vs. next-drive toggles" section) and consistent with
  how most comma-upstream toggles already behave. `COASTING_DOWNHILL` is
  correctly nested inside the `COASTING` flag check, mirroring EDP10's
  `ACM`/`ACM_DOWNHILL` nesting (Node 6).

## `ngp_suite.py` (`NGPFeatureSuite`) — ℹ️ note: not live-wired, test/inventory scaffolding only

Grepped all references: `NGPFeatureSuite` is only imported by its own file
and by `test_ngp_additional.py`. The real runtime daemons (`controlsd.py`,
`plannerd.py`) instantiate individual controllers directly (`NGPDLON`,
`TrafficJamAssist`, etc.), not through this composition root. This is fine
as a "does everything still construct" smoke-test fixture plus a written
inventory of port status (`PortState.INTEGRATED` vs `PORTABLE` vs
`EXTERNAL`/`EXCLUDED`) — but worth recording here so it isn't later mistaken
for the actual wiring path when auditing what's live vs. scaffolded.

---

**Node status: done** (representative pass, not exhaustive across all 13
controller files). 1 real finding, root-caused to EOP10 and fixed there
(dead `speed_limit` toggle — NGP10 still needs the port), 1 asymmetry
worth confirming intentional (`traffic_control` has no toggle), integration
wiring across `controlsd.py`/`desire_helper.py`/`plannerd.py` all checked
out correct against their documented intent.

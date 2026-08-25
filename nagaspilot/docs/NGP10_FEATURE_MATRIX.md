# NGP10 feature matrix

| Area | Feature | Runtime path | Status |
|---|---|---|---|
| Longitudinal | DLON | `nagaspilot/controls/ngp_dlon.py` → longitudinal planner | Integrated, always-on automatic (no user-selectable mode) |
| Lateral | DLAT | `nagaspilot/controls/ngp_dlat.py` → `controlsd.py` | Integrated, advisory (non-controlling), always-on automatic |
| Longitudinal | TJA gap/cut-in gate | `nagaspilot/controls/ngp_tja.py` → longitudinal planner | Integrated |
| Longitudinal | Speed-zone accel/jerk | `nagaspilot/speed_zones.py` → longitudinal planner | Integrated |
| Longitudinal | BRSC (Bumpy Road Speed Controller, vertical-IMU roughness) | `nagaspilot/controls/ngp_brsc.py` (pure policy, byte-identical across branches) → longitudinal planner | Integrated, default on (`ngp_lon_brsc`) |
| Lateral | ALCC/always-on lateral | inline in `controlsd.py` (not `ngp_alcc.py` — that module has zero non-test importers) | Integrated, default off |
| Lateral | LCA speed/auto-sec | upstream `DesireHelper` in `modeld.py` (not `ngp_lca.py` — same as above, unwired) | Integrated, default off |
| Lateral | Road-edge gate | `ngp_road_edge.py`, `modeld.py` | Integrated, default off |
| Lateral | ISO VM limits | OpenDBC lateral safety | Integrated |
| Longitudinal | Lane Change Lead Handoff (pure-camera adjacent-lane lead tracking) | `nagaspilot/controls/ngp_lc_lead_handoff.py` → longitudinal planner | Integrated, default off (`ngp_lon_lc_lead_handoff`), no panel toggle (matches EOP10) |
| Longitudinal | VTSC (Vision Turn Speed Control, 0-250m advisory) | `nagaspilot/controls/ngp_vtsc.py` → longitudinal planner | Integrated, default off (`ngp_lon_vtsc`), panel toggle in Longitudinal Ctrl section |
| Longitudinal | NSLC-equivalent (nav-source speed-limit enforcement) | `nagaspilot/controls/ngp_speed_policy.py` → longitudinal planner | Integrated, default off (`ngp_lon_nslc`), no panel toggle (matches EOP10's `EOPNSLCEnabled`). Nav-only — no map source on this branch, no driver-confirmation debounce on limit changes (see `EOP10_PARITY_CANDIDATES.md`) |
| Longitudinal | Adaptive acceleration limit (low-speed clamp + cruise-setpoint ramp-off) | `_apply_adaptive_accel_limit()` in `longitudinal_planner.py` | Integrated, always-on, no param (ported verbatim from EOP10, itself merged from FrogPilot). Only takes effect in DLON's `acc` mode — `blended` (E2E) mode uses `accel_clip = [ACCEL_MIN, ACCEL_MAX]` unmodified, so how often this actually applies depends on how often DLON picks `acc`, which hasn't been measured on this branch |
| Adaptation | ratio/stiffness | upstream `paramsd` / `LiveParametersV2` | Integrated and persistent |
| Gateway | BYD learned geometry | BrownPanda vehicle learner | Integrated and DFLASH-persistent |
| Radar | Converted BYD objects | BrownPanda + shared OpenDBC Tesla adapter on party bus 0 | NGP10 only; unavailable when frames are absent or with an unmodified fork |
| Perception | GridD/SOC/radar helpers | existing bounded helper modules | Portable; no control authority |

Vehicle actuation still requires the branch’s normal safety model and hardware
validation. A module being integrated does not claim target-car HIL completion.

**2026-08-08 to 2026-08-25, `plannerd` could not start at all** (`SubMaster.__init__`
raised `KeyError('mapData')` — see the "Correction (2026-08-25)" note further down).
Every row above whose runtime path is "longitudinal planner" (DLON, TJA, BRSC,
Lane Change Lead Handoff, VTSC) ran through that same process and therefore never
executed on this branch during that window, not just "unvalidated on road" —
`longitudinalPlan` itself was never published. Fixed 2026-08-25; still no on-road
validation of any of them as of this fix.

**Written-but-unwired modules** (`ngp_vtsc.py`, `ngp_mtsc.py`,
`ngp_collision.py`, `ngp_road_condition.py`, `ngp_traffic_control.py`,
`ngp_speed_policy.py`, `ngp_radar.py`, `ngp_alcc.py`, `ngp_lca.py`,
`selfdrive/adaptd/ngp_profile.py`) are deliberately not listed as
"Integrated" above — see `EOP10_PARITY_CANDIDATES.md` in this same directory
for the full EOP10-vs-NGP10 comparison, portability assessment per feature,
and a suggested wiring order.

**BRSC note (2026-08-04):** the pure policy module was ported first (commit
`822986441`), then this worktree's pre-existing uncommitted `ngp_*` →
`nagaspilot/controls/` migration was reviewed (diffed old vs. new file contents,
`py_compile` + pure-Python test verification) and committed in its own logical
commits, which unblocked wiring BRSC into `longitudinal_planner.py`/`plannerd.py`
the same way DLON/TJA are: an `NGPFlags.BRSC` bit read once from
`ngp_lon_brsc` in `plannerd.py`, `accelerometer` added to the `SubMaster`, cruise
speed reduced with a plain `min()` at the same point `force_slow_decel` zeroes
`v_cruise`, and the accel cap applied the same way `tja_result.accel_scale` is.
`ngpBrscActive/ngpBrscSpeed/ngpBrscRoughness` land at capnp `@46`-`@48` (next free
after `ngpTjaDesiredGap @45`) — a different range than EOP10's `@66`-`@68` and
EDP10's `@40`-`@42`, since all three schemas have diverged independently.

**NGP panel (2026-08-04, completed same day):** a new `NGPPanel` class
(`selfdrive/ui/qt/offroad/ngp_panel.{h,cc}`) was added as the "NGP" tab in
`settings.cc` (registered in `selfdrive/ui/SConscript`). NGP10 previously had no
dragonpilot/EOP-style toggle panel at all — only `DeveloperPanel`. Named `NGP`
(not `DP`) because this branch is mid-migration toward `dev/EOP10`'s naming,
matching the `ngp_`-prefix convention already used for shared modules (see
`nagaspilot/docs/NAMING_CONVENTIONS.md`).

First pass exposed only the BRSC toggle; then expanded to cover the rest of
this branch's user-facing `ngp_*` params. As of the 2026-08-09 coasting
retirement, `common/params_keys.h`'s `ngp_*` block has 15 keys: 7 are
reachable from the UI, and 8 (DLON's per-trigger sub-toggles) are
deliberately not — see below for why:

- **Lateral Ctrl section** (`add_lateral_toggles()`): `ngp_lat_alcc` (Always-on
  Lane Centering Control), `ngp_lat_road_edge_detection` (Road Edge Detection),
  `ngp_lat_lca_speed` (LCA Speed spinbox, mph) and `ngp_lat_lca_auto_sec` (Auto
  Lane Change delay, only shown once LCA speed > 0) — same
  `ParamSpinBoxControl`/`ParamDoubleSpinBoxControl` + show/hide pattern as
  `dp_panel.cc`.
- **Longitudinal Ctrl section** (`add_longitudinal_toggles()`): just the
  `ngp_lon_brsc` toggle. DLON has no panel control by design (see below).
- **DLAT/DLON mode selection removed, 2026-08-09**: both DLAT
  (Laneful/Laneless) and DLON (ACC/E2E) are default, always-on automatic
  behaviors of this branch — users cannot select or force a mode directly.
  `ngp_lat_dlat_mode`, `ngp_lon_dlon` (DLON master toggle), and
  `ngp_lon_dlon_mode` were removed from `params_keys.h` and the panel
  entirely (EOP10 kept its equivalent `EOPDLATMode`/`EOPDLONMode`
  `ButtonParamControl`s until 2026-08-10, when they were removed too —
  see `controlsd.py`'s DLAT block and `ngp_dlon.py::update_params()`'s
  docstring). `controlsd.py` resolves `self.dlat_use_laneless` directly from
  `NGPDLAT.update_model()`'s hysteresis-debounced suggestion every frame;
  `ngp_dlon.py`'s `self.mode` is hardcoded to `NGPDLONMode.AUTO`.
- **DLON's per-trigger sub-toggles remain, and gained a ninth
  (2026-08-09)**: `ngp_lon_dlon_curves`/`_lane_confidence`/`_slow_lead`/
  `_low_speed`/`_stop_prediction`/`_navigation`/`_signal`/`_speed_limit`/
  `_force_stops` — none reachable from the panel, matching `dev/EOP10`'s
  `eop_panel.cc` choice to leave its equivalent `EOPDLON*Enabled` set
  off-panel too. `_lane_confidence` couples DLAT's resolved Laneless state
  into DLON's automatic switch (see "DLAT→DLON confidence coupling" below);
  it is a *tuning* toggle for which signals the automatic switch considers,
  not a way to force a mode, so it doesn't conflict with the mode-selection
  removal above.
  **`_speed_limit` history (2026-08-08):** an audit found this toggle read
  its param every second but was never consulted by any trigger-evaluation
  logic on either EOP10 or NGP10 — no `detect_speed_limit_*` trigger existed.
  Root cause was that the trigger itself had never been implemented, not
  that the toggle was meant to be permanently inert. Implemented the real
  trigger the same day: `detect_speed_limit_trigger()` in `ngp_dlon.py`
  reads `navInstruction.speedLimit` (m/s) and fires when that limit is more
  than `SPEED_LIMIT_TRIGGER_MARGIN_MS` (2 m/s) below current speed, on the
  theory that E2E's smoother deceleration profile handles the transition
  into a lower posted limit better than stock ACC (same rationale as the
  existing `navigation` trigger, which uses `navInstruction.maneuverDistance`
  for the analogous "upcoming route event" case).
  **Correction (2026-08-25):** the original version of this entry also had
  `detect_speed_limit_trigger()` reading `mapData.speedLimit` (km/h,
  preferred over nav) to match `dev/EOP10`'s `nslc.py` source preference, and
  had `plannerd.py`'s `SubMaster` subscribe to `'mapData'`. That subscription
  crashed: NGP10's `cereal/log.capnp` has no `MapData` struct/Event field and
  `cereal/services.py` has no `'mapData'` entry (EOP10 has both), so
  `SubMaster.__init__`'s `SERVICE_LIST[s]` lookup raised `KeyError('mapData')`
  on every `plannerd` start — confirmed by reproducing the lookup directly
  and by confirming no process anywhere in this tree publishes `mapData`.
  Fixed by dropping the `mapData` subscription and the map branch entirely;
  `detect_speed_limit_trigger()` is nav-only until NGP10 gets a real
  map-data source. This also answers `EOP10_PARITY_CANDIDATES.md`'s MTSC
  entry's open question about whether `mapData` carries OSM curvature data —
  it doesn't exist on this branch at all, so MTSC has moved out of Tier 2.
  TJA has no backing param on
  any branch (always active, not user-toggleable), so it was never a panel
  candidate.

  *(2026-08-09: the mode selector this paragraph originally documented was
  removed — see "DLAT/DLON mode selection removed" above. Left as written
  for its trigger-implementation narrative.)*

**Live vs. next-drive toggles:** DLON's per-trigger toggles (including the new
`ngp_lon_dlon_lane_confidence`) are re-read while driving
(`ngp_dlon.py::update_params()` polls them every 1s) since there's no mode
param left to gate them behind. Every panel-exposed toggle (`ngp_lat_alcc`,
`ngp_lat_lca_speed`, `ngp_lat_lca_auto_sec`, `ngp_lat_road_edge_detection`,
`ngp_lon_brsc`, `ngp_lon_vtsc`) — plus the non-panel `ngp_lon_lc_lead_handoff`
and `ngp_lon_nslc` — is read once at process start
(`plannerd.py`/`controlsd.py`/`modeld.py`, all before their `while True:`
loop) — a change takes effect on the next onroad transition
(these are `only_onroad`/car-gated processes in `process_config.py`, so this
means "next drive," not "reboot the device"), not mid-drive. This matches how
most openpilot/dragonpilot settings-panel toggles behave (the panel itself is
normally only reachable offroad), so it's expected UX, not a bug — noted here
so it isn't mistaken for one.

`ParamSpinBoxControl`/`ParamDoubleSpinBoxControl` didn't exist in NGP10's
`selfdrive/ui/qt/widgets/controls.h` before this — ported verbatim from
`dev/EOP10`'s self-contained versions (not EDP10's, which depend on a
`DoubleSpinBoxControl` base styled with dragonpilot-specific icon assets that
don't exist in this tree). Verified with a real (non-scons) syntax-only
`g++ -fsyntax-only` compile of `ngp_panel.cc` and `settings.cc` against the
system's Qt5 dev headers + `moc` — both compile clean (zero errors, only a
pre-existing unrelated `QButtonGroup::buttonClicked` deprecation warning also
present in `dp_panel.cc`/`eop_panel.cc`). This is stronger than the previous
brace-counting check, but is still not the project's actual `scons` Qt build
(blocked in this worktree by an unrelated, pre-existing `opendbc.INCLUDE_PATH`
gap in `panda/SConscript` — not fixed, out of scope) — an on-hardware or
working-scons-env build/render check is still recommended before shipping.
`-fsyntax-only` also proves the code parses, not that the two new `Q_OBJECT`
classes' moc output links; risk is low (`controls.h` already carries ten other
`Q_OBJECT` classes moc'd the same way), but this hasn't been linked or run.

**Not gated by `vehicle_has_long_ctrl`:** unlike `dp_panel.cc` (which hides
`dp_lon_acm`/`dp_lon_acm_downhill` on stock-longitudinal cars), `NGPPanel`
shows DLON/BRSC unconditionally, same as `eop_panel.cc`. `NGPPanel`'s
constructor doesn't read `CarParams` at all — there's currently nothing on
this branch's panel that needs per-car gating (see the coasting retirement
below for why the one feature that would have needed it is gone instead).

**Coasting (ACM) retired, 2026-08-09:** `ngp_lon_coasting`/
`ngp_lon_coasting_downhill` and their entire wiring — `nagaspilot/controls/
ngp_coasting.py`, `NGPFlags.COASTING`/`COASTING_DOWNHILL` in
`longitudinal_planner.py`/`plannerd.py`, the panel toggle, the
`nagaspilot/controls/ngp_suite.py` manifest entry, and the test that
exercised it — were removed outright rather than renamed. Root cause: this
was ported from EDP10's ACM concept (`ngp_coasting.py`'s own docstring:
"migrated from EDP10 ACM behavior"), but `dev/EOP10` has no ACM-equivalent
feature *at all* — its coast behavior (`get_coast_accel`/`allow_throttle` in
`longitudinal_planner.py`) is stock openpilot's unconditional
throttle-probability logic, not a user toggle, and it isn't gated by DLON
mode. There was no DLON-side concept to rename this into; keeping it under
any name would still be carrying EDP10-only functionality forward on a
branch whose explicit purpose is proving EOP10's software on comma 3
hardware ahead of the ExoPilot 01M migration — so the call was to match
EOP10 exactly (nothing) rather than keep a relabeled EDP10 feature. AEM,
by contrast, needed no such treatment: NGP10's `ngp_lon_dlon_mode`
(Chill/Experimental/Auto) already replaced that concept before this session,
the same way EOP10's own `EOPDLONMode` did.

**`nagaspilot/controls/ngp_suite.py` also removed, 2026-08-09:** it was a
"composition root" / feature-port manifest (a `NGPFeatureSuite` class
instantiating every pure-policy controller plus a static `manifest()` table
of port status) that neither `dev/EOP10` nor `dev/EDP10` has any equivalent
of, and per the BRSC note above it was never wired into any runtime path on
any branch — the only things that referenced it were this doc and one test
(`test_ngp_additional.py`, `test_adaptive_profile_and_manifest_distinguish_
integrated_features`, trimmed to drop the manifest assertions and renamed
`test_adaptive_profile_distinguishes_personality`). This file's own table
above already carries the same "what's ported, what's the status" tracking
in prose form, so removing the code duplicate loses no information — it was
NGP10-invented scaffolding, not a pattern carried over from either sibling
branch.

**DLAT wired into `controlsd.py`, 2026-08-09:** `ngp_dlat.py`'s own docstring
called it a "proving line" deliberately kept non-controlling — `controlsd.py`
now calls `NGPDLAT.update_model(model_v2, v_ego=CS.vEgo)` every frame and
publishes the resolved `dlat_use_laneless`/`lane_confidence` on
`controlsState` (`ngpDlatUseLaneless @68`, `ngpDlatLaneConfidence @69`) purely
as advisory telemetry — it does not feed any actuator, unlike EOP10's
`dlat.py`, which gates a real curvature nudge via `red.py` (stereo+YOLO road
edge fusion, no comma-3 hardware equivalent — see `EOP10_PARITY_CANDIDATES.md`
for why full parity there isn't portable).

**DLAT/DLON both made unconditional automatic, no user choice, 2026-08-09:**
per explicit instruction, this branch does not let users select or force a
lateral (Laneful/Laneless) or longitudinal (ACC/Experimental-E2E) mode
directly — both are resolved automatically from model confidence every frame.
This was a deliberate divergence from `dev/EOP10` at the time — it still
exposed `EOPDLATMode`/`EOPDLONMode` as user-facing `ButtonParamControl`s in
`eop_panel.cc`, and that choice was intentionally *not* ported here — but
EOP10 removed its own mode selectors the next day (2026-08-10), closing
the divergence; both branches are now unconditional-automatic. Fixed a
crash introduced mid-edit in this same change: `controlsd.py`'s per-frame
block and its `controlsState` publish briefly referenced `self.dlat_mode`/
`self._dlat_param_t` after they'd already been removed from `__init__`
(`AttributeError` on every frame) — caught before commit via `py_compile` +
direct-import test execution, not left in the shipped commit.

**DLAT→DLON confidence coupling, 2026-08-09:** DLAT and DLON previously ran
fully independently despite both being confidence-driven automatic switches
over the same underlying signal. Per request, studied `~/pilot/dragonpilot`'s
`aem.py`/`acm.py` for prior art first — both are non-commercial-only licensed
(Copyright Rick Lan, 2025) and, on inspection, aren't actually about
lane-confidence coupling anyway (`aem.py` uses throttle-intent probability,
`acm.py` is lead-gated coast suppression, the concept already retired from
this branch above) — so no code was copied; `ngp_dlon.py` gained an
independently-written `detect_lane_confidence_trigger()` that reads
`sm['controlsState'].ngpDlatUseLaneless` (cross-process: DLAT runs in
controlsd, DLON in plannerd, which already subscribes `controlsState`) and
adds it as the lowest-priority `_evaluate_auto_mode()` trigger, next to
`curves` — both are perception-difficulty signals. Reuses DLAT's own
hysteresis-resolved decision rather than re-deriving a second threshold, so
there's one source of truth. A stale/missing `controlsState` resolves to
no-trigger (neutral), matching DLAT's own default-to-laneful convention
rather than misreading absence as low confidence. Gated by a new per-trigger
toggle, `ngp_lon_dlon_lane_confidence` (default on) — see above. Verified via
6 new tests in `selfdrive/controls/tests/test_ngp_dlon_lane_confidence.py`
(direct-import execution, same technique as the rest of this doc's testing
notes — `test_ngp_dlon_mtsc.py`'s own imports hit the pre-existing
`RadarData.ErrorDEPRECATED` capnp/opendbc version-skew blocker in this
dev-PC worktree; **fixed 2026-08-23** — `cereal/log.capnp` now references
`Car.RadarData.Error`, matching the rename already present in this branch's
own `opendbc_repo` pin and in `EXO-ELEC/opendbc`'s `master`; `import cereal`
works normally again, the direct-import workaround is no longer required
for new tests, see `EGPU_INTEGRATION.md`). The identical coupling was also
implemented on
`dev/EOP10`'s `dlon.py`/`dlat.py` (at the time, only applied while
`EOPDLONMode` was `Auto`; since EOP10 removed `EOPDLONMode` entirely on
2026-08-10, it's now always consulted there too, same as NGP10).

**DLAT made a real default (not just advisory), 2026-08-09:** DLAT had no
actuator or downstream consumer on this branch — controlsd.py only
published its resolved state as `controlsState` telemetry (until the
coupling above), because this branch's lateral control is unified
end-to-end (`model_v2.action.desiredCurvature`), with no separate
laneful/laneless path planner left to switch between. Confirmed by
checking `~/pilot/sunnypilot`'s own changelog: they removed their
equivalent Dynamic Lane Profile outright — "upstream laneless model is now
on by default" — for the same architectural reason. Per explicit request
to make DLAT default rather than advisory, found the part of the laneless
concept that still means something post-E2E, also from sunnypilot's
changelog: "Permanent: Laneless during Auto Lane Change execution."

Added an LCA (Auto Lane Change) initiation gate: `modeld.py` computes
`NGPDLAT.lane_confidence(modelv2_send.modelV2.laneLineProbs)` at the same
site `evaluate_road_edges()` already runs (in-process, no new cross-process
subscription needed — `DesireHelper` already receives model_v2-derived data
there), compares it against `NGPDLAT`'s own `DEFAULT_ENTER_THRESHOLD`
(0.40, promoted from a constructor default to a module constant so it's
referenceable without instantiating the stateful hysteresis arbiter — this
is a one-shot check, not the Laneful/Laneless state machine), and passes
the result as a new `low_lane_confidence` bool into `desire_helper.py`'s
`DesireHelper.update()`. Blocks `preLaneChange → laneChangeStarting`
initiation (and pauses the nudgeless auto-timer) exactly where the
existing `blindspot_detected` check already blocks — same tier, same
"initiation only, never abort mid-maneuver" semantics. Default on, no
toggle, matching "implement as default."

License note: `~/pilot/sunnypilot` is ALSO non-commercial/
permission-required for commercial use (Copyright Haibin Wen, SUNNYPILOT
LLC, 2024) — same category as dragonpilot's `aem.py`/`acm.py`. No
sunnypilot Python source was read or copied (a grep of its current
`selfdrive/` tree found zero "laneless" hits; the concept exists only in
changelog prose, which is what informed this independent implementation).
FrogPilot was not available locally (`~/pilot/FrogPilot` does not exist)
and was not consulted.

Verified: 4 new tests in
`selfdrive/controls/tests/test_desire_helper_lane_confidence.py` pass via
direct import (stubs `cereal.log` directly for the same pre-existing
capnp-blocker reason noted throughout this doc); `test_ngp_dlat.py`'s 6
tests still pass unchanged after promoting `DEFAULT_ENTER_THRESHOLD` to a
module constant. The identical gate was implemented on `dev/EOP10`
(`dlat.py`, `desire_helper.py`) the same day — see that branch's
`docs/eop/03_Software/Controllers/DLAT.md` §11.

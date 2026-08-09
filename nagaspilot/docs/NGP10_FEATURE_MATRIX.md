# NGP10 feature matrix

| Area | Feature | Runtime path | Status |
|---|---|---|---|
| Longitudinal | DLON | `nagaspilot/controls/ngp_dlon.py` → longitudinal planner | Integrated |
| Longitudinal | TJA gap/cut-in gate | `nagaspilot/controls/ngp_tja.py` → longitudinal planner | Integrated |
| Longitudinal | Speed-zone accel/jerk | `nagaspilot/speed_zones.py` → longitudinal planner | Integrated |
| Longitudinal | BRSC (Bumpy Road Speed Controller, vertical-IMU roughness) | `nagaspilot/controls/ngp_brsc.py` (pure policy, byte-identical across branches) → longitudinal planner | Integrated, default on (`ngp_lon_brsc`) |
| Lateral | ALCC/always-on lateral | `controlsd.py`, car safety flag | Integrated, default off |
| Lateral | LCA and road-edge gate | `ngp_lca.py`, `ngp_road_edge.py`, modeld | Integrated, default off |
| Lateral | ISO VM limits | OpenDBC lateral safety | Integrated |
| Adaptation | ratio/stiffness | upstream `paramsd` / `LiveParametersV2` | Integrated and persistent |
| Gateway | BYD learned geometry | BrownPanda vehicle learner | Integrated and DFLASH-persistent |
| Radar | Converted BYD objects | BrownPanda + shared OpenDBC Tesla adapter on party bus 0 | NGP10 only; unavailable when frames are absent or with an unmodified fork |
| Perception | GridD/SOC/radar helpers | existing bounded helper modules | Portable; no control authority |

Vehicle actuation still requires the branch’s normal safety model and hardware
validation. A module being integrated does not claim target-car HIL completion.

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

First pass exposed only the BRSC toggle; now completed with every other
already-integrated, user-facing `ngp_*` param this branch has (all 15 keys in
`common/params_keys.h`'s `ngp_*` block are now reachable from the UI — none
left unexposed):

- **Lateral Ctrl section** (`add_lateral_toggles()`): `ngp_lat_alcc` (Always-on
  Lane Centering Control), `ngp_lat_road_edge_detection` (Road Edge Detection),
  `ngp_lat_lca_speed` (LCA Speed spinbox, mph) and `ngp_lat_lca_auto_sec` (Auto
  Lane Change delay, only shown once LCA speed > 0) — same
  `ParamSpinBoxControl`/`ParamDoubleSpinBoxControl` + show/hide pattern as
  `dp_panel.cc`.
- **Longitudinal Ctrl section** (`add_longitudinal_toggles()`, extended):
  `ngp_lon_dlon` (DLON master toggle) plus a `ButtonParamControl` mode selector
  for `ngp_lon_dlon_mode` (Chill/Experimental/Auto, mirroring
  `ngp_dlon.py::NGPDLONMode`), and the existing `ngp_lon_brsc` toggle.
- **Deliberately still not exposed**: DLON's eight individual per-trigger
  sub-toggles (`ngp_lon_dlon_curves`/`_slow_lead`/`_low_speed`/
  `_stop_prediction`/`_navigation`/`_signal`/`_speed_limit`/`_force_stops`) —
  `dev/EOP10`'s `eop_panel.cc` has the identical set of backing params
  (`EOPDLON*Enabled`) and made the same choice to expose only the mode selector,
  not each trigger individually. Matching that precedent rather than inventing
  new UI surface not modeled on any sibling branch.
  **`_speed_limit` history (2026-08-08):** an audit found this toggle read
  its param every second but was never consulted by any trigger-evaluation
  logic on either EOP10 or NGP10 — no `detect_speed_limit_*` trigger existed.
  Root cause was that the trigger itself had never been implemented, not
  that the toggle was meant to be permanently inert. Implemented the real
  trigger the same day: `detect_speed_limit_trigger()` in `ngp_dlon.py`
  reads `mapData.speedLimit` (km/h, preferred) falling back to
  `navInstruction.speedLimit` (m/s) — same source preference and unit
  handling as `dev/EOP10`'s `nslc.py` — and fires when that limit is more
  than `SPEED_LIMIT_TRIGGER_MARGIN_MS` (2 m/s) below current speed, on the
  theory that E2E's smoother deceleration profile handles the transition
  into a lower posted limit better than stock ACC (same rationale as the
  existing `navigation` trigger, which uses `navInstruction.maneuverDistance`
  for the analogous "upcoming route event" case). `plannerd.py`'s
  `SubMaster` gained a `mapData` subscription (was missing entirely) and
  `navInstruction` moved into `ignore_alive` (both optional/intermittent
  services, matching EOP10's existing pattern) so the new trigger actually
  has data to read.
  TJA has no backing param on
  any branch (always active, not user-toggleable), so it was never a panel
  candidate.

**Live vs. next-drive toggles:** `ngp_lon_dlon_mode` is the only one of these
params re-read while driving (`ngp_dlon.py::update_params()` polls it every
1s). Every other toggle added here (`ngp_lat_alcc`, `ngp_lat_lca_speed`,
`ngp_lat_lca_auto_sec`, `ngp_lat_road_edge_detection`, `ngp_lon_dlon`,
`ngp_lon_brsc`) is read once
at process start (`plannerd.py`/`controlsd.py`/`modeld.py`, all before their
`while True:` loop) — a change takes effect on the next onroad transition
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

# NGP10 feature matrix

| Area | Feature | Runtime path | Status |
|---|---|---|---|
| Longitudinal | DLON | `nagaspilot/controls/ngp_dlon.py` → longitudinal planner | Integrated |
| Longitudinal | Coasting/downhill | `nagaspilot/controls/ngp_coasting.py` → longitudinal planner | Integrated, default off |
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
the same way DLON/coasting/TJA are: an `NGPFlags.BRSC` bit read once from
`ngp_lon_brsc` in `plannerd.py`, `accelerometer` added to the `SubMaster`, cruise
speed reduced with a plain `min()` at the same point `force_slow_decel` zeroes
`v_cruise`, and the accel cap applied the same way `tja_result.accel_scale` is.
`ngpBrscActive/ngpBrscSpeed/ngpBrscRoughness` land at capnp `@46`-`@48` (next free
after `ngpTjaDesiredGap @45`) — a different range than EOP10's `@66`-`@68` and
EDP10's `@40`-`@42`, since all three schemas have diverged independently. `ngp_suite.py`
is *not* wired into the runtime path on any branch (it's a standalone feature-port
inventory), so BRSC was intentionally not added to its manifest.

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
  `ngp_dlon.py::NGPDLONMode`), `ngp_lon_coasting` (Adaptive Coasting Mode) with
  `ngp_lon_coasting_downhill` shown only while coasting is enabled, and the
  existing `ngp_lon_brsc` toggle.
- **Deliberately still not exposed**: DLON's eight individual per-trigger
  sub-toggles (`ngp_lon_dlon_curves`/`_slow_lead`/`_low_speed`/
  `_stop_prediction`/`_navigation`/`_signal`/`_speed_limit`/`_force_stops`) —
  `dev/EOP10`'s `eop_panel.cc` has the identical set of backing params
  (`EOPDLON*Enabled`) and made the same choice to expose only the mode selector,
  not each trigger individually. Matching that precedent rather than inventing
  new UI surface not modeled on any sibling branch. TJA has no backing param on
  any branch (always active, not user-toggleable), so it was never a panel
  candidate.

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

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

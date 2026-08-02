# NGP10 feature matrix

| Area | Feature | Runtime path | Status |
|---|---|---|---|
| Longitudinal | DLON | `nagaspilot/controls/ngp_dlon.py` → longitudinal planner | Integrated |
| Longitudinal | Coasting/downhill | `nagaspilot/controls/ngp_coasting.py` → longitudinal planner | Integrated, default off |
| Longitudinal | TJA gap/cut-in gate | `nagaspilot/controls/ngp_tja.py` → longitudinal planner | Integrated |
| Longitudinal | Speed-zone accel/jerk | `nagaspilot/speed_zones.py` → longitudinal planner | Integrated |
| Longitudinal | BRSC (Bumpy Road Speed Controller, vertical-IMU roughness) | `nagaspilot/controls/ngp_brsc.py` (pure policy, ported byte-identical from EOP10) | Portable, not yet wired into `longitudinal_planner.py` on this branch — see note below |
| Lateral | ALCC/always-on lateral | `controlsd.py`, car safety flag | Integrated, default off |
| Lateral | LCA and road-edge gate | `ngp_lca.py`, `ngp_road_edge.py`, modeld | Integrated, default off |
| Lateral | ISO VM limits | OpenDBC lateral safety | Integrated |
| Adaptation | ratio/stiffness | upstream `paramsd` / `LiveParametersV2` | Integrated and persistent |
| Gateway | BYD learned geometry | BrownPanda vehicle learner | Integrated and DFLASH-persistent |
| Radar | Converted BYD objects | BrownPanda + shared OpenDBC Tesla adapter on party bus 0 | NGP10 only; unavailable when frames are absent or with an unmodified fork |
| Perception | GridD/SOC/radar helpers | existing bounded helper modules | Portable; no control authority |

Vehicle actuation still requires the branch’s normal safety model and hardware
validation. A module being integrated does not claim target-car HIL completion.

**BRSC note (2026-08-03):** this worktree already had a large uncommitted change set
touching `longitudinal_planner.py`, `plannerd.py`, `cereal/log.capnp`,
`common/params_keys.h`, and `ngp_suite.py` at the time BRSC was ported (an in-progress
ngp_* → `nagaspilot/controls/` migration). To avoid bundling that unrelated,
unreviewed work into this commit, only the portable policy module and its test were
added here. Wiring into `longitudinal_planner.py` (subscribe `accelerometer`, apply via
`_apply_speed_limit`, add `ngp_suite.py` manifest entry, add capnp
`brscActive/brscSpeed/brscRoughness` fields, add `NGPBRSCEnabled` to
`common/params_keys.h`) mirrors the EOP10 integration in
`docs/eop/03_Software/Controllers/BRSC.md` on `dev/EOP10` and should be done once the
pending migration on this branch is committed.

# NGP10 feature matrix

| Area | Feature | Runtime path | Status |
|---|---|---|---|
| Longitudinal | DLON | `nagaspilot/controls/ngp_dlon.py` → longitudinal planner | Integrated |
| Longitudinal | Coasting/downhill | `nagaspilot/controls/ngp_coasting.py` → longitudinal planner | Integrated, default off |
| Longitudinal | TJA gap/cut-in gate | `nagaspilot/controls/ngp_tja.py` → longitudinal planner | Integrated |
| Longitudinal | Speed-zone accel/jerk | `nagaspilot/speed_zones.py` → longitudinal planner | Integrated |
| Lateral | ALCC/always-on lateral | `controlsd.py`, car safety flag | Integrated, default off |
| Lateral | LCA and road-edge gate | `ngp_lca.py`, `ngp_road_edge.py`, modeld | Integrated, default off |
| Lateral | ISO VM limits | OpenDBC lateral safety | Integrated |
| Adaptation | ratio/stiffness | upstream `paramsd` / `LiveParametersV2` | Integrated and persistent |
| Gateway | BYD learned geometry | BrownPanda vehicle learner | Integrated and DFLASH-persistent |
| Radar | Converted BYD objects | BrownPanda + shared OpenDBC Tesla adapter on party bus 0 | NGP10 only; unavailable when frames are absent or with an unmodified fork |
| Perception | GridD/SOC/radar helpers | existing bounded helper modules | Portable; no control authority |

Vehicle actuation still requires the branch’s normal safety model and hardware
validation. A module being integrated does not claim target-car HIL completion.

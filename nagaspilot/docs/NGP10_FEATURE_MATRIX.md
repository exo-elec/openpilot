# NGP10 Feature Matrix

| Group | Feature | NGP10 module | Status | Authority |
| --- | --- | --- | --- | --- |
| Core | DLAT | `ngp_dlat.py` | Shadow | None |
| Core | DLON | `ngp_dlon.py` | Shadow | None |
| Core | VTSC / MTSC | `ngp_vtsc.py`, `ngp_mtsc.py` | Proposal | None |
| Core | Speed sources/zones | `ngp_speed_policy.py`, `nagaspilot/speed_zones.py` | Proposal | None |
| Core | Adaptive coasting | `ngp_coasting.py` | Proposal | None |
| Lateral | ALCC | `ngp_alcc.py` | Proposal | None |
| Lateral | LCA / road edge | `ngp_lca.py`, `ngp_road_edge.py` | Proposal/shadow | None |
| Safety | Radar zones | `ngp_radar.py` | Shadow | None |
| Safety | Collision risk | `ngp_collision.py` | Shadow | Stock AEB |
| Safety | Road condition | `ngp_road_condition.py` | Proposal | None |
| Safety | Traffic control | `ngp_traffic_control.py` | Proposal | None |
| Perception | Sparse GridD/BEV | `gridd/lazy_bev.py` | Shadow | None |
| Perception | MonoD backend | `monod/ngp_monod.py` | Default-off contract | None |
| Perception | SOC / overlays | `pathd/ngp_soc.py`, `gridd/ngp_overlays.py` | Proposal/shadow | None |
| Optional data | Route curvature | `mapd/ngp_curvature.py` | Pure helper | None |
| Optional data | Adaptive telemetry | `adaptd/ngp_profile.py` | Proposal | None |
| Diagnostics | Trip statistics | `tripd/ngp_trip.py` | Shadow/in-memory | None |
| Platform | Vehicle parsing/control | Upstream OpenDBC Tesla | External | Upstream/Panda |
| Platform | Target-car translation | TC275 gateway | External | TC275 |
| Excluded | RKNN/RGA/EOP HAL/stereo/radar4D | None | Excluded | N/A |

Runtime-visible shadow results are composed by `ngp_shadowd` and published as
`ngpState`. Pure helpers without a valid source remain inactive.

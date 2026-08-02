# NGP10 Comma 3 Second-Pass Audit

## Compared branches

- `dev/EDP10`: DragonPilot application features plus BYD port work.
- `dev/EOP10`: expanded controls, perception, map, diagnostics, and RK3588 HAL.

## Additional ports selected

| Source concept | Minimal NGP10 treatment | Reason |
| --- | --- | --- |
| EOP AEB predictor | Radar-based collision-risk shadow | Useful diagnostic; stock AEB remains authoritative |
| EOP RCD/SQSC | Pure road-condition policy accepting validated observations | Avoid OpenCV camera load and persistent surface DB |
| EOP TLSC | Traffic-control stop proposal | Useful for replay; never requests braking |
| EOP MapD curvature | Route-polyline curvature helper | No OSM daemon, network, cache, or GPS requirement |
| EOP TripD | In-memory trip accumulator inside `ngp_shadowd` | Negligible resource cost and no parameter churn |

## Already covered or superseded

EDP AEM, ACM, ALKA, LCA, road-edge detection, and external radar are covered by
DLON, adaptive coasting, ALCC, LCA/road-edge gates, and normalized radar.
EOP speed resolution, radar zones, GridD, MonoD, SOC, overlays, and adaptive
telemetry are covered by the first-pass minimized modules.

## Not ported

| Area | Decision |
| --- | --- |
| CAT | Upstream `paramsd` already learns vehicle parameters |
| Following-distance knobs | Upstream longitudinal personalities already provide this policy |
| DDSC | Do not create a second driver-monitoring speed intervention |
| EOP AEB/TLSC actuation | Preserve stock AEB and longitudinal authority |
| RED path repulsion | Depends on stereo/YOLO and directly alters the path |
| RCD OpenCV camera classifier | Unmeasured CPU/thermal cost on comma 3 |
| CSLB/surface SQLite learning | Unvalidated crowd data, persistent writes, and location coupling |
| Full MapD/OSM daemon | Network, cache, storage, and GPS coupling are unnecessary for MTSC |
| Calibration replacement | Keep upstream comma 3 `calibrationd` and `paramsd` |
| PathD long-horizon/A* | Requires richer BEV, stereo, surface history, and additional compute |
| PointCloudD/StereoD/radar4D | Hardware mismatch |
| EOP UI/branding/TTS/recording | Not a portable driving capability; increases package/runtime surface |
| EDP Toyota/VAG/HKG patches | Target vehicle is normalized as Tesla by TC275 |

## Validation state

Only static syntax/style/diff consistency is performed during the porting
phase. Runtime, cereal build, replay, resource, gateway, Panda, and HIL tests
remain a separate phase.

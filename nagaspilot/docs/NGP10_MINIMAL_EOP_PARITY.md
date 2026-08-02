# NGP10 Minimal EOP/EDP Parity

## Completion boundary

NGP10 is a minimized EOP10 application layer on official openpilot v0.10.0.
It must exceed the usable application features in EDP10 without copying
EOP10's RK3588 platform stack or replacing upstream OpenDBC.

BrownPanda owns target-car translation and presents Tesla Model 3 HW3-style CAN.
NGP10 continues to use upstream OpenDBC Tesla vehicle parsing/control and Panda
safety. A radar-capable BrownPanda exposes converted radar on party bus 0 through
NGP10's narrow OpenDBC Tesla `RadarInterface`. EOP10 `vehicled`, `socketd`,
duplicate Tesla vehicle parser/controller, and Python safety layers are explicitly excluded.

## EDP10 parity and supersession

| EDP10 behavior | NGP10 replacement | Port state |
| --- | --- | --- |
| DragonPilot AEM | DLON trigger/hysteresis evaluator | Shadow |
| DragonPilot ACM | Bounded adaptive-coasting evaluator | Non-controlling proposal |
| DragonPilot ALKA | ALCC independent engagement proposal | Non-controlling proposal |
| DragonPilot LCA | Human-nudge LCA with DM, BSM and radar gaps | Non-controlling proposal |
| Road-edge lane-change block | v0.10.0 road-edge confidence gate | Implemented |
| EDP/DragonPilot VTSC direction | VTSC p97 model estimator | Non-controlling proposal |
| CITY/HIGHWAY policy document | Canonical 12/24/36 m/s zone module | Implemented |
| BYD parser/controller/safety | BrownPanda translation + upstream Tesla OpenDBC; NGP radar adapter | External gateway responsibility |
| Stock/factory AEB and longitudinal | Preserved unchanged | Authoritative |

## Additional minimized EOP10 features

DLAT model adaptation, MTSC math, speed-source resolution, adaptive coasting, radar2D/radar3D
tracking, sparse metric-only GridD, default-off single-camera MonoD backend,
SOC offset proposals, diagnostic overlays, and adaptive telemetry profile
computation are implemented as portable modules. None has control authority.

GridD never creates stereo depth from comma 3's different-FOV road cameras.
MonoD accepts metric positions only from an independently calibrated backend.
Radar4D, RKNN/RGA, EOP HAL, side/rear daemons, BLE/NCP/OBD transports, and
target-car PID decoding remain excluded.

The modules are composed by `ngp_shadowd`, which consumes existing v0.10.0
`modelV2`, `carState`, `radarState`, `liveTracks`, driver-monitoring, and
navigation services. It publishes `ngpState` diagnostics at 20 Hz. It never
publishes `carControl`, `sendcan`, or a planner command.

DragonPilot branding, Dashy, rainbow-path rendering, model download/selection,
auto-shutdown, delayed logging, and brand-specific Toyota/VAG/HKG patches are
not feature-parity targets for the minimized Tesla-gateway product. Upstream
openpilot already owns device lifecycle and supported-brand behavior; NGP10
surpasses EDP10 at the portable driving-feature layer, not by reproducing its
settings count or cosmetic surface.

## Deferred validation

Feature implementation precedes testing by project decision. Recorded-route
replay, stale-input coverage, resource measurements, Panda checks, gateway
wire checks, and hardware-in-the-loop validation remain required before any
proposal can gain a control consumer.

# NGP10 EOP Transition Plan

`dev/NGP10` is the application/selfdrive proving line. It starts from official
openpilot v0.10.0 and ports selected EOP10 behavior while keeping the original
comma 3 camera/process APIs. TC275 FreeRTOS BrownPanda presents the target car
as Tesla Model 3 HW3-format CAN. Features are proven here first, then promoted
into EOP10 for HAL and hardware-in-the-loop testing.

## Scope

- Port DLAT, DLON, VTSC, MTSC, ALCC, LCA, radar2D/radar3D, SOC, GridD, and
  single-camera MonoD as isolated, reviewable features.
- Keep driver monitoring enabled and preserve the CITY/HIGHWAY speed policy.
- Use BYD/Panda work and BrownPanda Tesla-format wire tests as references.
  NGP10 must not import EOP RK3588, stereo, radar4D, or hardware HAL code.
- Keep control output disabled until unit tests, replay checks, and stationary
  CAN validation pass.

## Sequence

1. Record the exact EOP10 source commits and required v0.10.0 schema/API
   changes.
2. Port DLAT behind a non-controlling logger, then add replay coverage before
   allowing it to select a path.
3. Port DLON with factory-longitudinal behavior as the default; keep any
   openpilot-longitudinal path explicitly opt-in.
4. Prove selfdrive behavior on NGP10 with unit, replay, and comma 3-compatible
   process tests.
5. Promote only proven application commits into EOP10, where HAL, packaging,
   camera plumbing, and hardware-in-the-loop behavior are tested.
6. Compare comma 3 routes with and without the driver camera and retain the
   evidence in this directory.

## Current implementation status

| Area | Status |
| --- | --- |
| DLAT | v0.10.0 model adapter and non-controlling arbiter implemented; recorded-route replay pending |
| DLON | Pure trigger/state evaluator implemented; actuator integration disabled |
| VTSC | Model-only shadow estimator implemented |
| MTSC | Map-independent curvature evaluator implemented |
| Speed policy/ALCC/LCA | Portable non-controlling proposal modules implemented |
| GridD/SOC/MonoD/overlays | Minimal capability-gated shadow modules implemented |
| Radar2D/radar3D | Tesla-normalized tracking and LCA zone gates implemented; source binding pending |
| Adaptive telemetry | Pure normalized profile computer implemented; transport excluded |
| BrownPanda/Tesla HW3 boundary | Upstream OpenDBC retained; external gateway validation pending |

`ngp_shadowd` composes the portable modules and publishes the reserved-fork
`ngpState` cereal service at model cadence. The schema includes an explicit
`controlAuthority = false` field and has no control or planner consumer.

## Host validation (2026-08-02)

| Gate | Result |
| --- | --- |
| Cereal schema and service generation | Pass |
| Portable feature, capability, and gateway contract tests | 112 passed |
| Manager process preparation | 4 passed, 1 platform skip |
| `ngp_shadowd` one-cycle messaging smoke test | Pass |
| Ruff and whitespace checks | Pass |

Recorded-route replay, stationary CAN, comma 3 resource measurement, and
hardware-in-the-loop validation remain promotion gates; host validation does
not grant control authority.

## Non-goals

Do not import EOP `vehicled`/`socketd`, duplicate upstream Tesla/OpenDBC code,
rebrand or delete DragonPilot code, import EOP hardware infrastructure into
NGP10, or enable untested lateral/longitudinal output on a moving vehicle.

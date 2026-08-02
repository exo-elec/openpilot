# NGP10 EOP Transition Plan

`dev/NGP10` is the application/selfdrive proving line. It starts from official
openpilot v0.10.0, the same application baseline as EOP10, and ports selected
EOP10 behavior while keeping the original comma 3 camera and process APIs.
Features are proven here first, then promoted into EOP10 for hardware/HAL
integration testing.

## Scope

- Port DLAT and DLON as isolated, reviewable features.
- Keep driver monitoring enabled and preserve the CITY/HIGHWAY speed policy.
- Use the proven BYD Atto 3 parser and Panda safety work as references. NGP10
  must not import EOP RK3588, stereo, radar, or hardware-specific HAL code.
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

## Non-goals

Do not rebrand or delete DragonPilot code, import EOP hardware infrastructure
into NGP10, or enable untested lateral/longitudinal output on a moving vehicle.

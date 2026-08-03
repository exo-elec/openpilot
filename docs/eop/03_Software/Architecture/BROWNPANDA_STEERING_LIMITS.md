# BrownPanda gateway steering limits (reference)

EOP10 targets `~/panda/TC375_BrownPanda` as its vehicle gateway (NGP10 uses
`TC275_BrownPanda`; EDP10 talks BYD natively with no gateway - see
`~/panda/TC275_BrownPanda/docs/ARCHITECTURE_TESLA_TO_BYD.md` for the full
three-branch picture). This note is a pointer, not a duplicate: the
steering-limit design itself lives in the gateway firmware and is documented
there.

## What EOP10 needs to know

`system/socketd/safety/tesla_safety.py` is EOP10's own Layer 1 (Tesla-frame,
"tighter" side): `MAX_STEERING_ANGLE = 2700` (270°, explicitly 75% of stock
Tesla's `opendbc/safety/modes/tesla.h` `max_angle=3600`), `MAX_STEERING_RATE
= 200` (20°/s, 80% of 250). These numbers are grounded in Tesla's own real
safety mode and were not part of the BYD-specific fix below - no change
needed here.

Downstream of that, `TC375_BrownPanda`'s gateway firmware (`DBC/byd_atto3.c`,
`DBC/safety.c`) translates the Tesla-frame command to BYD's frame
(`Safety_TranslateTeslaSteerAngle`) and enforces, in order:

1. A continuous ISO 11270 vehicle-model formula (`Safety_IsoMaxAngleDeg`/
   `Safety_IsoMaxAngleRateDegps`, generic in `safety.c`) - BYD Atto3's real
   operating limit, tightest at every real speed.
2. An 8-point speed-zoned backstop LUT (`0/2/6/12/18/24/30/36` m/s, the
   canonical `nagaspilot/speed_zones.py` grid) - defense-in-depth, should
   never bind in normal operation.

As of 2026-08-03 the physical ceiling is **390°** (`MAX_STEERING_ANGLE_DEG`
in `TC375_BrownPanda/DBC/safety.h`), corrected from an earlier uncited 120°
placeholder. Full numbers, derivation, and the U-turn/tight-curve sanity
check are in `TC275_BrownPanda/docs/SAFETY_LIMITS.md` (identical firmware
logic on both gateway variants) and openpilot's own
`nagaspilot/docs/STEERING_LIMIT_POLICY.md` (`dev/EDP10`).

The underlying math (`Safety_ZoneInterp`, `Safety_IsoMaxAngleDeg`,
`Safety_IsoMaxAngleRateDegps`, `Safety_TranslateTeslaSteerAngle`) is now
generic infrastructure in the gateway's `safety.h`/`safety.c` - only the
slip factor and LUT values are BYD Atto3-specific, so a future target
vehicle on this gateway doesn't require reimplementing this logic.

## Open items

Same as the EDP10/TC275/TC375 notes: no target-vehicle steering-rate
capture exists yet (LUT values are reasoned from physics and a community
reference's real telemetry, not this project's own car), and UN R79 was
researched but not used in the final design (ISO 11270 was sufficient).

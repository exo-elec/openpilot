# Steering limit policy: ISO comfort layer + speed-zoned backstop

**Status: implemented** (2026-08-03). This note started as pre-implementation
research; the design it converged on has since been built and committed
across `opendbc/safety/modes/byd.h`, `opendbc/car/byd/{values,carcontroller}.py`,
and both `TC275_BrownPanda`/`TC375_BrownPanda` firmware. This is now a
description of what's live, not an open question.

## The two-layer contract

- **Layer 2 (panda `byd.h` + BrownPanda TC275/TC375 gateway)**: the hardware
  safety net. Enforces the **same continuous ISO 11270 formula** as Layer 1
  (not structurally looser on that check), plus a **speed-zoned backstop
  LUT** at 100% of its computed values. This is the final word before the
  vehicle bus.
- **Layer 1 (openpilot controller, `carcontroller.py`)**: the application
  comfort layer. Same continuous ISO formula (governs real operation day to
  day), plus its own backstop LUT at **80%** of Layer 2's - so Layer 1
  degrades first if something upstream of the shared ISO check misbehaves.

Both backstop LUTs should almost never bind: the continuous ISO check is
tighter than either backstop at every real speed above a few m/s (see
table below).

## Standards used

**ISO 11270** (Lane Keeping Assistance Systems): max lateral accel **3.0
m/s²**, max lateral jerk **5.0 m/s³**, with a road-roll tolerance added
(`+ 9.81 * 0.06`, since safety code has no live roll signal) giving the
actual constant used everywhere: **`3.0 + 9.81*0.06 ≈ 3.59`** for both the
accel and jerk term (this matches upstream `opendbc/safety/lateral.h`'s
`steer_angle_cmd_checks_vm` exactly, which uses the same value for both -
verified intentional, not a copy-paste bug).

**UN R79** was researched as an alternative anchor but not used in the
final implementation - see Open items.

## What's implemented

### Continuous ISO limit (real operating limit)

`Safety_IsoMaxAngleDeg`/`Safety_IsoMaxAngleRateDegps` (generic, in
BrownPanda's `safety.c`) and `steer_angle_cmd_checks_vm`/
`apply_steer_angle_limits_vm` (opendbc's shared `lateral.h`, called from
`byd.h`/`carcontroller.py`) all compute the same formula:

```
max_angle_or_rate = (ISO_accel_or_jerk / v²) × steer_ratio × (1 - slip·v²) × wheelbase
```

using BYD Atto3's real `steer_ratio=19.8`, `wheelbase=2.72` (both from
`opendbc/car/byd/values.py`'s `CarSpecs`), clamped at a **390°** physical
ceiling.

### Speed-zoned backstop LUT (defense-in-depth)

A 7-point piecewise-linear table at `0/6/12/18/24/30/36` m/s, chosen to
keep lateral accel in a **0-1.35g band** at every breakpoint *and* every
midpoint between them (verified numerically - an earlier idea to use a
straight line from 390°@0 to 30°@36 m/s was rejected because G-force
depends on v², not v, and that line's implied accel bulges to **2.89g**
around 27 m/s despite looking reasonable at both endpoints):

| Speed (m/s) | 0 | 6 | 12 | 18 | 24 | 30 | 36 |
|---|---:|---:|---:|---:|---:|---:|---:|
| Angle, panda/gateway (100%) | 390° | 360° | 240° | 120° | 60° | 45° | 30° |
| Angle, openpilot (80%) | 312° | 288° | 192° | 96° | 48° | 36° | 24° |
| Rate, panda/gateway (100%) | 4 | 4 | 4 | 3.2 | 2.4 | 1.6 | 1.2 |
| Rate, openpilot (80%) | 3.2 | 3.2 | 3.2 | 2.56 | 1.92 | 1.28 | 0.96 |

(Rate in deg/20ms frame.) The rate LUT's 0/6/12 m/s points hold flat at the
real evidenced EPS mechanical ceiling instead of following the jerk formula,
which diverges to a physically unachievable number near zero speed - see
`~/panda/byd-atto3-openpilot-port`'s `values.py`: *"Stock Veoneer max=4.8,
5 caused 29deg spikes/shaky wheel, so 3 stays within the safe stock range"*
(real telemetry from the actual camera/EPS hardware, not a derived number).

### The 120° vs 390° correction

TC275/TC375 firmware originally had `MAX_STEERING_ANGLE_DEG = 120.0f`,
traced to `TC275_BrownPanda` commit `36c23c0bb5` ("update safety") with
zero citation. `byd.h` was briefly changed to match it (120°) earlier in
this session before that was found to be backwards: the route-driven
community reference (`~/panda/byd-atto3-openpilot-port`) uses
`max_angle = 3900` (390°) explicitly, "matches the python-side
ANGLE_LIMITS" - real evidence the 120° number never had. All three
implementations (`byd.h`, `values.py`, TC275/TC375 `safety.h`) now use 390°.

### Generalized for future car ports

`Safety_ZoneInterp`/`Safety_IsoMaxAngleDeg`/`Safety_IsoMaxAngleRateDegps`/
`Safety_TranslateTeslaSteerAngle` live in BrownPanda's `safety.h`/`safety.c`
as generic vehicle-model infrastructure - only the slip factor and the two
LUT arrays are BYD Atto3-specific (kept in `byd_atto3.c`). A future car
port on the same gateway (e.g. BYD Dolphin, referenced in this workspace's
`~/panda/BYD_Dolphin/`) only needs its own protocol decoding plus those few
numbers, not a reimplementation of this math.

## Real-world sanity check: tight curves and U-turns

Worked through during design: Thailand-style highway U-turn lanes require
turning close to the physical steering limit at low speed. The continuous
ISO formula already handles this correctly without special-casing - it's
flat at the 390° physical ceiling from 0 m/s up to roughly 9.7 m/s (where
the ISO-derived value first drops below 390°), so as long as VTSC/MTSC has
slowed the vehicle into that range before the tight turn is needed, full
steering authority is available. A genuinely large-radius highway curve
(500m+) only needs a few degrees at 36 m/s even under the tightest
(0.3g) limit - only an unrealistically tight curve (~150m radius or less)
would need enough angle to conflict with the backstop at highway speed,
and taking such a curve at 130 km/h would already be unsafe regardless of
what the steering system allows (tire grip fails first).

## Open items

- **UN R79 primary source still unread.** Not used in the final design
  (ISO 11270 was sufficient and already had local code precedent), but if
  revisited, the 2.94 m/s³ figure and road-wheel/steering-wheel framing
  from the earlier research pass need verification against the actual
  UNECE text, not just search summaries.
- **No target-car steering-rate capture exists.** The BYD-specific slip
  factor (`-0.0006166479`) and the backstop LUT's specific numbers are
  reasoned from physics and the community reference's telemetry, not a
  capture on this project's own vehicle. Still provisional pending that.
- **BYD's 0x1E2 angle frame is assumed, not confirmed.** This design
  assumes the CAN-commanded angle is steering-wheel-equivalent (matching
  how `tesla.h` uses `steer_ratio` for its own `_vm` check). Not verified
  against a firmware source read or wire capture for BYD specifically.
- **No test harness available for TC275/TC375** (AURIX TASKING toolchain)
  in this environment - the C firmware changes were verified by formula
  derivation, brace/paren balance, and diffing against the equivalent,
  test-covered `opendbc` port, not by compiling.

## Sources

- [ISO 11270:2014 — Lane keeping assistance systems (LKAS), ISO](https://www.iso.org/standard/50347.html)
- [ISO 11270 sample PDF, iTeh Standards](https://cdn.standards.iteh.ai/samples/50347/558ca926c9a44182aa8af3ff1c9fb0ac/ISO-11270-2014.pdf)
- [UN/ECE R79, "encyclopedia" of assisted steering](http://www.nev01.com/article/20734775.html)
- [ACSF-09-07 (OICA-CLEPA) industry proposal, UNECE wiki](https://wiki.unece.org/download/attachments/36536322/ACSF-09-07%20-%20(OICA-CLEPA)%20Industry%20proposal%20-%20CSF-ACSF%20A%20%20B1.pdf?api=v2)
- [Assisted Driving Systems UN R79 Homologation, ATIC](https://www.atic-ts.com/european-assisted-driving-systems/)
- `~/panda/byd-atto3-openpilot-port` (route-driven community reference,
  local checkout - not a URL)

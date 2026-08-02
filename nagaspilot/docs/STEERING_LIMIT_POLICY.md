# Steering limit policy: ISO/regulatory floor vs. application comfort layer

This is a research note, not an implementation. No firmware or safety-mode
code has been changed as a result of it.

## The two-layer contract

- **Layer 2 (BrownPanda TC275/TC375 gateway)**: the hardware safety net.
  Deliberately loose, and its number should come from regulatory/standards
  ceilings (UN R79, ISO 11270), not from comfort tuning. Its job is to catch
  runaway or corrupted commands, not to shape the driving experience.
- **Layer 1 (openpilot on NGP10/EDP10, visionpilot)**: the application
  comfort layer. Deliberately tighter than Layer 2, tuned for UX/smoothness
  using the practical speed-zone scheme (currently CRAWL/CITY/HIGHWAY in
  `byd.h`; a CITY/URBAN/HIGHWAY + separate CRAWL(2 m/s) rename was proposed
  but is unresolved — see Open items).

This matches what EDP10's `byd.h` and TC275/TC375 already do structurally
(app-layer taper vs. gateway hard limit); what's been missing is grounding
Layer 2's number in an actual standard instead of an apparent copy of
Tesla's own limits.

## Standards, with numbers

**ISO 11270** (Lane Keeping Assistance Systems, performance requirements):
max lateral acceleration **3.0 m/s²**, max lateral jerk **5.0 m/s³**. This
is already implemented in this exact codebase —
`opendbc/safety/lateral.h:4` (`ISO_LATERAL_ACCEL = 3.0`) and `lateral.h:302`
(`// Lower than ISO 11270 lateral jerk limit, which is 5.0 m/s^3`). Solid:
confirmed by both the standard's own summary and the local code comment
independently.

**UN R79** (steering equipment / ACSF — Automatically Commanded Steering
Function, Category B1/corrective steering): below 10 km/h, a fixed road-wheel
steering angle rate limit of **0.4 rad/s (~22.9°/s)**; above 10 km/h, the
rate is derived from a lateral-jerk-style parameter of **2.94 m/s³**. An
ACSF is out of compliance if it tracks a curve at more than 0.3 m/s² above
the OEM-declared max lateral acceleration.

> **Not yet verified against the primary source.** This came from an
> AI-summarized web search, not a direct read of the UNECE R79 text or the
> OICA/CLEPA ACSF industry proposal PDF. Treat the 2.94 m/s³ figure and the
> "road wheel, not steering wheel" framing as provisional until someone
> reads the primary document.

## Current state across all four sources (frame-normalized)

R79's 0.4 rad/s figure is a **road-wheel** angle rate. Every rate limit in
this project's code is expressed at the **steering-wheel** (EPS command)
level, so a straight comparison needs the vehicle's steering ratio. BYD
Atto3's real ratio is already in this tree:
`opendbc/car/byd/values.py:71` — `CarSpecs(..., steerRatio=19.8)`. Using
that (not Tesla's 12 — they are different vehicles and the two must not be
conflated):

**R79 low-speed ceiling, steering-wheel-equivalent: 22.9°/s × 19.8 ≈ 453°/s**

| Source | Value | Frame | Notes |
|---|---|---|---|
| UN R79 low-speed (<10 km/h) | ~453°/s | steering-wheel (converted, BYD ratio) | regulatory ceiling |
| TC275/TC375 `byd_atto3.c` (current) | 500°/s flat | steering-wheel (assumed) | ~10% above the R79 ceiling — much closer to "roughly regulatory" than it looked before this conversion, not an arbitrary number |
| `opendbc/safety/modes/byd.h` CRAWL (0 m/s) | 200°/s | steering-wheel | app-layer, well inside R79 |
| `opendbc/safety/modes/byd.h` HIGHWAY (24 m/s) | 25°/s | steering-wheel | app-layer, far inside R79 — this is the comfort tuning, not a safety ceiling |
| EOP10 `tesla_safety.py` (Layer 1, EOP10-only) | 270° max angle, 20°/s rate | steering-wheel | app-layer for a different vehicle boundary (Tesla-format gateway path) |
| `opendbc/safety/modes/tesla.h` `_vm` | ISO-11270-derived, continuous | steering-wheel | uses Tesla's own `steer_ratio=12` — do not reuse for BYD |

**Resolved (2026-08-02, later in this session):** the max-angle discrepancy
was not a frame mismatch — it was TC275's 120° that was wrong. Checked
against `~/panda/byd-atto3-openpilot-port` (the route-driven community
reference this whole port cites elsewhere): its
`opendbc/safety/safety/safety_byd.h` uses `max_angle = 3900` (390°)
explicitly because it "matches the python-side ANGLE_LIMITS," and its
`values.py` documents `MAX_ANGLE_RATE = 3` deg/20ms with real telemetry
("Stock Veoneer max=4.8 (5 caused 29deg spikes/shaky wheel), so 3 stays
within the safe stock range"). TC275's 120° traces to
`TC275_BrownPanda` commit `36c23c0bb5` ("update safety"), which introduced
it with zero citation. `opendbc/safety/modes/byd.h`, `opendbc/car/byd/values.py`,
and `opendbc/safety/tests/test_byd.py` are corrected back to 390° (matching
their own state before this session's earlier, mistaken "align to TC275"
change); TC275/TC375 firmware's `MAX_STEERING_ANGLE_DEG` still needs the
same correction. The rate side did not have this problem — TC275's old flat
500°/s and the new ISO-vm floor (4°/20ms) are both in a defensible range
relative to the reference's evidenced 3-4.8 deg/20ms.

## Two structural constraints found

1. **`struct lookup_t` is fixed at 3 points** (`x[3]`/`y[3]`,
   `opendbc/safety/safety_declarations.h:80-83`), shared across every
   opendbc car's angle/torque safety mode. A 4-zone breakpoint table
   (CRAWL/CITY/URBAN/HIGHWAY or similar) cannot be expressed in `byd.h`
   without changing a struct every car port depends on.
2. **opendbc has already moved past breakpoint tables.** `lateral.h:298`:
   `// TODO: remove the inaccurate breakpoint angle limiting function above
   and always use this one` — referring to `steer_angle_cmd_checks_vm()`,
   the ISO-11270-grounded, continuous vehicle-model approach.
   `opendbc/safety/modes/tesla.h:227` already calls the `_vm` variant;
   `byd.h` still uses the older 3-point lookup table. Adopting `_vm` for BYD
   would need BYD's real `steer_ratio` (19.8, now known) and dissolves the
   zone-count problem entirely — a continuous formula has no breakpoints to
   name or argue about.

## Open items (block any implementation)

- **UN R79 primary source unread.** The 2.94 m/s³ figure and the
  road-wheel/steering-wheel framing need verification against the actual
  UNECE R79 text or the OICA/CLEPA ACSF proposal, not just search summaries.
- **No target-car steering-rate capture exists.** Per EDP10 commit
  `b7f989b14`, `byd.h`'s current CITY/HIGHWAY taper values are provisional,
  "shaped after psa.h's taper" — not measured on this car. Any new number
  (R79-derived or otherwise) has the same problem until real capture data
  exists.
- **BYD's 0x1E2 angle frame is assumed, not confirmed.** This note assumes
  the CAN-commanded angle is steering-wheel-equivalent (matching how
  `tesla.h` uses `steer_ratio` for its `_vm` check). Not verified against a
  firmware source read or wire capture for BYD specifically.
- **Zone naming is still unresolved.** The CITY/URBAN/HIGHWAY rename with a
  separate CRAWL(2 m/s) constant was proposed but not decided — and is
  moot if the `_vm`/ISO path is adopted instead of a breakpoint table.
- **No firmware or opendbc code has been changed under this note.** All
  BrownPanda firmware edits made earlier in this session were reverted;
  see the "concurrent process" note below for why this must go through
  the actual repo owners before any real edit lands.

## Concurrent-writer note (repo hygiene, not part of the research)

While working in `~/panda/TC275_BrownPanda` and `~/panda/TC375_BrownPanda`,
a separate, currently-running process (committing as `EXO-ELECTRONICS`, the
same git identity as this session) auto-committed working-tree changes
across both repos mid-session — commits `d8e0873` (TC275, "docs: define
NGP10 single-channel CAN contract") and `739880e` (TC375, "docs: define
EOP10 single-channel CAN contract") — sweeping up doc edits made in this
session alongside unrelated files neither of us should touch blind
(`DBC/comma.h`, `DBC/gateway.c`, `DBC/tesla_auth.h`, `Board/tc375_can_topology.h`,
still uncommitted as of this note). This session's only uncommitted
behavioral change (`DBC/byd_atto3.c` in TC275) was reverted cleanly and
nothing of the concurrent process's work was touched or lost. Worth knowing
before any further firmware work lands in those two repos — there's a race
here that needs coordinating, not code review.

## Sources

- [ISO 11270:2014 — Lane keeping assistance systems (LKAS), ISO](https://www.iso.org/standard/50347.html)
- [ISO 11270 sample PDF, iTeh Standards](https://cdn.standards.iteh.ai/samples/50347/558ca926c9a44182aa8af3ff1c9fb0ac/ISO-11270-2014.pdf)
- [UN/ECE R79, "encyclopedia" of assisted steering](http://www.nev01.com/article/20734775.html)
- [ACSF-09-07 (OICA-CLEPA) industry proposal, UNECE wiki](https://wiki.unece.org/download/attachments/36536322/ACSF-09-07%20-%20(OICA-CLEPA)%20Industry%20proposal%20-%20CSF-ACSF%20A%20%20B1.pdf?api=v2)
- [Assisted Driving Systems UN R79 Homologation, ATIC](https://www.atic-ts.com/european-assisted-driving-systems/)

# Longitudinal braking envelopes

**Status:** engineering rationale only; not a certification or homologation claim.

This note separates three values that serve different purposes. They must not be
presented as interchangeable comfort, actuator, or emergency-braking limits.

| Envelope | Current value | Intended meaning |
|---|---:|---|
| Cruise speed-profile deceleration | `-1.2 m/s²` | Gentle speed-control bound (`CRUISE_MIN_ACCEL`), not the full lead-following actuator range. |
| OpenPilot normal-control floor | about `-2.5 m/s²` (Tesla raw `312`) | Research-backed outer comfort boundary for ordinary cruise/following; it is not a routine target. |
| Explicit collision-mitigation / BrownPanda floor | `-3.48` / `-3.5 m/s²` | Available only after the host marks `DAS_aebEvent=ACTIVE`; BrownPanda remains the independent hardware backstop. This does not establish AEBS compliance. |
| UN Regulation No. 152 emergency-braking demand | at least `5.0 m/s²` | Regulatory AEBS service-brake demand when an imminent collision is detected, subject to the regulation's operating and test conditions. |

## Where the numbers come from

### `-2.5 m/s²` normal-control boundary

The normal boundary is selected from real-world comfort evidence rather than as
a percentage of the hardware range. Tesla `DAS_control` encodes zero
acceleration at raw `375`; raw `312` represents approximately `-2.5 m/s²`.
The cruise speed profile remains gentler at `-1.2 m/s²`, and the controller
should request only the braking actually needed.

The retired `-2.8 m/s²` host value came only from taking 80% of the historical
Tesla/Panda encoded magnitude. It was not an ISO requirement or comfort result
and is no longer the normal longitudinal policy.

### `-3.48` / `-3.5 m/s²`

ISO 15622:2018 covers adaptive cruise control, whose role and operating domain
are distinct from AEBS. Its longitudinal bound is expressed as a **two-second
average** automatic deceleration, including a `3.5 m/s²` upper magnitude at
higher vehicle speed. That context explains the common OpenPilot controller
limits around `-3.5 m/s²`; it does not mean every instant must be clipped at
exactly that value, nor that it is a passenger-comfort target.

ISO source: <https://www.iso.org/standard/71515.html>

UN Regulation No. 152 instead requires an emergency-braking demand of at least
`5.0 m/s²` when the system detects an imminent collision. It also defines
warning timing, test scenarios, speed ranges, false-reaction behavior, failure
indication, and performance outcomes. A `-3.5 m/s²` command ceiling cannot, by
itself, support a claim that this implementation meets UN R152.

Regulatory source: <https://eur-lex.europa.eu/eli/reg/2024/2497/oj/eng>

ISO 22839 specifies forward-vehicle collision-mitigation system behavior and
verification; ISO 22733-1 supplies car-to-car AEBS test methods. These are test
and system requirements, not permission to select one universal deceleration
constant.

- <https://www.iso.org/standard/45339.html>
- <https://www.iso.org/standard/84241.html>

## Real-world interpretation

A 2026 test-track study with 41 participants found `-1.5` to `-2.5 m/s²` was
generally preferred for comfort and perceived safety, while `-3.5 m/s²` was
more acceptable when a pedestrian appeared suddenly. This supports an adaptive
profile: brake earlier and more gently when the hazard is known, while reserving
the strongest validated authority for genuinely imminent hazards.

Research source: <https://doi.org/10.1016/j.trf.2025.06.029>

These findings are useful design evidence, not a universal legal comfort limit.
Comfort depends on brake onset, jerk, duration, occupant posture, road grade,
surface adhesion, tyres, vehicle loading, and actuator response.

## Required implementation policy

1. Normal cruise must optimize for early, smooth braking and should not treat
   `-2.5 m/s²` as a target. It is the outer ordinary-control boundary.
2. Corner BLE radar and auxiliary cameras remain advisory-only. They must never
   acquire braking authority. Corner radar also must not originate the canonical
   forward FCW; its short-range roles are BSD, RCW, FCTA and RCTA. Forward FCW
   is owned by the built-in forward 77 GHz radar/model pipeline. At low speed,
   the two front corners may issue a distinct near-front obstacle warning for a
   high-vehicle bumper blind zone; it remains advisory-only and requires two
   corners or corner-plus-camera evidence for the warning/chime level.
3. Collision-mitigation entry requires a confirmed track from the built-in
   forward 77 GHz radar plus range, closing-speed, confidence, continuity, and
   driver-override checks. TTC is necessary but not sufficient: entry requires
   three continuous radar frames and a physically meaningful required-
   deceleration result. When the request crosses below `-2.5 m/s²`, the Tesla
   CAN layer sets `DAS_aebEvent=ACTIVE`; host safety then permits the bounded
   emergency range down to `-3.48 m/s²`.
4. Host and BrownPanda limits are independent safety layers. A wider emergency
   envelope must be implemented and validated in both layers; changing only one
   creates an unusable or unsafe command path.
5. Until vehicle brake performance, CAN semantics, stopping outcomes, false
   positives, degraded sensing, road grade, low-adhesion behavior, and override
   behavior pass closed-course and HIL tests, describe the feature as
   **experimental bounded collision mitigation**, not certified AEBS.
6. Do not claim UN R152 compliance while the maximum validated demand remains
   below `5.0 m/s²` or while the regulation's complete test matrix has not been
   passed by the vehicle installation.

Normal braking ramps at no more than `2.0 m/s³`. Emergency collision
mitigation may ramp at `4.5 m/s³`, leaving margin below BrownPanda's independent
`5.0 m/s³` jerk backstop. The transition is a controlled ramp, not an
instantaneous acceleration step.

## Validation evidence required before increasing authority

- Instrument commanded versus measured longitudinal acceleration with an
  independent calibrated reference, including actuator lag and jerk.
- Test dry/high-adhesion first, then grade, payload, tyre, temperature, wet and
  split-friction cases in a controlled facility.
- Exercise stationary, moving, cut-in and crossing targets, including vulnerable
  road users and false-positive objects.
- Verify driver brake/throttle/steering override, stock-AEB arbitration,
  heartbeat loss, stale radar, sensor misalignment and BrownPanda rejection.
- Record TTC at warning and brake onset, minimum distance or impact speed, peak
  and two-second-average deceleration, jerk, and every safety-layer decision.

ISO 26262 and ISO 21448 provide the functional-safety and SOTIF processes needed
to justify the architecture and perception limitations. Following their design
principles is not equivalent to certification.

- <https://www.iso.org/publication/PUB200262.html>
- <https://www.iso.org/standard/77490.html>

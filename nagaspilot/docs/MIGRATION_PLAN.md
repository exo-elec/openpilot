# EDP10 Migration Plan

## Method

Port behavior in small commits, using audited DragonPilot reference history and
the proven BYD Atto 3 repository as references rather than cherry-picking either
tree wholesale. Preserve upstream and DragonPilot behavior unless a BYD or
comma 3 requirement is demonstrated.

## Stages

1. Document immutable bases, ownership boundaries, and validation criteria.
2. Add passive BYD identification, DBC/parser definitions, fingerprints,
   torque data, and deterministic parser tests. **Complete** in `a2394317d`.
3. Add classic Panda CAN safety rules and forwarding tests; build the standard
   Panda target used by original comma 3. The first ignition-only Panda change
   is **complete** in `7a7bb4fe5`; the BYD safety model remains draft/unvalidated.
4. Add steering and HUD control while preserving required stock message fields.
5. Validate factory longitudinal and stock AEB as the default mode.
6. Add openpilot longitudinal as an explicit trial, with safety tests and route
   evidence independent from factory-longitudinal results.
7. Port selected EOP policies only when they fit the fixed CITY/HIGHWAY product
   model and do not weaken driver monitoring.
8. Keep MonoD/SOC in shadow mode until comma 3 resource use, camera geometry,
   temporal tracking, and guardrail behavior are demonstrated from recordings.

## Current Port State

The first BYD slice is passive and is adapted from inherited DragonPilot code plus
`shemps/byd-atto3-openpilot-port` commit
`5b34194240bb831719629d2fd095fae5daaed1e0`. It registers the Atto 3,
reference fingerprints and firmware, DBC/checksum, state parser, and parser
tests. The interface uses Panda `noOutput`, reports `dashcamOnly`, advertises no
openpilot-longitudinal trial, and its controller always returns an empty CAN
send list. Actuation and Panda safety remain later, independent stages.

Documentation from the transition branch is now under `nagaspilot/docs/`:
the BYD port plan, HIL checklist, parameter evidence, parity audit, manual
capture procedure, speed policy, and deferred EOP/MonoD studies are references
only until their individual exit gates are met.

## Remaining Agent Tasks

1. ~~Finish the EDP10-compatible BYD safety model.~~ **Done.** `test_byd.py`
   now subclasses `PandaCarSafetyTest`/`AngleSteeringSafetyTest` (the current
   EDP10 API, not the `CarSafetyTest`/`make_can_msg_safety` API `test_byd.py`
   was drafted against). The dynamic, `controls_allowed`-gated `.fwd` hook was
   removed — `check_relay` in `BYD_TX_MSGS` already gives static forwarding
   block, which is what `test_fwd_hook`'s API requires and what
   `BYD_ATTO3_COMMA3_PORT_PLAN.md` asks for. Full local safety suite (2600+
   tests, all safety modes) passes with no TX-address collisions. Interface
   still selects `noOutput`.
2. ~~Confirm the current safety model ID.~~ **Done.** `SAFETY_BYD 35U` is
   unique against the full `safety_declarations.h` enum.
3. Port the angle/HUD controller as a separate, byte-tested commit. Preserve
   stock ACC/AEB and keep output disabled until stationary hardware validation.
4. Add factory-longitudinal recording/replay checks before considering the
   opt-in openpilot `0x32E` trial. Radar remains outside the software path.
5. Validate comma 3 camera/driver-camera profiles and BYD fingerprints against
   target-car recordings; the current fingerprint is single-vehicle evidence.
6. Run the relevant Panda build and host tests under Python 3.11/3.12; retain
   manual route/CAN/video evidence in the HIL checklist.

## Deferred Work

MonoD, GridD, SOC, VTSC, EOP RK3588 infrastructure, UI rebranding, Dashy
removal, private radar/CAN-FD, and broad `cereal`/`modeld`/`dmonitoring` ports
are not next tasks. Revisit them only after the BYD passive, safety, and stock
longitudinal gates are complete.

## Explicit Non-Goals

Do not replay wholesale UI, `cereal`, `modeld`, or `dmonitoring` API changes
from newer branches. Do not import EOP hardware HAL/RK3588 infrastructure, stereo
or radar dependencies, or its large daemon stack. Do not delete or rebrand
DragonPilot/Dashy sources. Avoid generic Panda driver changes unless a focused
BYD test proves they are necessary.

## Per-Commit Gate

Each functional commit must state provenance and safety impact, run focused
unit/safety tests, pass `git diff --check`, and keep stock and openpilot control
paths distinguishable. Host tests are not vehicle validation; retain recorded
CAN/video, route, CANape, or HIL evidence for hardware decisions.

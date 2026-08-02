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
   **The `0x1E2` steering-command side is done:** `opendbc/car/byd/bydcan.py`'s
   `create_steering_control` and `opendbc/car/byd/carcontroller.py`'s
   `CarController` generate it via `apply_std_steer_angle_limits` at 50 Hz,
   gated on `CarControl.enabled` so it stays inert while `interface.py` keeps
   `dashcamOnly`/`noOutput` (verified by
   `test_controller_disabled_sends_nothing`). Byte-verified against the
   firmware's real `WriteRaw` sequence
   (`test_lateral_cmd_matches_firmware_wire_layout`, cross-checked against
   `~/panda/TC275_BrownPanda/DBC/byd_atto3.c:1173-1201`) and against
   `opendbc/safety/modes/byd.h`'s `byd_tx_hook` directly
   (`test_controller_engaged_matches_safety_model` packs real controller
   output and feeds it through `libsafety`) — the discriminating check that a
   controller and the safety model actually agree on every limit and
   sentinel, not just that each passes its own test in isolation.

   Angle-rate limits are 3-step, tapering with speed at
   `nagaspilot/docs/SPEED_ZONE_POLICY.md`'s `CITY_SPEED_MPS`/`HIGHWAY_SPEED_MPS`
   breakpoints (12/24 m/s): `4`/`2`/`.5` deg-per-50Hz-cycle winding up,
   `4`/`3`/`1.5` unwinding (always faster than winding up, for recovery
   safety). The 0 m/s value is unchanged from the original flat draft so city
   behavior is untouched; the 12/24 m/s values are a provisional design (no
   BYD-specific steering-rate capture exists) shaped after `psa.h`'s taper,
   which shares this struct's exact `max_angle`/`angle_deg_to_can` scale. Both
   `opendbc/safety/modes/byd.h`'s `BYD_STEERING_LIMITS` and
   `opendbc/car/byd/values.py`'s `CarControllerParams.ANGLE_LIMITS` must stay
   mirrored; `test_controller_engaged_matches_safety_model` is what enforces
   that. Note: `nagaspilot/docs/SPEED_ZONE_POLICY.md` cites
   `nagaspilot/speed_zones.py` as the canonical constants source, but that
   file does not currently exist in this tree (only stale `.pyc` caches do) -
   the values above are hardcoded in `opendbc/car/byd/values.py` with a
   citation comment instead, since `opendbc_repo` has no dependency on
   `nagaspilot/` regardless.
   **The `0x316` HUD side remains blocked:** `byd.h`'s
   `check_relay = true` on both `0x1E2` and `0x316` statically blocks
   camera-to-car forwarding for both addresses the moment this safety mode is
   active — regardless of `controls_allowed` — so the controller must also emit
   a substitute `0x316` every cycle or the cluster/EPS loses that frame
   entirely. A byte-exact software passthrough is not safe to assume:
   `AUTO_LIGHT`, `HMA_ON_OFF`, `LDSW_TYPE` have no firmware witness, and the
   firmware reads an unexplained bit 35 (`handsOffDetected`) that overlaps
   `MPC_RightLaneState`'s low bit. Capture `0x316`'s full bit pattern on the
   target car (BLF/MF4, per `community_port_comparison.md`'s update
   procedure) before writing the controller's HUD side; the DBC's `CM_ BO_
   790` comment already flags the specific fields to verify.
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

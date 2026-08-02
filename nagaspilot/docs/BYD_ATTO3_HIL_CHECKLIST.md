# BYD Atto 3 comma 3 HIL checklist

This checklist validates the single BYD test car without broadening EDP10 or
enabling multiple unproven features together. Complete factory-longitudinal
baseline testing before selecting openpilot longitudinal.

## Required setup

- Test car VIN, model year, market, camera and harness identifiers.
- Record whether the driver-camera profile is standard or
  `DISABLE_DRIVER=1`; do not switch profiles within one evidence run.
- Manually synchronized acquisition of factory-MPC road video and raw CAN.
- Original comma 3 with a physical disconnect immediately available.
- Stable 12 V support and a safe, ventilated stationary work area.
- Stock DTC scan before installation.
- CAN capture from buses 0 and 2 on the same acquisition timeline as video.
- `CarParams`, firmware-query responses and Panda health logs.
- Vehicle secured against movement for every stationary test.

Follow [MANUAL_CAPTURE_VALIDATION.md](MANUAL_CAPTURE_VALIDATION.md) for raw
artifact retention, synchronization metadata, scenarios and replay comparison.
Runtime output policy is not part of offline parity assessment. Do not flash a
transmitting build or perform public-road testing merely from this checklist.

## Gate 1: identification and listen-only

1. Capture VIN and `0xF195` responses.
2. Compare the live fingerprint with the one recorded in `fingerprints.py`.
3. Record missing and additional CAN addresses; do not broaden matching yet.
4. Verify bus routing: chassis traffic on bus 0, camera traffic on bus 2.
5. Confirm no unexpected DTC after at least one complete power cycle.

Pass evidence:

- exact fingerprint/FW comparison;
- synchronized factory-MPC video and raw CAN retained outside the repository;
- capture hashes, channel mapping, timing offset/error and drop counts;
- no vehicle-control TX from comma 3;
- before/after DTC report.

## Gate 2: ignition

Capture these transitions while watching Panda `ignition_can`:

| Transition | Expected `0x242` gear | Expected ignition |
|---|---:|---:|
| vehicle asleep/off | absent or `0` | false after timeout |
| powered/ready in Park | `1` | true |
| Reverse | `2` | true |
| Neutral | `3` | true |
| Drive | `4` | true |
| shutdown | absent or `0` | false within the normal two-second timeout |

Reject the gate if byte 5 has unexpected upper bits, valid gear disappears
while ready, ignition remains high after shutdown, or another `0x242` shape can
trigger the detector.

## Gate 3: passive signal scaling

Compare decoded values against independent vehicle observations:

- `0x11F` steering angle at center and known left/right angles;
- `0x1F0` speed at several steady indicated and GPS speeds;
- `0x242` brake and gear transitions;
- `0x342` accelerator deadband;
- `0x32D` ACC available, active, override and off states;
- `0x1FC` EPS states 8, 9, 10 and 11;
- blinkers, doors, belt and blind-spot indications.

Use executable-reference parity (`19.8` steering ratio and the DBC's `0.0713`
speed scale) as the provisional baseline. Compare `14.8`, `19.5`, `19.8`, and a
continuous fit from the same manually synchronized video/CAN dataset; do not tune
steering ratio and speed scale independently. Follow
[BYD_PARAMETER_EVIDENCE.md](BYD_PARAMETER_EVIDENCE.md).

## Gate 4: forwarding with output blocked

With Panda still preventing host TX, confirm:

- all car-to-camera traffic forwards;
- factory camera `0x1E2`, `0x316` and `0x32E` reach the car;
- stock ACC and AEB remain functional;
- enabling/disabling openpilot software state does not change bus traffic;
- relay-malfunction detection does not trigger during normal forwarding.

## Gate 5: stationary lateral

Enable only BYD lateral safety after Gates 1–4 pass and their captures are
reviewed. Keep openpilot longitudinal disabled.

1. Verify idle `0x1E2`/`0x316` bytes and counters before steering authority.
2. Request minimal left/right angles with wheels clear and vehicle secured.
3. Verify requested versus measured angle and the 50-degree safety-error bound.
4. Exercise enable, disable, brake, gas, driver override and standstill.
5. Stop and restart the command stream to verify EPS 9→10 and 10→9 recovery;
   deliberately confirm state 11 is not latched.
6. Disconnect comma 3 and confirm immediate stock behavior.

Any DTC, unexpected wheel motion, checksum rejection, EPS state 11, forwarding
collision, or failed disengagement returns the interface to `noOutput`.

## Gate 6: stationary longitudinal trial

This gate is separate and optional. Factory longitudinal remains the baseline.

1. Select experimental longitudinal only after stationary lateral passes.
2. Verify ordinary factory `0x32E` is replaced only while `longActive`.
3. Verify factory `0x32E` resumes immediately on disengagement.
4. Inject/replay an AEB-grade factory payload (`raw < 20`) and confirm it is
   forwarded even during openpilot longitudinal authority.
5. With propulsion mechanically prevented, verify command bounds, checksum,
   counter, stop-hold and resume bits.
6. Confirm brake and invalid/stale RX disable longitudinal TX.

No moving-vehicle longitudinal trial is permitted until a separate review of
the stationary evidence approves a closed-course procedure.

## Evidence record

For every gate record:

- date, operator and reviewer;
- code commit and Panda firmware hashes;
- vehicle identifiers and DTC reports;
- exact capture filenames and SHA-256 hashes;
- observed versus expected results;
- deviations, follow-up action and final pass/fail decision.

Do not mark a feature supported from memory, screenshots, or console summaries;
retain the raw data needed to reproduce the decision.

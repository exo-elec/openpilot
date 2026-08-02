# BYD Atto 3 CANape capture plan — resolving the fields DBC comments flag

This is a focused, signal-by-signal capture plan for a **passive, listen-only**
CANape session on a normal (stock, unmodified) BYD Atto 3 MPC ADAS car, using
`opendbc/dbc/byd_atto3.dbc`. Its only goal is to resolve the specific fields
that DBC's `CM_ BO_ *` comments currently flag as unverified — it does not
validate the transmit/override path (see "Out of scope" below). Follow
[MANUAL_CAPTURE_VALIDATION.md](MANUAL_CAPTURE_VALIDATION.md) for the general
recording, retention and acceptance-record requirements; this document only
adds the specific signal watch-list and scenarios.

## Setup

- Load `opendbc/dbc/byd_atto3.dbc` (this project's DBC, not
  `~/panda/BYD_Atto3/DBC/byd_atto3.dbc`) into CANape so the Graphic/Measurement
  windows show the exact names this project decides whether to trust.
- Classic CAN, 500 kbit/s, chassis-harness connector pins 4/8 (per
  `BYD_ATTO3_COMMA3_PORT_PLAN.md`). Confirm bus assignment against a known
  message (e.g. `B_0x242_VCU_DriveState_L8_20ms`'s `VCU_Gear`) before trusting
  any capture.
- Record raw (BLF or MF4, per `community_port_comparison.md`'s update
  procedure), not just CANape's live decode — a wrong bit-position hypothesis
  can be re-checked against the same raw capture without a second drive.
- Retain: vehicle/VIN/market, firmware versions, date, scenario list with
  timestamps, and (if available) synchronized road video, per
  `MANUAL_CAPTURE_VALIDATION.md`'s "Ground-truth recording" section.

## Priority signal watch-list

Each row: current status in the DBC, what to do, what a resolved answer
looks like.

### `A_0x316_MPC_MpcState_L8_20ms` (`0x316`)

| Signal | Bits | Status | Scenario | Resolves to |
|---|---|---|---|---|
| `AUTO_LIGHT` | `1\|2@0+` | No firmware witness; kept at the community fork's bit position/endianness | Cycle headlight/auto-light mode via the stalk or settings while watching this field for any change | Either confirm this bit position tracks light state, or find the real one and update `CM_ BO_ 790` |
| `HMA_ON_OFF` | `15\|1@0+` | No firmware witness | Toggle High-Mount-Assist / high-beam-assist in vehicle settings if present | Confirm or replace |
| `LDSW_TYPE` | `49\|2@0+` | No firmware witness | Cycle lane-departure-warning alert type (vibrate/sound/both) in vehicle settings if present | Confirm or replace |
| `MPC_RightLaneState` bit 35 vs `handsOffDetected` | `34\|2@1+` (2-bit field; bit 35 is its low-order bit) | The firmware (`TC275_BrownPanda/DBC/byd_atto3.c` case `0x316`) independently reads bit 35 as a boolean `handsOffDetected`, while the CANape capture defines bits 34-35 as one 2-bit `MPC_RightLaneState` enum with no separate hands-off meaning documented | Run two *independent* scenarios: (1) vary lane marking state (drive over a clear single/double line) while keeping hands on the wheel throughout, watching bit 35 in isolation from bit 34; (2) on a straight road with constant, clear lane markings, release the wheel to trigger the camera's hands-off nag, watching bit 35 again | If bit 35 tracks lane geometry independent of hands state, the firmware's `handsOffDetected` read is likely a misinterpretation - flag that in `byd_atto3.c`'s tracker, not this DBC. If it tracks hands state independent of lane geometry, `MPC_RightLaneState` needs splitting into a real 1-bit lane flag (bit 34) and a separate hands-off bit (35) |
| `MPC_LkasState` low 2 bits (arming pattern) | `36\|4@1+` | Firmware-witnessed for read; the *override* pattern (`(state & 0b1100) \| 0b0010`) is ported from the CarrotPilot-derived reference fork, unvalidated here | Toggle stock LKS off/available/active while watching bits 36-37 | Confirms the passive decode only - the override pattern itself needs an active transmit bench test, see "Out of scope" |

### `B_0x32D_HUD_AdasState_L8_20ms` (`0x32D`)

| Signal | Bits | Status | Scenario | Resolves to |
|---|---|---|---|---|
| `ACC_ON1` | `22\|1@0+` | Firmware doesn't read this bit at all for `0x32D`; the CANape capture defines only a single `VCU_ACCOnPrimary` at the same bit 22 (`@1+`) - no secondary flag anywhere | Engage/disengage stock ACC via steering-wheel button, watch bit 22 alongside `VCU_ACCState`'s transitions | If it tracks ACC-on state, rename to match `VCU_ACCOnPrimary` (Intel) and drop `ACC_ON2` entirely |
| `ACC_ON2` | `20\|1@0+` | **No evidence anywhere** - not in firmware, not in the CANape capture. This is a pure community-fork guess | Watch bit 20 through the same ACC engage/disengage scenario | If it's constant/noise, remove the field; do not keep guessing at a second flag no source defines |

### `B_0x1FC_EPS_MotorState_L8_20ms` (`0x1FC`)

| Signal | Bits | Status | Scenario | Resolves to |
|---|---|---|---|---|
| `TORQUE_FAILED` | `2\|1@1+` | Community-only, no firmware or CANape witness | Apply steering torque until any dash EPS warning appears (safely, at standstill or very low speed) | Confirm or drop |
| `DRIVER_TORQUE` | `4\|12@1-` | Community-only | Apply varying steering wheel torque, compare against `EPS_DriverTorque` on `0x11F` for consistency | Confirm, or note as a duplicate/red herring |
| `TARGET_ANGLE` | `16\|16@1-` | Community-only | Compare against `SAS_SteeringAngle` (`0x11F`) and `MPC_LkasOutput` (`0x316`) while stock LKS is active | Confirm or drop |
| `HANDS_ON_WHEEL_WARN` | `45\|1@1+` | Community-only | Release the wheel to trigger the camera's hands-off nag, watch this bit alongside `0x316`'s hands-off-related fields above | Confirm or drop |
| `TORQUE_TEMP_FAILED` | `46\|2@1+` | Community-only, has a `VAL_` table (`TooLarge`/`TooFast`/`Both`) with no evidence behind the labels | Same scenario as `TORQUE_FAILED` | Confirm, correct, or drop the `VAL_` table |

### `B_0x11F_SAS_SensorState_L5_10ms` (`0x11F`)

`COUNTER` (bits 35-38, Motorola) and `CHECKSUM_4BIT` (bits 39-42) are ignored
in `byd.h`'s RX check (`ignore_counter`/`ignore_checksum`) and have no firmware
witness (the firmware's own comment marks bits 32-39 as one unnamed
`SAS_unknown_checksum_11F` byte, not a split counter+checksum). Low priority -
only worth resolving if a future stage needs to validate this message's
liveness/integrity rather than just its angle/torque values.

## What "resolved" looks like

For each field above, record (per `MANUAL_CAPTURE_VALIDATION.md`'s acceptance
format): the scenario, timestamped raw bytes before/during/after the
triggering event, the CANape-decoded value at each point, and one line stating
what the bit means or that it's unconfirmed/noise. Update the corresponding
`CM_ BO_ *` comment in `opendbc/dbc/byd_atto3.dbc` and, for any bit position or
endianness that changes, `opendbc/car/byd/carstate.py` and the relevant test in
`opendbc/car/byd/tests/test_byd.py` in the same commit - do not let the DBC and
the Python decode drift apart.

## Out of scope for this capture session

This plan is **passive listen-only** and only resolves what the stock car
*reports*. It does not and cannot validate:

- The `0x1E2`/`0x316` **transmit/override** bit patterns in
  `opendbc/car/byd/bydcan.py` (`create_steering_control`, `create_lkas_hud`) -
  those require an active bench session where a transmitted frame competes
  with or replaces the camera's own frame on the bus, per
  `BYD_ATTO3_HIL_CHECKLIST.md`'s later gates, not a passive recording.
- The `0x32E` longitudinal command factors/choreography in `create_acc_cmd` -
  same reason.
- Steering ratio (`14.8` vs `19.5` vs `19.8`) or the wheel-speed scale
  precisely - both need controlled maneuvers and independent ground-truth
  measurement (GPS speed, known steering-lock angle), not just passive
  listening; see [BYD_PARAMETER_EVIDENCE.md](BYD_PARAMETER_EVIDENCE.md).

Resolving the fields above is a prerequisite for writing a validated `0x316`
controller (`MIGRATION_PLAN.md` task 3's blocker) and for closing
`BYD_REFERENCE_PARITY_AUDIT.md`'s open rows, but it is one input among several
- not the full HIL sign-off.

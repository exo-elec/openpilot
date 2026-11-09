# BYD Atto 3 camera-port parity audit

- Audited transition branch: `dev/EDP10`
- Original reference: `/home/vcar/panda/byd-atto3-openpilot-port` commit
  `5b34194240bb831719629d2fd095fae5daaed1e0`
- Later references, added since the first audit: that same fork's
  CarrotPilot-derived revision (0x316/0x32E porting), and
  `~/panda/TC275_BrownPanda/DBC/byd_atto3.c` (the tc275_freertos dev/BYD_ATTO3
  firmware, tested on this project's own target car)
- Audit date: 2026-07-31; updated 2026-08-02 - this update supersedes the
  original "byte-identical" verdict below, see "What changed" first
- Scope: original comma 3, chassis CAN, and the stock forward camera only

## What changed since the original audit

The original audit (2026-07-31) found byte-identical parity with commit
`5b34194`. Since then, `opendbc/dbc/byd_atto3.dbc`, `opendbc/safety/modes/byd.h`,
and `opendbc/car/byd/carstate.py` were **deliberately renamed and bit-corrected**
against the tc275/BYD_Atto3 firmware and CANape capture - at the user's
explicit direction to stop treating the community fork as the naming/protocol
authority, since it is unproven on this project's own (Thailand-market) car.
That work found and fixed a real bit-position bug (`0x1E2`'s `COUNTER`) and
~15 Motorola/Intel bit-numbering errors the community fork's layout carried.
**The DBC is no longer byte-identical to the original reference by design.**
0x316 and 0x32E generation, absent at the first audit, are now implemented -
ported from the fork's *later* CarrotPilot-derived revision, not commit
`5b34194`.

## Verdict

The NagasPilot port has **field-level parity with firmware/capture evidence
where such evidence exists**, and **intentional divergence from the original
community reference's naming and several bit positions**, corrected against
this project's own target-car-tested firmware. Where no firmware or capture
evidence exists for a field (see `opendbc/dbc/byd_atto3.dbc`'s `CM_ BO_ *`
comments and [BYD_CANAPE_OPEN_QUESTIONS.md](BYD_CANAPE_OPEN_QUESTIONS.md)),
the community fork's value is kept as a placeholder, explicitly flagged
unverified - not claimed as parity. **Recording parity** against the target
car is pending the CANape capture in `BYD_CANAPE_OPEN_QUESTIONS.md` followed
by the manually synchronized factory-MPC video/CAN workflow. Runtime
`noOutput`/`dashcamOnly` selection is not a parity criterion; it is a separate
vehicle-activation decision. This audit is not a supported-vehicle or
road-test claim.

Private Veoneer radar CAN-FD decoding is outside the product scope. Its absence
is intentional and is not counted as a parity failure. The factory ACC and AEB
traffic remain vehicle-owned in the default mode; NagasPilot does not consume
radar tracks.

## Parity matrix

| Area | Result | Evidence or reason |
|---|---|---|
| Chassis DBC | **No longer byte-identical, by design** | Message/signal names follow the tc275/BYD_Atto3 convention; ~15 fields corrected from Motorola to Intel bit numbering and 4 real bit-position bugs fixed (`0x1E2` `COUNTER`, `0x242` `RAW_THROTTLE`, `0x418`'s two BSD fields), all against `TC275_BrownPanda/DBC/byd_atto3.c`'s real `ReadRaw`/`WriteRaw` calls - see the DBC's own header `CM_` comment and the two `[BYD]` naming/bit-layout commits. Fields with no firmware or CANape witness keep the community fork's value, explicitly flagged unverified. |
| Fingerprint | Exact (unchanged) | `fingerprints.py` is byte-identical to the original audit; not touched this pass. |
| Firmware inventory/query | Exact data, adapted API (unchanged) | The six observed ECU firmware entries and DID `0xF195` query are retained. Fuzzy matching and query-bypass behavior remain reference differences to evaluate from manual vehicle evidence. |
| Passive vehicle state | Baseline parity, firmware-corrected | Speed, steering angle/torque, EPS state, pedals, gear, blinkers, blind spots, doors, belt, stock ACC state, set speed, and standstill are decoded from the renamed/bit-corrected signals. Three fields (`STALKS`, `PEDAL`, `PCM_BUTTONS`) were switched from the community fork's decode to the firmware's real, car-tested one - a functional change, not just a rename; see the `[BYD] Rename to tc275/BYD_Atto3 naming...` commit. |
| Steering `0x1E2` | Byte-verified against firmware, not the reference | `test_lateral_cmd_matches_firmware_wire_layout` packs a frame and asserts the raw bytes match `byd_atto3.c`'s real `WriteRaw` sequence directly - stronger evidence than reference-fork parity, since it's checked against this project's own target-car-tested firmware. |
| Lane/HUD `0x316` | Coded, not target-car validated | Absent at the original audit (`carcontroller.py` was a no-op stub); now implemented, ported from the fork's *later* CarrotPilot-derived revision (not commit `5b34194`). Passes every stock field through from `CS.lkas_hud` untouched except specific bits that later revision's own on-car captures proved safe. Proven on that fork's vehicle, not this one - see `BYD_CANAPE_OPEN_QUESTIONS.md`. |
| Optional longitudinal `0x32E` | Coded but **cannot transmit** | Absent at the original audit; now implemented (`create_acc_cmd`), same CarrotPilot-derived provenance as `0x316`. `opendbc/safety/modes/byd.h`'s `BYD_TX_MSGS` whitelist does not include `0x32E` - a real panda would reject every frame at the generic safety check before any BYD-specific logic runs. Fail-closed, not silently transmitting, but this row is not "generated-byte parity" as the original audit claimed; that claim predates the controller existing at all. |
| Panda steering safety | Functional parity, statically blocked forwarding | Same angle/rate/error limits and BYD checksum/fixed-field validation. Forwarding is **static** (`check_relay = true` on `0x1E2`/`0x316`), not the reference's dynamic `controls_allowed`-gated hook - `byd_fwd_hook` was removed this session as incompatible with the current safety-test API; see the safety-model commit. |
| Factory ACC/AEB forwarding | **Not implemented, correcting the original audit** | The original audit's "preserves AEB-grade frames below raw value 20" described the port plan's *proposed* payload-aware forwarding (`BYD_ATTO3_COMMA3_PORT_PLAN.md`'s commit-sequence item 5), which was never built. `0x32E` is outside `byd.h` entirely (see the row above) - there is currently no forwarding logic for it at all, payload-aware or otherwise. |
| Reference POC controls | Not yet parity (unchanged) | LCC-button latch, fixed-acceleration probe, rocker behavior, `gearStep`, and diagnostic bring-up controls are not ported. Staged differences, not rejected features. |
| Private radar CAN-FD | Out of scope (unchanged) | No `byd_radar_fd.dbc`, radar interface, Veoneer track parser, or bus-1 monitor dependency. |
| Runtime activation | Outside parity scope (unchanged) | `interface.py` still selects `noOutput`/`dashcamOnly`/`openpilotLongitudinalControl=False`. Output policy does not affect offline parser, generator, safety, or manual replay parity. Vehicle activation is reviewed separately. |

## Reference differences that require vehicle evidence

Private radar is the only reference capability excluded by scope. Other rows
marked different or pending remain candidates for staged parity work; reference
environment variables or fork-specific plumbing may be expressed through the
normal NagasPilot/openpilot configuration instead of copied literally.

These are unresolved measurements, not reasons to copy more reference code:

- Steering ratio remains unresolved: the upstream OpenDBC draft uses `14.8`,
  the reference README reports a `19.5` fit, and the executable reference
  (and this project) use `19.8`. Still needs the manual fit described in
  [BYD_PARAMETER_EVIDENCE.md](BYD_PARAMETER_EVIDENCE.md).
- Wheel-speed scale is **resolved from firmware, not vehicle evidence**:
  `byd_atto3.c` case `0x1F0` uses `0.07142857` exactly. `ESP_VehicleSpeed`
  (DBC) and `byd.h`'s RX decode were both updated to that value, replacing
  the `0.0713` approximation this audit previously carried and the old
  reference safety file's separate `0.0758`. Still wants an independent
  speed-source cross-check before being called target-car confirmed - see
  `BYD_CANAPE_OPEN_QUESTIONS.md`'s "Out of scope" section.
- The reference's `STEER_DRIVER_DISENGAGE = 30` is only defined and never used.
  NagasPilot retains the used soft-override threshold and does not carry the
  dead constant.
- NagasPilot uses the branch's current steering-limit and car-interface APIs.
  Scheduling has the same 50 Hz steering and about 33 Hz longitudinal rates,
  although the steering frame phase differs by one control tick.

## Recording-parity gates

Before the synchronized factory-MPC workflow, resolve
[BYD_CANAPE_OPEN_QUESTIONS.md](BYD_CANAPE_OPEN_QUESTIONS.md)'s open DBC
fields with a passive CANape session against a normal car - no comma
3/panda hardware required. Then establish target-car parity with the
synchronized factory-MPC workflow in
[MANUAL_CAPTURE_VALIDATION.md](MANUAL_CAPTURE_VALIDATION.md). Record factory
video and buses 0/2 CAN together, then replay and cross-check:

1. Passive replay confirms every decoded signal, DBC speed scale, steering
   sign, gear mapping, stock ACC states, checksum, counters, and message rates.
2. Original comma 3 ignition and firmware query work without forced-silent or
   query-bypass changes and without diagnostic trouble codes.
3. Video-aligned factory `0x1E2` and `0x316` transitions establish lane, active,
   driver-override, fault, recovery and idle-stream ground truth.
4. Factory `0x32E` cadence, factors, normal braking, standstill and resume are
   characterized without intentionally provoking AEB.
5. NagasPilot replay reports byte/state mismatches and resolves each one before
   the corresponding feature is considered recording-equivalent.

Until those gates pass, the precise statement is: **camera-port software
baseline parity, target-car recording parity pending**.

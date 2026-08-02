# BYD Atto 3 camera-port parity audit

- Audited transition branch: `dev/EDP10`
- Reference: `/home/vcar/panda/byd-atto3-openpilot-port`
- Reference commit: `5b34194240bb831719629d2fd095fae5daaed1e0`
- Audit date: 2026-07-31
- Scope: original comma 3, chassis CAN, and the stock forward camera only

## Verdict

The NagasPilot port has **source-data and generated-message parity** with the
reference for the intended camera-based BYD Atto 3 baseline. **Recording
parity** against the target car is pending manually synchronized factory-MPC video
and raw CAN. Runtime `noOutput`/`dashcamOnly` selection is not a parity
criterion; it is a separate vehicle-activation decision. This audit is not a
supported-vehicle or road-test claim.

Private Veoneer radar CAN-FD decoding is outside the product scope. Its absence
is intentional and is not counted as a parity failure. The factory ACC and AEB
traffic remain vehicle-owned in the default mode; NagasPilot does not consume
radar tracks.

## Parity matrix

| Area | Result | Evidence or reason |
|---|---|---|
| Chassis DBC | Exact | `byd_atto3.dbc` is byte-identical; SHA-256 `126a607e05ab17b108bbcbe132a7d5c13e3b9d574503b6db43a95c2c8926cbb7`. |
| Fingerprint | Exact | `fingerprints.py` is byte-identical; the single fingerprint has 109 CAN IDs. |
| Firmware inventory/query | Exact data, adapted API | The six observed ECU firmware entries and DID `0xF195` query are retained. Fuzzy matching and query-bypass behavior remain reference differences to evaluate from manual vehicle evidence. |
| Passive vehicle state | Baseline parity | Speed, steering angle/torque, EPS state, pedals, gear, blinkers, blind spots, doors, belt, stock ACC state, set speed, and standstill are decoded from the same messages. |
| Steering `0x1E2` | Generated-byte parity | Active and idle vectors across negative, zero, and positive angles match the reference exactly. |
| Lane/HUD `0x316` | Generated-byte parity | Active and inactive reference vectors match exactly while preserving captured stock fields. |
| Optional longitudinal `0x32E` | Generated-byte and limit parity | Normal, stop-hold, and resume vectors match across braking and acceleration inputs. Controller limits remain -3.0 to +1.5 m/s² inside Panda's -3.5 to +2.0 m/s² envelope. |
| Panda steering safety | Functional parity, stricter payload validation | Same angle/rate/error limits and camera-message replacement; NagasPilot also validates the BYD checksum and fixed command fields. |
| Factory ACC/AEB forwarding | Functional parity | Factory mode forwards `0x32E`. Trial mode replaces ordinary stock ACC only while longitudinal control is allowed and preserves AEB-grade frames below raw value 20. |
| Reference POC controls | Not yet parity | LKAS-button latch, fixed-acceleration probe, rocker behavior, `gearStep`, and diagnostic bring-up controls are not ported yet. They are staged differences, not rejected features. Equivalent behavior should use NagasPilot/openpilot mechanisms where possible. |
| Private radar CAN-FD | Out of scope | No `byd_radar_fd.dbc`, radar interface, Veoneer track parser, or bus-1 monitor dependency. |
| Runtime activation | Outside parity scope | Output policy does not affect offline parser, generator, safety, or manual replay parity. Vehicle activation is reviewed separately. |

## Reference differences that require vehicle evidence

Private radar is the only reference capability excluded by scope. Other rows
marked different or pending remain candidates for staged parity work; reference
environment variables or fork-specific plumbing may be expressed through the
normal NagasPilot/openpilot configuration instead of copied literally.

These are unresolved measurements, not reasons to copy more reference code:

- The upstream OpenDBC draft uses steering ratio `14.8`; the reference README
  reports a `19.5` fit paired with its speed fit; and the reference's executable
  platform code uses `19.8` with the DBC path NagasPilot ports. NagasPilot keeps
  the executable pair (`19.8`, `0.0713`) until manual analysis fits both together. See
  [BYD_PARAMETER_EVIDENCE.md](BYD_PARAMETER_EVIDENCE.md).
- The byte-identical DBC decodes `WHEELSPEED_CLEAN` with `0.0713`, while the
  reference Panda safety file uses `0.0758`. NagasPilot consistently uses the
  DBC scale. A target-car capture and independent speed measurement must decide
  this before enabling output.
- The reference's `STEER_DRIVER_DISENGAGE = 30` is only defined and never used.
  NagasPilot retains the used soft-override threshold and does not carry the
  dead constant.
- NagasPilot uses the branch's current steering-limit and car-interface APIs.
  Scheduling has the same 50 Hz steering and about 33 Hz longitudinal rates,
  although the steering frame phase differs by one control tick.

## Recording-parity gates

Target-car parity is established with the synchronized factory-MPC workflow in
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

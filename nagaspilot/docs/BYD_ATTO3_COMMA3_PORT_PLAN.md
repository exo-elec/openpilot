# BYD Atto 3 on comma 3 port plan

This implementation plan is governed by the
[NGP11.1 conservative branch concept](BRANCH_CONCEPT.md). If a proposed porting
shortcut conflicts with that concept, stability and minimal DragonPilot delta
take priority.

- Status: software port in progress; hardware output remains disabled
- Target branch: `NGP11.1`
- Plan recorded: 2026-07-31

## Objective

Build a minimal BYD Atto 3 integration for original comma 3 hardware while
keeping this branch close to openpilot/DragonPilot 0.11.1. The first usable
version will provide camera-based lateral control and retain the vehicle's
stock ACC, braking, AEB, and radar behavior. A separately selected trial mode
can generate the native BYD longitudinal command from openpilot. Both modes are
validated first through manually synchronized factory-MPC video and raw CAN;
runtime output selection is a separate activation decision.

NagasPilot does not parse or use private radar CAN-FD tracks. “Retain radar
behavior” means leaving the car's factory radar, ACC, and AEB system untouched
in the default mode. See the
[camera-port parity audit](BYD_REFERENCE_PARITY_AUDIT.md) for the exact boundary.

This document is a work plan, not a claim that the vehicle is supported. No
public-road testing is authorized by the presence of this document.

## Sources and ownership

The implementation will combine three clearly attributed sources:

| Source | Role | Rule |
|---|---|---|
| openpilot 0.11.1 | Core controls, car interface and comma 3 runtime | Keep changes from upstream as small as possible. |
| DragonPilot 0.11.1, tag `dragonpilot-0.11.1` (`2d9cae2d8`) | Proven comma 3 base and DragonPilot features | Preserve Rick Lan's `dragonpilot/` namespace, attribution and history. Do not rebrand or rewrite it. |
| [BYD Atto 3 openpilot port](https://github.com/shemps/byd-atto3-openpilot-port), local checkout `/home/vcar/panda/byd-atto3-openpilot-port`, commit `5b34194240bb831719629d2fd095fae5daaed1e0` | BYD CAN observations, DBC, fingerprint, firmware query, state decoding and control messages | Port only the relevant behavior to current APIs. Preserve its MIT notice and third-party attribution to openpilot/OpenDBC, CarrotPilot and BukaPilot. |

New NagasPilot-owned features belong under `nagaspilot/` where possible.
Necessary vehicle registration, OpenDBC and Panda safety integration will use
their normal shared locations, with source attribution kept in code and commit
history. Rick Lan's `dragonpilot/` files are outside the modification scope.

The BYD reference is applied feature by feature under the adoption process and
ledger in [BRANCH_CONCEPT.md](BRANCH_CONCEPT.md). “Proven in the reference car”
is input evidence, not permission to import unrelated reference-fork design.

## Evidence and limitations

The BYD reference was recorded from one 2024 right-hand-drive export BYD Atto
3/Yuan Plus with an `LGX` WMI, Veoneer MVS4 camera and 77V12FLR radar. Its
fingerprint and ECU firmware list must not be treated as universal coverage for
all model years, markets or trims.

Observed camera-connector topology:

- chassis CAN: classic CAN at 500 kbit/s on connector pins 4 and 8;
- private radar CAN: CAN-FD on connector pins 3 and 7;
- diagnostic firmware query: DID `0xF195`; the usual `0xF188` query received a
  negative response on the observed vehicle.

Known measurements requiring confirmation on the target car:

- Published implementations disagree on steering ratio: upstream OpenDBC uses
  `14.8`, the reference README reports a paired `19.5` drive fit, and its
  executable code uses `19.8`. NagasPilot preserves the executable reference's
  `19.8`/DBC-speed pair until manual analysis fits both parameters together. See
  [BYD_PARAMETER_EVIDENCE.md](BYD_PARAMETER_EVIDENCE.md).
- The observed cluster-speed scaling of `0.758` may be specific to that car or
  signal interpretation. Compare decoded wheel/vehicle speed with independent
  measurements before using it for control.
- The supplied fingerprint and six firmware versions represent only the
  observed car. Capture the target car before extending matching rules.

## Phase-one boundary

Phase one includes:

- original comma 3 hardware and its native road-camera pipeline;
- wide and narrow road cameras as already supported by this branch;
- standard vision driver monitoring when the driver camera is present, plus an
  explicit `DISABLE_DRIVER=1` wheel-touch fallback profile when it is absent;
- BYD chassis-CAN parsing and firmware identification;
- passive vehicle-state reporting;
- angle-based lateral steering through message `0x1E2`;
- stock-field-preserving lane/HUD output through message `0x316`;
- stock ACC-based engagement and disengagement;
- Panda safety support for both the standard and `panda_tici` firmware trees;
- existing DragonPilot ALKA/LCA/road-edge behavior only after baseline lateral
  control is safe and stable.

The initial factory-longitudinal capture baseline does not yet activate:

- openpilot longitudinal control and BYD acceleration message `0x32E`;
- modification, suppression or synthesis of stock AEB commands;
- Veoneer private-radar decoding and CAN-FD monitor changes;
- proof-of-concept button latches, fixed acceleration probes, `gearStep`, and
  CarrotPilot-specific cruise plumbing;
- reference environment-variable safety/control switches and diagnostic or
  firmware-query bring-up workarounds;
- EOP10 RKNN, RGA, MPP, V4L2, webcam, external IMU, stereo, Radar4D, GridD,
  PathD, TLSC or other RK3576/RK3588 hardware paths;
- Dashy and unrelated branding or UI expansion.

Of the BYD reference capabilities, only private radar is excluded from scope.
The POC, UI, query, and diagnostic items above are staged after the manual
factory baseline. Their behavior may be adapted to normal NagasPilot/openpilot
configuration rather than retaining the reference fork's exact plumbing.

### Comma 3 hardware profiles

| Profile | Cameras | CAN | Driver monitoring |
|---|---|---|---|
| Standard | wide road, narrow road, driver | classic chassis/camera CAN | vision policy with normal wheel-touch fallback |
| No driver camera | wide road and narrow road | identical classic CAN path | explicit no-face samples select the existing wheel-touch policy |

Set `DISABLE_DRIVER=1` before manager/camerad starts for the second profile.
This uses camerad's existing per-camera disable path; it does not enable webcam,
private CAN-FD, radar tracks, or EOP camera infrastructure. Monitoring thresholds
and terminal-alert behavior remain the upstream values.

### Longitudinal mode selection

The software now models two mutually exclusive modes:

| Mode | Engagement source | `0x32E` source | Current gate |
|---|---|---|---|
| Factory longitudinal (default) | BYD stock ACC state | BYD factory camera, forwarded unchanged | `noOutput`, pending hardware validation |
| Openpilot longitudinal trial | BYD stock ACC state (`pcmCruise`) | Openpilot planner using the native BYD message | Explicit experimental-longitudinal selection plus `noOutput`, pending hardware validation |

Openpilot-longitudinal mode does not use the reference port's environment
variables, fixed-acceleration probe, POC button latch, or rocker repurposing.
Panda permits `0x32E` only under the longitudinal safety flag, while controls
are allowed, with a hard -3.5 to +2.0 m/s² envelope. The controller uses a
narrower -3.0 to +1.5 m/s² comfort envelope and bounded command jerk.

The shared forwarding API has an additive payload-aware hook so ordinary stock
ACC `0x32E` frames can be blocked during openpilot control while factory
AEB-grade frames below raw value 20 (harder than -4.0 m/s²) remain forwarded.
Existing safety modes retain their address-only forwarding hooks.

## BYD CAN baseline

The reference maps these chassis messages:

| Address | Initial use |
|---|---|
| `0x11F` | steering angle and driver torque |
| `0x1F0` | vehicle speed |
| `0x1FC` | EPS state |
| `0x242` | drive state, gear and brake |
| `0x316` | stock lane/HUD state and controlled HUD output |
| `0x32D` | stock cruise state |
| `0x342` | accelerator pedal |
| `0x3B0` | steering-wheel buttons |
| `0x418` | blind-spot state |
| `0x1E2` | controlled steering-angle command |

The chassis transmit checksum observed by the reference is the inverted sum of
the first seven data bytes:

```text
(~sum(data[0:7])) & 0xff
```

Both the DBC interpretation and checksum need deterministic tests before any
transmit path is enabled.

## Implementation stages

### 1. Data and provenance

- Add the chassis DBC, platform metadata, `0xF195` firmware query, the observed
  fingerprint and firmware versions.
- Mark the fingerprint as single-vehicle evidence.
- Add checksum and DBC parser tests.
- Keep the platform unregistered or passive until its safety model exists.

Exit gate: DBC generation/checking passes, captured frames decode consistently,
and every copied or adapted component has an attribution trail.

### 2. Passive vehicle state

- Port steering angle, driver torque, EPS state, speed, gear, brake, accelerator,
  stock cruise, blinkers, blind spots, doors and seat belt.
- Adapt to the current 0.11.1 car-interface API, including `dp_params`.
- Keep reference POC engagement, rocker-button cruise modification, `gearStep`
  and bring-up switches out of the passive decoder; track them as later,
  independently tested parity stages.
- Declare radar unavailable to openpilot and leave the stock radar/ACC system in
  control of longitudinal behavior.

Exit gate: replay/parser tests pass, values agree with the target car, no CAN
transmit is allowed, and listen-only operation creates no DTCs.

### 3. Lateral controller and HUD

- Generate angle command `0x1E2` at 50 Hz using the current
  `apply_steer_angle_limits_vm` API and measured vehicle parameters.
- Continue sending a validated idle command with `STEER_REQ=0` while controls
  are enabled but lateral control is inactive or the vehicle is stopped. The
  reference reports that stopping the stream can latch EPS state 11.
- Copy stock `0x316` fields and override only validated arming/lane fields.
- Retain stock `0x32E`, ACC and AEB traffic unchanged.

Exit gate: message-byte tests, checksum tests, rate-limit tests and recorded-CAN
comparisons pass without connecting the transmit path to a moving vehicle.

### 4. Panda safety

- Allocate a new stable BYD safety-model ID after checking the complete current
  enumeration. ID 35 is already used by MG and must not be replaced.
- Implement the current OpenDBC safety API rather than copying the reference
  fork's packet-aware forwarding API.
- Permit only the phase-one steering and HUD transmit messages.
- Enforce steering-angle limits, rate limits, real-versus-commanded error,
  driver override, RX frequency/liveness, stock-ACC state, brake and accelerator
  disengagement, and relay-malfunction handling.
- Use DragonPilot's existing `lat_control_allowed()`/ALKA mechanism where
  appropriate. Start with ordinary stock-ACC lateral control; enable ALKA as a
  separate subphase.
- Forward stock longitudinal and AEB traffic untouched. Dynamically block only
  the stock camera steering/HUD messages when NagasPilot has lateral authority.

Required safety tests include controls-disabled rejection, malformed checksum,
stale RX, excessive angle, excessive rate, excessive angle error, brake/gas
disengagement, driver override, relay fault, forwarding in both engagement
states, idle steering frames, and ALKA state transitions.

Exit gate: the complete Panda safety suite passes for both host safety tests and
the relevant firmware build paths.

Current software status: BYD safety model ID 36 and focused host tests are
implemented. Factory mode allows only `0x1E2` and `0x316`; longitudinal flag 1
additionally allows bounded `0x32E`. The vehicle interface intentionally still
selects `noOutput`, so neither mode can reach vehicle actuators yet.

### 4b. Openpilot longitudinal trial

- Select through the normal experimental-longitudinal input (`alpha_long`).
- Keep stock BYD ACC state as the engagement authority (`pcmCruise=true`).
- Generate `0x32E` at approximately 33 Hz only while `longActive`.
- Preserve measured BYD factor patterns, stop-hold and resume fields.
- Clamp planner acceleration and add a second command-side jerk limiter.
- Never suppress an AEB-grade factory `0x32E` payload.

Exit gate: controller byte tests, longitudinal safety bounds, gas/brake exits,
ordinary-frame replacement, AEB pass-through and stale-state tests pass before
any stationary hardware trial. On-road tuning is not authorized by host tests.

### 5. Original comma 3 integration

- Build and test the shared safety implementation through both `panda/` and
  `panda_tici/`.
- Add BYD ignition detection to both board-specific CAN paths if captured data
  confirms that `0x242` drive state is required.
- Preserve the normal `pandad_tici` ELM327/firmware-query lifecycle. Do not copy
  the reference's forced-silent or `SKIP_FW_QUERY` workaround unless target-car
  evidence proves it necessary and it receives a separate safety review.
- Do not change camera, sensor or model pipelines. Original comma 3 wide/narrow
  road-camera support is the ground truth.

Exit gate: both firmware targets build, ignition transitions correctly, firmware
query completes, relay forwarding is correct, and no unexpected DTC appears.

Current software status: standard Panda and `panda_tici` firmware builds pass.
Both ignition paths recognize the reference vehicle's bus-0 `0x242`
`DRIVE_STATE.GEAR`, narrowed to values 1 through 4 with unused upper bits clear
to reduce collisions with unrelated `0x242` messages. Host tests pass; actual
comma 3 power-on, ready, park, shutdown, and two-second timeout transitions must
still be captured on the target vehicle.

### 6. Bench, HIL and controlled-vehicle validation

Use the [manual factory-MPC validation workflow](MANUAL_CAPTURE_VALIDATION.md)
followed by the feature-by-feature
[BYD Atto 3 comma 3 HIL checklist](BYD_ATTO3_HIL_CHECKLIST.md) and retain the
raw evidence it requires.

Validate in this order:

1. Offline DBC, parser, controller and Panda safety tests.
2. Record manually synchronized factory-MPC video plus raw buses 0/2 CAN, then
   replay it offline with no hardware transmit.
3. Powered bench and harness validation in listen-only mode.
4. Stationary target-car validation with steering output safety-blocked.
5. Stationary low-authority steering validation with an immediate hardware
   disconnect available.
6. Closed-course, low-speed testing with a qualified driver and observer.

Record bus routing, firmware responses, checksums, command frequency, EPS states
8/9/10/11, driver override, standstill behavior, disengagements and DTC scans.
Any mismatch returns the port to the previous non-transmitting stage.

### 7. DragonPilot feature compatibility

After normal stock-ACC lateral operation is validated, test DragonPilot ALKA,
LCA and road-edge behavior through small NagasPilot-side integration changes.
Do not modify the original `dragonpilot/` implementation merely to make BYD
fit. Each feature must be removable without changing the baseline BYD port.

### 8. Deferred work

Private Veoneer radar and selected EOP10 ideas remain deferred. Openpilot
longitudinal is implemented only as an explicit, hardware-gated trial path; it
must not be hidden behind runtime environment switches.

The detailed EOP10 comparison is recorded in
[EOP10 feature-port audit](EOP10_FEATURE_PORT_AUDIT.md). In particular, EOP10
DLAT and DLON must not replace the baseline DragonPilot controls:

- DLAT does not currently alter the steering path and references model fields
  absent from the 0.11.1 schema;
- DLON cannot affect a BYD that retains stock longitudinal control, and its
  heuristic force-stop behavior is outside the phase-one safety boundary;
- DragonPilot AEM, ALKA, LCA and conservative road-edge lane-change blocking
  remain the baseline;
- a minimal vision turn-speed controller may be reconsidered only after BYD
  openpilot longitudinal control is independently implemented and validated.

## Proposed commit sequence

Keep every commit buildable and reviewable:

1. `[BYD] Add Atto 3 metadata, DBC and observed fingerprints`
2. `[BYD] Add passive vehicle-state decoding`
3. `[BYD] Add lateral steering and HUD message generation`
4. `[SAFETY] Add lateral-only BYD safety model and tests`
5. `[SAFETY] Add payload-aware BYD AEB pass-through`
6. `[BYD] Add opt-in openpilot longitudinal trial path`
7. `[PANDA] Add verified BYD ignition support for panda and panda_tici`
8. `[BYD] Register platform with stock-ACC engagement`
9. `[NGP] Add separately tested BYD ALKA/LCA compatibility`
10. `[BYD] Record bench, HIL and closed-course validation results`

Do not squash these stages into an unattributed import. The history should make
it possible to compare each BYD-derived change with commit `5b34194`, distinguish
NagasPilot adaptations, and revert one capability without disturbing the rest.

## Completion criteria for the minimal port

The phase-one port is complete only when:

- DragonPilot sources remain unmodified and attributable;
- the vehicle matches by verified fingerprint/firmware evidence without unsafe
  broad matching;
- all parser, controller and Panda safety tests pass;
- both comma 3 Panda firmware paths build and pass their relevant tests;
- steering and HUD traffic meet the captured checksum, counter and frequency
  behavior;
- stock ACC and AEB remain operational and unmodified;
- steering ratio, speed scaling and safety limits have measured justification;
- bench, stationary and closed-course results are recorded; and
- the documentation clearly distinguishes validated behavior from deferred or
  experimental work.


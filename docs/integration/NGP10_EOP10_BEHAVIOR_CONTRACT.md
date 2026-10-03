# NGP10 to EOP10 Behavior Contract

## Purpose

Keep the validated NGP10 driving concepts and their published outputs as the
baseline for EOP10, while allowing hardware-specific implementations and
additional EOP10 concepts. Code may differ between the branches when that is
needed to preserve the same behavior on different hardware.

## Ownership rule

- A behavior that needs only inputs already available on the comma device is
  developed and validated in NGP10 first. EOP10 carries that behavior forward.
- A behavior that requires EOP10 hardware or sensors is implemented in EOP10.
  It consumes normalized, timestamped observations and cannot silently change
  the baseline behavior when its inputs are absent or stale.
- A new driving concept is introduced at the first branch that can validate
  its inputs and outputs. It is not moved upstream merely because the source
  code could technically run there.
- 01M and 02M hardware and display adaptations preserve the same behavior
  contracts unless a separately identified, validated concept change is made.

## Branch layer ownership

| Branch | Starts here | Keep this boundary |
|---|---|---|
| NGP10 | Proven comma-device driving policy: DLON, DLAT, and portable ALCC policy using existing vehicle/model/calibration signals. | Keep policy that can be meaningfully exercised with NGP10 inputs here first. |
| EOP10 | The hardware-enabled ADAS foundation: `pathd`, `stereod`, `gridd`, `monod`, TR13 BLE corner radar, and driving rules that consume their camera, stereo, radar, or IMU observations. | Keep camera/sensor acquisition and sensor-dependent rules here; expose normalized observations and gate them on freshness/validity. |
| 01M | EOP10-based RK3588 device/UI, screen, and calibration adaptation. | Preserve EOP10 ADAS behavior while adapting the device experience. |
| 02M | EOP10 → 01M lineage adapted to RK3576; ATR24 WiFi corner radar starts here. | Keep RK3576/display changes and 02M-only sensors here. RTK corrections also belong here because of the ZED-F9P, but an NTRIP/RTCM daemon is not implemented yet. |

TR13 is the EOP10 BLE baseline; ATR24 is the 02M WiFi upgrade. `gridd` and
`monod` are EOP10 services, and `pathd` begins with the EOP10 sensor-backed
ADAS layer. DLON/DLAT begin in NGP10 and remain the portable control-policy
baseline. The RTK boundary names the first hardware layer that can support the
feature; it does not claim that correction service is already operational.

## Contracts to preserve

The implementation can vary, but these externally meaningful outputs must
remain compatible unless a deliberate concept change is documented:

- `longitudinalPlan`: target speed/acceleration, stop decision, and lead
  handling semantics.
- `modelV2` lane-change state and direction consumed by `desire_helper.py`.
- `carControl`: actuator meaning and safety limits delivered to the vehicle
  interface.
- Driver-visible alerts and status messages associated with each feature.

Hardware adapters own capture, timestamps, calibration, and coordinate-frame
conversion. Shared driving logic consumes the normalized messages above and
does not inspect RK board identity or device-specific camera node paths.

## Current migration review points

These are places where EOP10 currently mixes shared behavior with EOP-specific
logic and should be handled as separate parity items, not moved as whole files:

- `selfdrive/controls/lib/longitudinal_planner.py` replaces NGP10's `NGPDLON`,
  `NGPVTSC`, `NGPLeadHandoff`, and `NGPSpeedPolicy` use with EOP implementations
  and adds map, surface, road-condition, traffic-light, and other policies.
  Compare each feature's inputs, priority, and resulting `longitudinalPlan`
  before deciding whether to reuse, adapt, or keep the EOP implementation.
- `selfdrive/controls/lib/longitudinal_mpc_lib/long_mpc.py` and
  `selfdrive/controls/lib/longitudinal_planner.py` import acceleration bounds
  through the Tesla vehicle module. The bound values and plan output must be
  preserved while deciding whether their source belongs to a generic vehicle
  contract or to the EOP vehicle adapter.
- `selfdrive/controls/controlsd.py` combines standard control with EOP-only
  ALCC, AEB, radar-zone, stereo/path, and adaptive-driving behavior, and uses
  the EOP vehicle daemon. Keep the actuator interface platform-owned; review
  each added behavior separately against the `carControl` contract.
- `selfdrive/controls/lib/desire_helper.py` combines the shared lane-change
  state machine with EOP gap, lane-width, blind-spot, and road-edge checks.
  Preserve NGP lane-change transitions and outputs; add EOP observations as
  explicit, gated inputs.

## Integration fix applied

`controlsd.py` now gives ALCC the shared `Events` collection populated from the
current `onroadEvents` snapshot, and subscribes to `pandaStates` before passing
them to ALCC. The Panda state is used only when the SubMaster checks say it is
valid and fresh. This repairs the interface mismatch where ALCC received a
plain list even though it reads and mutates event names and checks event
categories. The change preserves ALCC's intended decisions and the standard
`carControl` output contract.

## Implementation sequence

1. Record the NGP10 behavior and output for one feature as the reference.
2. Identify the feature's required signals, units, timestamps, and validity
   rules.
3. Keep the NGP implementation as-is when it already expresses the desired
   concept. When EOP needs a different implementation, adapt it at the input
   boundary and compare the output contract.
4. Add hardware-dependent behavior as an independent, gated contribution. If
   its observation is unavailable or stale, it must not fabricate an input or
   corrupt the baseline output.
5. Document intentional behavior changes separately from hardware ports, then
   carry only the appropriate commits down `NGP10 -> EOP10 -> 01M -> 02M`.

This contract does not assert that all NGP10 features are already proven on
comma hardware or that all EOP10 feature implementations currently satisfy
these conditions. Those claims require feature-level review and validation.

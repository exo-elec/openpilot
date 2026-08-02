# NGP10 EOP10 Feature Plan for Comma 3

## Purpose

`dev/NGP10` is the application-level proving line for EOP10 behavior. It is
based on openpilot v0.10.0 and targets original comma 3 hardware with only the
wide-road and narrow/road cameras. EOP10 hardware/HAL work remains separate.

The comma 3 camera geometry must be calibrated for the installed pair and the
project's `D = 80 mm` reference. Treat 80 mm as a measured geometry parameter,
not as permission to import EOP10's stereo array. Never use EOP10's RK3588
camera defaults or its four-camera stereo assumptions.

## EOP10 audit findings

- **DLAT** (`selfdrive/controls/lib/dlat.py`) uses weighted lane-line
  confidence, predicted-path deviation, curve detection, and hysteresis. Its
  `predictedPath`, `predictedPathStd`, and orientation fields must be checked
  against the v0.10.0 `ModelDataV2` schema before integration.
- **DLON** (`selfdrive/controls/lib/dlon.py`) switches between ACC/Chill and
  Experimental/E2E using slow-lead, low-speed, turn-signal, stop-prediction,
  curve, navigation, and speed-limit triggers. Several EOP parameters and
  radar assumptions are optional and must not become user-facing knobs by
  default.
- **GridD/multi-camera fusion** assumes RK3588 road, wide, and stereo cameras,
  RGA, and a seven-camera platform. It is not a comma 3 port target.
- **StereoD/side/rear cameras** and `system/hardware/rk3588` are HAL/platform
  code and are excluded from NGP10.

## Implementation sequence

1. **Camera contract**: document wide/narrow stream names, resolution, frame
   timing, lens calibration, and the D=80 mm installation geometry. Validate
   projection and calibration on recorded frames before changing model input.
2. **DLAT shadow mode**: keep the existing non-controlling arbiter; add
   v0.10.0-compatible adapters for lane probabilities, predicted path, path
   standard deviation, and road-edge availability. Log the suggestion only.
3. **DLAT replay gate**: test lane confidence weighting, 1 s entry and 2 s
   recovery hysteresis, missing-camera behavior, and no sudden mode changes.
4. **DLON decision layer**: port only pure trigger evaluation and hysteresis.
   Factory/stock longitudinal behavior remains default; no automatic E2E
   actuator selection until route replay proves the decision layer.
5. **DLON safety gate**: test slow-lead, low-speed, turn-signal, stop
   prediction, curve, and speed-limit triggers with absent radar and absent
   navigation data. Keep emergency/AEB handling in the stock path.
6. **Promotion to EOP10**: move only commits that pass NGP10 unit/replay tests;
   validate HAL, packaging, camera transport, and hardware behavior in EOP10.

## Comma 3 constraints

- No RK3588, RGA, stereo, side/rear camera, radar, or CAN-FD dependencies.
- Wide and narrow cameras are the only perception inputs; missing one must
  degrade safely to the stock v0.10.0 behavior.
- Driver monitoring stays enabled and cannot be bypassed by DLAT/DLON.
- Do not enable steering or longitudinal output from a new feature until Panda
  safety, stationary CAN checks, and recorded-route evidence pass.

## Exit criteria

NGP10 is ready for EOP10 promotion only when focused tests pass, v0.10.0 schema
compatibility is demonstrated, both camera profiles are replay-tested, and
DLAT/DLON decisions are observable without changing actuator commands.

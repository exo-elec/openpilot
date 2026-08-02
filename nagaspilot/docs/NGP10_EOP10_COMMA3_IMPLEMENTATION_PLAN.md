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
- **VTSC** (`selfdrive/controls/lib/vtsc.py`) is a camera/model-based curve
  speed candidate. It uses `orientationRate` and `velocity`, learned
  per-curvature comfort limits, and a state machine; it is the best next EOP
  feature to evaluate after DLAT/DLON, but its deceleration output must remain
  shadow-only until replay proves it cannot fight stock longitudinal control.
- **MTSC** (`selfdrive/controls/lib/mtsc.py`) consumes MapD/OSM curvature in a
  150--500 m lookahead. Keep its pure curvature calculation available for
  testing, but do not require maps or GPS for comma 3 and do not enable it by
  default.
- **Speed-limit modules** (`mslc.py`, `nslc.py`, and
  `speed_limit_resolver.py`) can be audited as input resolution, but any
  automatic cruise-speed change must respect the fixed CITY/HIGHWAY policy and
  stock driver-monitoring events.
- **ALCC and LCA are separate EOP10 features.** `alcc.py` implements **ALCC
  (Always Lane Centering Control)** inside `controlsd`; it is not called ALKA
  in EOP10. `desire_helper.py` and `lc_lead_handoff.py` implement **LCA (Lane
  Change Assist)**. ALCC is the lateral-centering execution layer, while LCA
  handles turn-signal lane changes and gap/BSM checks. The adjacent-lead
  handoff is camera-only in EOP10, but depends on `leadsV3`, lane-change state,
  and longitudinal MPC; port only after core lane-change safety tests exist.
- **AEB/RED/traffic helpers** (`aeb.py`, `red.py`, `tlsc.py`, `cslb.py`) must
  remain stock safety paths until their v0.10.0 message fields and event
  semantics are verified.

## Full `selfdrive/` tree audit

The EOP10 tree contains more than controllers. The following additions are
explicitly classified before any port:

| EOP10 area | Examples | NGP10 decision |
| --- | --- | --- |
| Application control | `controls/lib/*`, `plannerd.py` | Candidate only after v0.10.0 API and replay tests |
| Camera/perception | `gridd/`, `monod/`, `pathd/`, `pointcloudd/` | Defer stereo/depth/BEV; retain only two-camera-compatible pure logic |
| Model runtime | `modeld/vision/*`, `rknn_*` | Do not port RKNN/NPU runners; keep v0.10.0 modeld |
| Radar/perception | `radar4d*`, `radar3d.py`, `radar_zones.py` | Excluded; project is camera-only |
| Platform daemons | `steamd/`, `sided/`, `stereod/`, `reard/`, `adaptd/` | Portable contracts may be implemented; hardware daemons are EOP10-only |
| Navigation/map | `mapd/`, `navd/`, `coordinationd/` | Audit as optional inputs; no required dependency for core control |
| Calibration/location | `camera_calibrationd.py`, `side_camera_calibrator.py`, `locationd/*` | Keep stock calibration; no side-camera calibration |
| Vehicle/diagnostics | `obd2d/`, `pandad/` changes | Port only BYD/Panda safety changes already proven separately |
| UI/assets/recording | `ui/`, `assets/`, `recordd/` | Do not import EOP branding or hardware-specific recording paths |

In particular, EOP10's `gridd` uses four MIPI cameras plus optional USB cameras,
`monod`/`stereod` assume depth or stereo, `pathd/soc.py` assumes richer object
geometry, and RKNN files target RK3588. None are direct comma 3 ports.
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

## Feature triage

| Feature | NGP10 action | Camera 3 suitability |
| --- | --- | --- |
| DLAT | Implement shadow arbiter, then replay gate | Wide + narrow model outputs |
| DLON | Implement trigger/state decision only | Model, car state; radar optional |
| VTSC | Next candidate; shadow speed target | Wide + narrow `modelV2` |
| MTSC | Test pure math only; map input optional | Not camera-only by itself |
| Speed limits | Resolve sources, no forced output initially | Dashboard/nav/map dependent |
| ALCC | Audit state machine; preserve stock engagement and DM | Requires vehicle/cereal validation |
| LCA | Preserve human-nudge default; audit gap/BSM gates | Requires vehicle/cereal validation |
| SOC/MonoD/GridD/stereo | Capability-gated implementation | Comma 3 diagnostics/fallback; EOP10 enables extra streams after geometry/resource tests |

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

<div align="center" style="text-align: center;">

<h1>ExoPilot</h1>

<p>
  <b>A fork of openpilot v0.10.0 for our own ExoPilot device, with more cameras, radar and safety features.</b>
</p>

<h3>
  <a href="#-branches">Branches</a>
  <span> · </span>
  <a href="#-how-to-install">Install</a>
  <span> · </span>
  <a href="docs/CARS.md">Cars</a>
  <span> · </span>
  <a href="docs/SAFETY.md">Safety</a>
  <span> · </span>
  <a href="https://github.com/exo-elec/openpilot/issues">Issues</a>
</h3>

[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)
[![Branch](https://img.shields.io/badge/Branch-dev%2FEOP10-blue)](https://github.com/exo-elec/openpilot/tree/dev/EOP10)
[![Last Updated](https://img.shields.io/badge/Last%20Updated-October%209th%2C%202026-brightgreen)](https://github.com/exo-elec/openpilot/commits/dev/EOP10)
[![Issues](https://img.shields.io/github/issues/exo-elec/openpilot?label=Issues)](https://github.com/exo-elec/openpilot/issues)

</div>

------

Using ExoPilot in a car
------

To use **ExoPilot** in a car, you need four things:

1. **Device:** an **ExoPilot 01M or 02M device**, with its matching build profile.
2. **Software:** the matching branch (`dev/EOP10`, `dev/01M`, or `dev/02M`). See *How to install* below.
3. **Car:** a supported car. See [docs/CARS.md](docs/CARS.md).
4. **Harness:** the harness that matches your car's make and model, to connect the device to the car.

ExoPilot has not been tested on a real vehicle yet. Read *Safety and legal* before you drive.

------

**ExoPilot** is based on **openpilot v0.10.0** (the full release, not v0.10.1 or later) and shares its runtime across the **ExoPilot 01M and 02M devices**. It is in development and has not yet been tested on a real vehicle.

Feature comparison: openpilot → NGP10 → EOP10 → 01M → 02M
------

Comparison reviewed **2026-10-09** against this repository. The reference is **official openpilot v0.10.0**, not current upstream: baseline commit `c085b8af19438956c15592828bd082803f43dfaf`. Later tinygrad/Chestnut integration does not upgrade the whole driving stack to a later openpilot release.

**Base** means provided by the official baseline. **Carried** means inherited from the preceding branch. **Extended** means inherited behavior plus branch-specific integration. **Added**, **Replaced** and **Removed** identify changes to that baseline. **Optional/experimental** describes implementation availability, not road validation. A settings key alone does not establish working end-to-end support.

The maintained ancestry is `openpilot v0.10.0 → dev/NGP10 → dev/EOP10 → dev/01M → dev/02M`. EDP10 is a comparison reference, not a parent. 01M and 02M carry the EOP feature implementation; their build profiles select presentation and hardware defaults rather than independent control policies.

### Platform, vehicle integration and display

| Capability | openpilot v0.10.0 | NGP10 | EOP10 | 01M | 02M |
|---|---|---|---|---|---|
| Platform | Base: comma hardware | Carried: comma 3/3X target | Replaced: Exopilot Rockchip HAL | Carried; RK3588 default | Carried; RK3576 default |
| Vehicle interface | Base: card + OpenDBC | Carried: card + pinned OpenDBC | Replaced: vehicled/socketd + BrownPanda gateway | Carried | Carried |
| External OpenDBC dependency | Base | Carried, pin `4b203ff5` | Removed; owned car schema and minimal MIT-derived protocol | Carried removal | Carried removal |
| Vehicle dynamics math | Base bicycle model | Carried into shared portable NGP module | Carried with adapter for incomplete vehicle parameters | Carried | Carried |
| BYD / gateway support | Vehicle-dependent baseline support | OpenDBC vehicle support; no Exopilot gateway ownership | Added BrownPanda integration; supported firmware/harness required | Carried | Carried |
| UI backend | Base: native Qt/C++ | Carried native Qt/C++ with NGP settings | Carried native Qt/C++ with EOP settings | Replaced presentation: PyQt5 | Carried PyQt5 |
| Logical display target | Base device geometry | Comma device geometry | 1024 × 600 default | 1024 × 600 | Extended width: 1600 × 600 |
| Visual style | Base openpilot | NGP native presentation | EOP native presentation | Nagasware tone, shared top/bottom bars and icons | Carried same tone, components and icons |
| Driving layout | Base full view | Carried | EOP native view | Full view; no floating side widgets | Extended: left/right floating widgets at ≥1280 px |
| Offroad / onboarding / settings | Base native flows | Extended NGP settings | Extended EOP settings | Shared PyQt home, onboarding, tabbed settings | Carried; adaptive sizing, same pages |
| Centered animated voice card | Not this implementation | Not provided | PyQt animation not used by native backend | Added shared 440 × 220 card | Carried same card and size, centered in wide view |

Display values are logical build defaults, not certification of a physical panel. Runtime detection and configured hardware still govern available devices. BLE surround radar belongs to EOP10 and is inherited by both variants; it is not a feature introduced only by 02M.

### Cruise, braking and speed policy

| Abbreviation / behavior | openpilot v0.10.0 | NGP10 | EOP10 | 01M | 02M |
|---|---|---|---|---|---|
| ACC — adaptive cruise / longitudinal planner | Base, supported-car dependent | Carried + custom policy | Extended vehicle/radar adapters | Carried EOP | Carried EOP |
| EXP — manually selected experimental driving | Base | Carried | Carried with platform model backend | Carried | Carried |
| DLON — dynamic longitudinal profile | No fork arbitration | Added cruise/end-to-end arbitration | Carried portable policy + EOP integration | Carried | Carried |
| TJA / speed-zone shaping | No fork-specific implementation | Added low-speed, acceleration and jerk policies | Carried/extended integration | Carried | Carried |
| BRSC — bumpy-road speed controller | No fork controller | Added roughness speed policy; default enabled | Carried/extended inputs | Carried | Carried |
| VTSC — vision turn speed controller | No fork controller | Added camera-curvature speed cap | Carried | Carried | Carried |
| NSLC — navigation speed limit control | No fork controller | Added optional consumer; no active NGP route publisher | Extended navigation inputs; needs valid route/limits | Carried | Carried |
| MTSC / MSLC — map turn / map speed-limit control | No fork controllers | Added optional experimental map consumers | Carried; needs fresh map/location data | Carried | Carried |
| TLSC — traffic-light speed control | No fork controller | Added optional experimental consumer; needs perception | Extended perception integration; not validated traffic-light autonomy | Carried | Carried |
| RCD / SQSC — road condition / surface quality | No fork controllers | RCD policy port exists; no Exopilot surface hardware | Extended surface inputs; sensors/models required | Carried | Carried |
| Acceleration / following gap / drive modes | Base personality controls | Extended profiles, adaptive gap, Eco/Normal/Sport, offsets | Carried/extended settings integration | Carried | Carried |
| SAM forced deceleration | Base camera-monitoring control path | Replaced producer; shared awareness-to-forceDecel hooks | Carried; final zero speed target overrides positive offsets | Carried | Carried |
| DDSC — driver-decay speed cap | No fork-specific cap | Optional extra policy, separate from mandatory SAM response | Carried policy | Carried | Carried |
| RSS AEB — radar emergency-braking request | No equivalent fork feature; baseline FCW remains distinct | Not added as EOP radar AEB | Added optional, default off; confirmed forward UART lead required | Carried | Carried |

These are controller paths, not promises of braking authority on every vehicle. OEM safety limits, vehicle interface support and sensor validity remain prerequisites.

### Steering and lane changes

| Abbreviation / behavior | openpilot v0.10.0 | NGP10 | EOP10 | 01M | 02M |
|---|---|---|---|---|---|
| LKAS — lane centering | Base | Carried | Carried with EOP vehicle adapter | Carried | Carried |
| DLAT — dynamic lateral profile | No fork arbitration | Added lane-line/laneless arbitration | Carried shared policy | Carried | Carried |
| ALCC — always-on lateral control | No fork option | Added optional policy; default off | Carried core + EOP-specific controller integration | Carried | Carried |
| Steering resume / blinker pause | Base driver override | Extended smooth resume and configurable blinker pause | Carried/extended integration | Carried | Carried |
| RED — road-edge detection / guard | Baseline model road edges | Extended vision-only guard/nudge policy | Extended extra-camera edge integration where provisioned | Carried | Carried |
| SOC — smart offset control | No fork controller | Added optional lateral offset; default off | Extended camera/radar object inputs | Carried | Carried |
| CAT — car adaptive tuning | Base parameter learning | Extended trusted-value policy; optional | Extended persistence/manual tuning integration | Carried | Carried |
| LCA — lane-change assist | Base signal + driver nudge | Extended minimum speed, optional automatic start | Carried/extended sensor checks | Carried | Carried |
| Gap / lane-width / edge checks | Base lane-change logic | Added optional checks | Extended canonical surround radar inputs | Carried | Carried |
| Lead handoff | Base lead fusion | Added optional next-lane handoff | Carried/extended integration | Carried | Carried |
| Low-speed turn desire | Base model desire handling | Added configurable experimental signal policy | Extended EOP turn integration | Carried | Carried |

### Monitoring, radar and perception ownership

| Capability | openpilot v0.10.0 | NGP10 | EOP10 | 01M | 02M |
|---|---|---|---|---|---|
| Camera DM — face/gaze monitoring | Base | Removed camera monitoring producer and related UI | Carried removal | Carried removal | Carried removal |
| SAM — steering activity monitoring | No fork producer | Added driveractivityd, 20 Hz driverMonitoringState | Carried identical producer and portable policy | Carried | Carried |
| Monitoring alerts / control contract | Base selfdrived events and forceDecel | Carried event interface; input-based warnings replace face warnings | Carried, integrated in EOP controller/planner | Carried | Carried |
| Radar2D — planar blind-spot presence | OEM/vehicle-specific baseline signals | Added portable planar BSD representation; preserves OEM flags | Extended derived compatibility output from canonical Radar4D | Carried | Carried |
| Radar3D — forward measured radar | Vehicle radar is car-specific | No Exopilot UART hardware | Added built-in forward UART → radard → radarState | Carried | Carried |
| Radar4D — canonical surround container | Not this pipeline | No EOP surround hardware | Added BLE measured 3D tracks, optional point-cloud inlet | Carried | Carried |
| Wi-Fi point cloud | Not this pipeline | Not provided | Optional RK3576 receiver; clustered-object tracker not implemented | Carried code; board support governs runtime | Carried; still requires hardware/configuration |
| Synthetic BrownPanda radar | Not this fork transport | Removed legacy adapter | Removed fabricated target slots/timers | Carried removal | Carried removal |
| OEM blind-spot signals | Vehicle dependent | Carried | Preserved actual BYD flags; not converted to invented obstacles | Carried | Carried |
| MonoD — road object detection | No fork YOLO producer | Added experimental tinygrad YOLO producer, default off | Extended RKNN producer; portable consumers carried | Carried | Carried |
| GridD / PathD / DPP — occupancy and rule proposals | No fork parallel pipeline | Added experimental portable cores; consumers separately gated | Extended multi-sensor adapters and bounded proposals | Carried | Carried |
| Extra cameras / stereo / side / rear | Baseline device camera set | No Exopilot camera hardware | Added platform paths; models, calibration and board mapping required | Carried | Carried |

Radar dimensional names describe data ownership, not transport. BLE supplies measured 3D tracks into Radar4D; Radar2D is its flattened compatibility view. Wi-Fi can add raw point clouds without changing the canonical owner. BLE does not invent vertical velocity, object shape or point counts. Confirmed mounts, freshness checks and hardware provisioning are required; raw point-cloud reception alone is not object tracking.

SAM resets on actual `steeringPressed`, `brakePressed` or `gasPressed`, and on disengagement. Automated steering torque/angle and vehicle motion do not reset it. Standstill holds awareness; changing policy or speed band preserves accumulated decay. No monitoring-off switch is provided.

| SAM speed band | Relaxed: full-awareness to critical | Tight: full-awareness to critical |
|---|---|---|
| Below 11 m/s | Hold | Hold |
| 11–22 m/s | 60 seconds | 30 seconds |
| 22–33 m/s | 30 seconds | 15 seconds |
| Above 33 m/s | 15 seconds | 10 seconds |

Soft/prompt warnings occur at 50%/75% elapsed; boundaries use ±0.5 m/s hysteresis. `ngp_dm_policy` is shared across descendants; persisted `strict` means the deployed relaxed table for compatibility. SAM detects missing driver input, not gaze, sleep or consciousness. These custom timings are not ISO-certified. See [the monitoring contract](nagaspilot/docs/STEERING_ACTIVITY_MONITORING.md).

### Inference, voice, navigation and optional services

| Capability | openpilot v0.10.0 | NGP10 | EOP10 | 01M | 02M |
|---|---|---|---|---|---|
| Driving model contract | Base v0.10 split vision/policy | Carried split contract | Extended RKNN/platform adapters | Carried | Carried |
| Tinygrad compiled runner | Baseline dependency/artifacts | Extended pinned modern runner; rebuilt artifacts required | Carried portable runner | Carried | Carried |
| Chestnut eGPU | No current fork integration | Added optional USB + AMD:LLVM, model/metadata/warmup gates | Carried common integration; private multi-input driving path fails closed | Carried restriction | Carried restriction |
| Larger trained driving weights | Baseline bundled models | Not provisioned: big ONNX names currently link to small models | No blanket upgraded-model claim | Carried limitation | Carried limitation |
| Cloud voice: Google STT → Gemini → TTS | Not this pipeline | Not provided | Added cloudd online pipeline, default off | Carried + animated PyQt status | Carried + same status |
| Local wake recognition | Not this pipeline | Not provided | Added CPU-only openWakeWord, default off | Carried | Carried |
| “Hi EXO” phrase weights | Not applicable | Not provided | Custom weights required; reference model recognizes “Hey Jarvis” | Carried requirement | Carried requirement |
| Microphone / endpointing | Not this voice pipeline | Not provided | Added beamformed audio where supported, adaptive VAD | Carried | Carried |
| Upload / reply codecs | Not this voice pipeline | Not provided | Added Ogg/Opus over HTTPS, local encoding/decoding only | Carried | Carried |
| Local STT / TTS | Not this pipeline | Not provided | Not used; recognition and synthesis run online | Carried architecture | Carried architecture |
| Siri-like interaction completeness | Not applicable | Not applicable | No complete barge-in/AEC/noise-cancellation parity | Same limitation | Same limitation |
| OSM / navigation | Baseline device/service dependent | Optional map-policy input; route publishing gap for NSLC | Extended routing/map services; connectivity/data required | Carried basic navigation HUD; no floating map | Extended presentation: floating OSM map/nav panel |
| TripD — trip statistics | Baseline logging, not fork TripD | Added optional portable service, default off | Carried via EOP parameter adapter | Carried | Carried |
| Lead departure / green-light notice | Not these fork notices | Added optional notices | Extended cloud spoken prompts when enabled/provisioned | Carried | Carried |
| Recording / streaming / teleoperation | Baseline logging/streaming tools | Carried baseline tools | Platform-specific services; availability must be checked per deployment | Carried implementation | Carried implementation |
| NavPilot credential bootstrap / actuator voice actions | Not applicable | Not provided | Not implemented end to end; no voice actuator command claim | Carried gap | Carried gap |
| MRM controlled-stop chain | Not this fork implementation | Not ported | Not ported; SAM slowdown is not an MRM implementation | Carried gap | Carried gap |

Wake inference uses ONNX CPUExecutionProvider, not the NPU or GPU. After activation, VAD submits after 800 ms of quiet, with a three-second no-speech timeout and 15-second maximum. Wake is suppressed while recording, processing or playing the downloaded reply. No custom Hi EXO model is bundled. Google inference requires a configured backend, credentials, enabled models and connectivity. Libopus can use ARM SIMD; dedicated Rockchip hardware Opus acceleration has not been verified.

### Switches, inheritance and validation

- **NGP-origin settings stay `ngp_*`** on descendants, including `ngp_dm_policy`, ALCC/SOC/CAT controls, MonoD and portable services. **EOP-origin settings stay `EOP*`**, including cloud/wake voice, platform eGPU and back-ported MTSC/MSLC/TLSC/DDSC/RCD policies. Some older `ngp_*` aliases remain for migration; a prefix describes feature origin, not necessarily hardware dependency.
- Optional experimental consumers are separately gated: enabling MonoD alone does not enable PathD control. `ngp_monod_enabled`, `ngp_pathd_enabled`, `ngp_lat_pathd`, `ngp_lon_pathd` and DPP mode must match the intended experiment. Map policies require their producer. ALCC/SOC/CAT/adaptive-gap and cloud/wake voice are opt-in. BRSC is enabled by default in NGP. Consult actual parameter defaults and UI availability rather than assuming every row has a master on/off switch.
- The lineage gate checks **86 portable Python source files** under `nagaspilot/controls`, `nagaspilot/runtime` and `nagaspilot/mapd`. EOP inherits these unchanged and adds adapters. The full NGP/EOP controller/planner files still differ because EOP has additional integration; HAL does not justify divergent monitoring policy.
- EOP10 → 01M → 02M source differences are limited to `common/build_profile.py`. Both PyQt variants use the same resize-aware driving view, offroad pages, icons and voice popup. At the normal 01M width no side widgets are created; wide layouts create them lazily.
- Host regression checks cover shared control/monitoring, protocol frame equivalence, radar contracts, compiled-model loading and PyQt layout/popup behavior. Native UI syntax checks passed. These checks do not replace vehicle, Rockchip, microphone, radar, GPU or firmware hardware validation. TASKING firmware compilation and paid Google end-to-end inference remain unverified.
- Every branch remains a development build. No feature table entry establishes vehicle safety certification. School/work-zone/sign semantic additions are outside the current implementation scope.

Implementation references: [portable monitoring](nagaspilot/docs/STEERING_ACTIVITY_MONITORING.md), [NGP dependency policy](nagaspilot/docs/DEPENDENCY_POLICY.md), [Chestnut integration](nagaspilot/docs/EGPU_INTEGRATION.md), and [lineage gate](nagaspilot/lineage.py). On EOP branches also read `docs/eop/SHARED_PIPELINE.md`, `docs/eop/CLOUDD_VOICE_PIPELINE.md`, `docs/integration/CONTROL_HAL_BOUNDARY_AUDIT.md` and `docs/DEPENDENCY_POLICY.md`. Historical port-audit tables may describe earlier states; this comparison records the current implementation and remaining gaps.

------

🔧 Branches
------

| Branch | Install | Description | Recommended&nbsp;For |
|---|---|---|---|
| `dev/EOP10` | `git clone -b dev/EOP10` | The shared ExoPilot base. Every ExoPilot device gets these features. | ExoPilot&nbsp;Developers |
| `dev/01M` | `git clone -b dev/01M` | Shared EOP features with a 1024×600 full-screen PyQt5 display. | ExoPilot&nbsp;01M&nbsp;owners |
| `dev/02M` | `git clone -b dev/02M` | Shared EOP features with a 1600×600 PyQt5 display and floating side panels. | ExoPilot&nbsp;02M&nbsp;owners |
| `dev/NGP10` | `git clone -b dev/NGP10` | NagasPilot: the smarter cruise, steering and lane-change features for the comma 3 / 3X. | comma&nbsp;3&nbsp;/&nbsp;3X&nbsp;owners |

All three EOP branches inherit the same source; `common/build_profile.py` selects the UI backend, default SoC and display size. See [Shared pipeline](docs/eop/SHARED_PIPELINE.md).

Every branch is in development and has not been tested on a real vehicle. **Do not** treat any of them as a release.

🧰 How to Install
------

There is no installer URL yet. Clone this branch onto the device:

```
git clone -b dev/EOP10 https://github.com/exo-elec/openpilot.git
```

**DO NOT** drive with a build you have not read the notes for. Every `dev/` branch changes often and can break.

🐞 Bug Reports / Feature Requests
------

If you run into bugs, issues, or have ideas for new features, please open an issue on **[GitHub](https://github.com/exo-elec/openpilot/issues)**.

Please include as much detail as possible: which branch and device you use, what you did and what happened. Photos, videos, log files, or anything that can help explain the issue or idea are very helpful!

📋 Credits
------

* [commaai/openpilot](https://github.com/commaai/openpilot): openpilot v0.10.0 (MIT)
* NagasPilot: the cruise, steering and lane-change features in the tables above

Star History
------

[![Star History Chart](https://api.star-history.com/svg?repos=exo-elec/openpilot&type=Date)](https://www.star-history.com/#exo-elec/openpilot&Date)

------

⚖️ Safety and legal
------

- **ExoPilot** is a modified version of **openpilot v0.10.0** by comma.ai, used under the MIT license (see [LICENSE](LICENSE)). It is not made, endorsed or supported by comma.ai.
- Baseline safety concepts are inherited, but the vehicle interface, monitoring and optional control policies are modified. See [docs/SAFETY.md](docs/SAFETY.md); this fork has no vehicle safety validation.
- New features have **not** been validated on a real vehicle. Always stay attentive and ready to take over.
- **There is no camera-based driver monitoring.** Driver activity monitoring only warns and slows the car when you stop touching the wheel, brake or gas; it never switches off by itself and does not check where you are looking. Keep watching the road and keep your hands ready at all times.

**MIT licensed.** openpilot is released under the MIT license. Some parts of the software are released under other licenses as specified.

Any user of this software shall indemnify and hold harmless Comma.ai, Inc. and its directors, officers, employees, agents, stockholders, affiliates, subcontractors and customers from and against all allegations, claims, actions, suits, demands, damages, liabilities, obligations, losses, settlements, judgments, costs and expenses (including without limitation attorneys’ fees and costs) which arise out of, relate to or result from any use of this software by user.

**THIS IS ALPHA QUALITY SOFTWARE FOR RESEARCH PURPOSES ONLY. THIS IS NOT A PRODUCT.
YOU ARE RESPONSIBLE FOR COMPLYING WITH LOCAL LAWS AND REGULATIONS.
NO WARRANTY EXPRESSED OR IMPLIED.**

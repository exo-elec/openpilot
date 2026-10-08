<div align="center" style="text-align: center;">

<h1>NagasPilot Audit</h1>

<p><b>NagasPilot v0.10.0 feature and audit notes for the comma 3 / 3X.</b></p>

<h3>
  <a href="#project-features">Features</a> ·
  <a href="#project-branches">Branches</a> ·
  <a href="#project-install">Install</a> ·
  <a href="#project-details">Details</a> ·
  <a href="#project-bug-reports">Bug reports</a>
</h3>

[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)
[![Branch](https://img.shields.io/badge/Branch-audit%2Ffinal--ngp10-blue)](https://github.com/exo-elec/openpilot/tree/audit/final-ngp10)
[![Latest commit](https://img.shields.io/github/last-commit/exo-elec/openpilot/audit%2Ffinal-ngp10?label=Latest%20commit)](https://github.com/exo-elec/openpilot/commits/audit/final-ngp10)

</div>

------

<a id="project-requirements"></a>

Using NagasPilot Audit
------

1. **Hardware and tools:** The device, vehicle and harness appropriate to this branch; see its project notes below.
2. **Software:** this branch, `audit/final-ngp10`.
3. **Configuration:** use this branch's project settings and integration notes.
4. **Setup:** follow *How to Install* and the detailed project guide below.

<a id="project-features"></a>

🌟 Highlight Features
------

- Driver-assistance planning and controls.
- Vehicle interfaces and messaging.
- Branch-specific NagasPilot or ExoPilot development.

<a id="project-branches"></a>

🔧 Branches
------

| Branch | Description | Use |
|---|---|---|
| [`audit/final-ngp10` **(this branch)**](https://github.com/exo-elec/openpilot/tree/audit/final-ngp10) | Audit: final ngp10 | Development / review |
| [`dev/EDP10`](https://github.com/exo-elec/openpilot/tree/dev/EDP10) | Project variant: EDP10 | Project variant |
| [`dev/EOP10`](https://github.com/exo-elec/openpilot/tree/dev/EOP10) | Shared ExoPilot application base | Project variant |
| [`dev/NGP10`](https://github.com/exo-elec/openpilot/tree/dev/NGP10) | NagasPilot for comma 3 / 3X | Project variant |
| [`dev/01M`](https://github.com/exo-elec/openpilot/tree/dev/01M) | ExoPilot 01M device edition | Project variant |
| [`dev/02M`](https://github.com/exo-elec/openpilot/tree/dev/02M) | ExoPilot 02M device edition | Project variant |
| [`nagaspilot`](https://github.com/exo-elec/openpilot/tree/nagaspilot) | Project development: nagaspilot | Project variant |

Use the branch that matches your hardware and task. A branch name does not establish release or validation status.

<a id="project-install"></a>

🧰 How to Install
------

Check out this branch:

```bash
git clone --branch audit/final-ngp10 https://github.com/exo-elec/openpilot.git
```

Cloning only downloads the source. Complete the branch-specific build, setup or
flashing steps under *Details* before running it.

<a id="project-credits"></a>

📋 Credits and Base Projects
------

- [comma.ai openpilot](https://github.com/commaai/openpilot)

Branch-specific attribution, license notices and project additions are documented below.

<a id="project-bug-reports"></a>

🐞 Bug Reports / Feature Requests
------

Report problems to the repository maintainer through the available
[GitHub channels](https://github.com/exo-elec/openpilot). If Issues is enabled, [open an issue](https://github.com/exo-elec/openpilot/issues).

Include the branch, hardware, software/toolchain versions, steps to reproduce,
expected behavior and what happened. Attach relevant logs or screenshots with
credentials and personal data removed.

<a id="project-details"></a>

📖 Details
------

<div align="center" style="text-align: center;">

<h1>NagasPilot</h1>

**A fork of openpilot v0.10.0 with smarter cruise control, steering and lane changes.**

[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)

</div>

------

**NagasPilot** is based on **openpilot v0.10.0** (the full release, not v0.10.1 or later) and runs on the **comma 3 / 3X**. It is in development and has not yet been tested on a real vehicle.

openpilot v0.10.0 vs **NagasPilot**
------

#### Gas and brake

| | Feature | What it does | openpilot v0.10.0 | NagasPilot |
|:--|---|---|:-:|:-:|
| ACC | **Adaptive Cruise Control** | Holds speed and following distance. | ✅ | ✅ |
| — | **Experimental mode** | End-to-end driving, switched on and off by hand. | ✅ | ✅ |
| DLON | **Dynamic Longitudinal Profile** | Switches between standard cruise and end-to-end following by itself. | ❌ | ✅ |
| TJA | **Traffic Jam Assist** | Smooth following in slow traffic and when other cars cut in. | ❌ | ✅ |
| — | **Speed zones** | Limits acceleration and jerk for your speed range. | ❌ | ✅ |
| BRSC | **Bumpy Road Speed Controller** | Slows down on rough or bumpy pavement. | ❌ | ✅ |
| VTSC | **Vision Turn Speed Controller** | Slows for curves the camera sees ahead. | ❌ | ✅ |
| NSLC | **Navigation Speed Limit Control** | Follows the speed limit from your navigation. | ❌ | ✅ |
| — | **Acceleration profiles** | Choose normal, eco or sport acceleration. | ❌ | ✅ |
| — | **Adaptive following gap** | Adjusts following distance to the situation. Optional. | ❌ | ✅ |
| — | **Speed offset** | Adds or subtracts a fixed amount from the set speed. | ❌ | ✅ |
| — | **Driving modes** | Eco, Normal or Sport sets acceleration, following style and adaptive gap together. Optional. | ❌ | ✅ |

#### Steering

| | Feature | What it does | openpilot v0.10.0 | NagasPilot |
|:--|---|---|:-:|:-:|
| LKAS | **Lane Centering** | Keeps the car centered in its lane. | ✅ | ✅ |
| DLAT | **Dynamic Lateral Profile** | Picks lane-line or laneless steering by how clear the lanes are, and switches early for tight curves. | ❌ | ✅ |
| ALCC | **Always-on Lateral Control** | Keeps steering assistance on without cruise control. Optional. | ❌ | ✅ |
| — | **Smooth steering resume** | Eases steering assistance back in after you take over. | ❌ | ✅ |
| RED | **Road Edge Detection** | Nudges the car away from a close road edge (curb, grass, guardrail, wall). | ❌ | ✅ |
| SOC | **Smart Offset Control** | Moves slightly away from a vehicle beside you on the highway. | ❌ | ✅ |
| CAT | **Car Adaptive Tuning** | Learns the car's steering ratio and stiffness and uses only trusted values. | ❌ | ✅ |
| — | **Lane turn desire** | Tells the driving model about a turn when you signal at low speed. Optional and experimental. | ❌ | ✅ |
| — | **Blinker pause** | Pauses steering assistance while a turn signal is on below a chosen speed, so it does not fight a turn. Optional. | ❌ | ✅ |

#### Lane changes

| | Feature | What it does | openpilot v0.10.0 | NagasPilot |
|:--|---|---|:-:|:-:|
| LCA | **Lane Change Assist** | Changes lane with a signal and a nudge. We add a minimum speed and an optional automatic start. | ✅ | ✅ |
| — | **Road-edge guard** | Blocks a lane change toward a road edge. | ❌ | ✅ |
| — | **Lane change checks** | Checks the next lane is free and wide enough. Optional. | ❌ | ✅ |
| — | **Lead handoff** | During a lane change, follows the car in the lane you are moving into. | ❌ | ✅ |

#### Safety and awareness

| | Feature | What it does | openpilot v0.10.0 | NagasPilot |
|:--|---|---|:-:|:-:|
| DM | **Driver Monitoring (camera)** | Camera check that the driver is paying attention. Removed: our devices have no driver camera. | ✅ | ❌ |
| — | **Driver activity monitoring** | Warns, then slows the car if you do not touch the wheel, brake or gas for too long (limits depend on speed). Never switches off by itself. | ❌ | ✅ |
| — | **Object detection (road camera)** | Spots cars, trucks, buses, bikes and people ahead, estimates how far they are and where they are heading. Experimental and off by default. Optional: slows the car a little when another car is about to cut in. | ❌ | 🚧 experimental |
| — | **Rule-based path planner (DPP)** | A second, rule-based planner runs beside the driving model and takes part only in certain situations: a car or bike about to cut in, or a truck or bike right beside you. It can brake earlier or move a little within the lane, and it hands control back to the driving model when anything looks unhealthy. Experimental and off by default. | ❌ | 🚧 experimental |
| — | **Lead departure notice** | Tells you when the car ahead drives off while you are stopped. Optional. | ❌ | ✅ |
| — | **Green-light notice** | Tells you when the car is released from a stop at a light. Optional. | ❌ | ✅ |

#### Device and hardware

| | Feature | What it does | openpilot v0.10.0 | NagasPilot |
|:--|---|---|:-:|:-:|
| — | **BYD support** | Support for BYD cars through our gateway. | ❌ | ✅ |

------

🌟 Highlights
------

### 🧠 Dynamic Longitudinal Profile (DLON)
openpilot's Experimental mode is switched on and off by hand. **DLON** decides for you: standard cruise when the road is easy, end-to-end following when it helps, such as in stop-and-go traffic.

---

### 🛣️ Dynamic Lateral Profile (DLAT)
**DLAT** follows the lane lines when they are clear and switches to laneless steering when they fade, and switches early when it sees a tight curve coming.

------

⚖️ Safety and legal
------

- **NagasPilot** is a modified version of **openpilot v0.10.0** by comma.ai, used under the MIT license (see [LICENSE](LICENSE)). It is not made, endorsed or supported by comma.ai.
- There is no camera-based driver monitoring: our devices (including comma 3 / 3X clones) have no driver camera. Driver activity monitoring only warns and slows the car when you stop touching the wheel, brake or gas; it never switches off by itself. Keep watching the road.
- openpilot's safety system is unchanged. See [docs/SAFETY.md](docs/SAFETY.md).
- New features have **not** been validated on a real vehicle. Always stay attentive and ready to take over.


**MIT licensed.** openpilot is released under the MIT license. Some parts of the software are released under other licenses as specified.

Any user of this software shall indemnify and hold harmless Comma.ai, Inc. and its directors, officers, employees, agents, stockholders, affiliates, subcontractors and customers from and against all allegations, claims, actions, suits, demands, damages, liabilities, obligations, losses, settlements, judgments, costs and expenses (including without limitation attorneys’ fees and costs) which arise out of, relate to or result from any use of this software by user.

**THIS IS ALPHA QUALITY SOFTWARE FOR RESEARCH PURPOSES ONLY. THIS IS NOT A PRODUCT.
YOU ARE RESPONSIBLE FOR COMPLYING WITH LOCAL LAWS AND REGULATIONS.
NO WARRANTY EXPRESSED OR IMPLIED.**

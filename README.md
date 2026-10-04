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

#### Steering

| | Feature | What it does | openpilot v0.10.0 | NagasPilot |
|:--|---|---|:-:|:-:|
| LKAS | **Lane Centering** | Keeps the car centered in its lane. | ✅ | ✅ |
| DLAT | **Dynamic Lateral Profile** | Picks lane-line or laneless steering by how clear the lanes are, and switches early for tight curves. | ❌ | ✅ |
| ALCC | **Always-on Lateral Control** | Keeps steering assistance on without cruise control. Optional. | ❌ | ✅ |
| — | **Smooth steering resume** | Eases steering assistance back in after you take over. | ❌ | ✅ |

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
| DM | **Driver Monitoring** | Checks the driver is paying attention. | ✅ | ✅ |
| — | **Lead departure notice** | Tells you when the car ahead drives off while you are stopped. Optional. | ❌ | ✅ |

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
- openpilot's safety system is unchanged. See [docs/SAFETY.md](docs/SAFETY.md).
- New features have **not** been validated on a real vehicle. Always stay attentive and ready to take over.

**MIT licensed.** openpilot is released under the MIT license. Some parts of the software are released under other licenses as specified.

Any user of this software shall indemnify and hold harmless Comma.ai, Inc. and its directors, officers, employees, agents, stockholders, affiliates, subcontractors and customers from and against all allegations, claims, actions, suits, demands, damages, liabilities, obligations, losses, settlements, judgments, costs and expenses (including without limitation attorneys’ fees and costs) which arise out of, relate to or result from any use of this software by user.

**THIS IS ALPHA QUALITY SOFTWARE FOR RESEARCH PURPOSES ONLY. THIS IS NOT A PRODUCT.
YOU ARE RESPONSIBLE FOR COMPLYING WITH LOCAL LAWS AND REGULATIONS.
NO WARRANTY EXPRESSED OR IMPLIED.**

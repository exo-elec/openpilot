<div align="center" style="text-align: center;">

<h1>NagasPilot</h1>

<p>
  <b>A fork of openpilot v0.10.0 with smarter cruise control, steering and lane changes.</b>
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
[![Branch](https://img.shields.io/badge/Branch-dev%2FNGP10-blue)](https://github.com/exo-elec/openpilot/tree/dev/NGP10)
[![Last Updated](https://img.shields.io/badge/Last%20Updated-October%208th%2C%202026-brightgreen)](https://github.com/exo-elec/openpilot/commits/dev/NGP10)
[![Issues](https://img.shields.io/github/issues/exo-elec/openpilot?label=Issues)](https://github.com/exo-elec/openpilot/issues)

</div>

------

Using NagasPilot in a car
------

To use **NagasPilot** in a car, you need four things:

1. **Device:** a **comma 3 / 3X**.
2. **Software:** this branch (`dev/NGP10`). See *How to install* below.
3. **Car:** a supported car. See [docs/CARS.md](docs/CARS.md).
4. **Harness:** the harness that matches your car's make and model, to connect the device to the car.

NagasPilot has not been tested on a real vehicle yet. Read *Safety and legal* before you drive.

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
| MTSC | **Map Turn Speed Controller** | Slows for curves ahead that the map shows, before the camera can see them. Optional, off by default. | ❌ | 🚧 experimental |
| MSLC | **Map Speed Limit Controller** | Lowers the set speed to the map's speed limit, with an offset you choose per speed range. Optional, off by default. | ❌ | 🚧 experimental |
| TLSC | **Traffic Light Speed Controller** | Slows for a red or yellow light ahead in your lane, seen by the road camera. Optional, off by default. | ❌ | 🚧 experimental |
| RCD | **Road Condition Detection** | Lowers the speed limit on a wet, icy or debris-covered road. Needs a road-surface sensor that comma devices do not have, so it stays idle there. Optional, off by default. | ❌ | 🚧 experimental |
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

🌟 Highlight Features
------

### 🧠 Dynamic Longitudinal Profile (DLON)
openpilot's Experimental mode is switched on and off by hand. **DLON** decides for you: standard cruise when the road is easy, end-to-end following when it helps, such as in stop-and-go traffic.

---

### 🛣️ Dynamic Lateral Profile (DLAT)
**DLAT** follows the lane lines when they are clear and switches to laneless steering when they fade, and switches early when it sees a tight curve coming.

---

And lots more! From safety to driving comfort, **NagasPilot** keeps growing. Check the tables above for everything it changes.

---

🔧 Branches
------

| Branch | Install | Description | Recommended&nbsp;For |
|---|---|---|---|
| `dev/EOP10` | `git clone -b dev/EOP10` | The shared ExoPilot base. Every ExoPilot device gets these features. | ExoPilot&nbsp;Developers |
| `dev/01M` | `git clone -b dev/01M` | The base plus the new on-screen display for the ExoPilot 01M (1024×600). | ExoPilot&nbsp;01M&nbsp;owners |
| `dev/02M` | `git clone -b dev/02M` | Adds the wide-screen display and the wireless corner radar (ExoPilot 02M, 1600×600). | ExoPilot&nbsp;02M&nbsp;owners |
| `dev/NGP10`&nbsp;(this&nbsp;branch) | `git clone -b dev/NGP10` | NagasPilot: the smarter cruise, steering and lane-change features for the comma 3 / 3X. | comma&nbsp;3&nbsp;/&nbsp;3X&nbsp;owners |

Every branch is in development and has not been tested on a real vehicle. **Do not** treat any of them as a release.

🧰 How to Install
------

There is no installer URL yet. Clone this branch onto the device:

```
git clone -b dev/NGP10 https://github.com/exo-elec/openpilot.git
```

**DO NOT** drive with a build you have not read the notes for. Every `dev/` branch changes often and can break.

🐞 Bug Reports / Feature Requests
------

If you run into bugs, issues, or have ideas for new features, please open an issue on **[GitHub](https://github.com/exo-elec/openpilot/issues)**.

Please include as much detail as possible: which branch and device you use, what you did and what happened. Photos, videos, log files, or anything that can help explain the issue or idea are very helpful!

📋 Credits
------

* [commaai/openpilot](https://github.com/commaai/openpilot): openpilot v0.10.0 (MIT)

Star History
------

[![Star History Chart](https://api.star-history.com/svg?repos=exo-elec/openpilot&type=Date)](https://www.star-history.com/#exo-elec/openpilot&Date)

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

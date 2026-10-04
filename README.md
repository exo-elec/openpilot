# NagasPilot

Smarter cruise control and steering.

**Runs on:** comma 3 / 3X  ·  **Status:** in development, not yet tested on a real vehicle.

## What we added to openpilot v0.10

NagasPilot is based on openpilot v0.10.0 and adds the following.

*Comes from:* **openpilot (extended)** = an openpilot feature we built on; **NagasPilot** = added by us for the comma-3 version.

### Longitudinal control (speed and following)

| Abbreviation | Feature | What it does | Comes from |
|---|---|---|---|
| **DLON** | Dynamic Longitudinal Profile | Automatically switches between standard cruise and end-to-end following to suit the situation. | openpilot (extended) |
| **TJA** | Traffic Jam Assist | Smooth following in slow traffic and when other cars cut in. | NagasPilot |
| **—** | Speed zones | Limits acceleration and jerk according to your speed range. | NagasPilot |
| **BRSC** | Bumpy Road Speed Controller | Slows the car down on rough or bumpy pavement. | NagasPilot |
| **VTSC** | Vision Turn Speed Controller | Slows for curves that the camera sees ahead, before you enter them. | NagasPilot |
| **NSLC** | Navigation Speed Limit Control | Follows the speed limit given by your navigation. | NagasPilot |
| **—** | Acceleration profiles | Choose normal, eco or sport acceleration. | NagasPilot |
| **—** | Adaptive following gap | Adjusts the following distance to the situation. Optional. | NagasPilot |
| **—** | Speed offset | Adds or subtracts a fixed amount from the set speed. | NagasPilot |

### Lateral control (steering)

| Abbreviation | Feature | What it does | Comes from |
|---|---|---|---|
| **DLAT** | Dynamic Lateral Profile | Chooses lane-line or laneless steering depending on how clear the lanes are. Includes curve assist: it switches to laneless early when a tight curve is coming. | NagasPilot |
| **ALCC** | Always-on Lateral Control | Keeps steering assistance on without cruise control. Optional. | NagasPilot |
| **—** | Smooth steering resume | Eases steering assistance back in after you take over. | NagasPilot |

### Lane changes

| Abbreviation | Feature | What it does | Comes from |
|---|---|---|---|
| **LCA** | Lane Change Assist options | Set the minimum speed for lane changes and an optional automatic start. | openpilot (extended) |
| **—** | Road-edge guard | Blocks a lane change toward a road edge. | NagasPilot |
| **—** | Lane change checks | Checks that the next lane is free and wide enough before changing. Optional. | NagasPilot |
| **—** | Lead handoff | During a lane change, follows the car in the lane you are moving into. | NagasPilot |

### Safety and awareness

| Abbreviation | Feature | What it does | Comes from |
|---|---|---|---|
| **—** | Lead departure notice | Tells you when the car ahead drives off while you are stopped. Optional. | NagasPilot |

### System and devices

| Abbreviation | Feature | What it does | Comes from |
|---|---|---|---|
| **—** | BYD support | Support for BYD cars through our gateway. | NagasPilot |

## What we removed from openpilot

Nothing is removed. NagasPilot only adds to openpilot.

## Safety and legal

- NagasPilot is a modified version of **openpilot v0.10.0** by comma.ai, used under the MIT license (see [LICENSE](LICENSE)). It is not made, endorsed or supported by comma.ai.
- openpilot's safety system is unchanged. See [docs/SAFETY.md](docs/SAFETY.md).
- Features here are new and have **not** been validated on a real vehicle.

**MIT licensed.** openpilot is released under the MIT license. Some parts of the software are released under other licenses as specified.

Any user of this software shall indemnify and hold harmless Comma.ai, Inc. and its directors, officers, employees, agents, stockholders, affiliates, subcontractors and customers from and against all allegations, claims, actions, suits, demands, damages, liabilities, obligations, losses, settlements, judgments, costs and expenses (including without limitation attorneys’ fees and costs) which arise out of, relate to or result from any use of this software by user.

**THIS IS ALPHA QUALITY SOFTWARE FOR RESEARCH PURPOSES ONLY. THIS IS NOT A PRODUCT.
YOU ARE RESPONSIBLE FOR COMPLYING WITH LOCAL LAWS AND REGULATIONS.
NO WARRANTY EXPRESSED OR IMPLIED.**

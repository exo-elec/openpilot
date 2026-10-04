# NagasPilot

Smarter cruise control and steering.

**Runs on:** comma 3 / 3X  ·  **Status:** in development, not yet tested on a real vehicle.

## What we added to openpilot v0.10

NagasPilot is based on openpilot v0.10.0 and adds the following.

| Feature | What it does |
|---|---|
| **DLON — Dynamic Longitudinal Profile** | Automatically switches between standard cruise and end-to-end following to suit the situation. |
| **DLAT — Dynamic Lateral Profile** | Chooses lane-line or laneless steering depending on how clear the lanes are. Includes curve assist: it switches to laneless early when a tight curve is coming. |
| **TJA — Traffic Jam Assist** | Smooth following in slow traffic and when other cars cut in. |
| **Speed zones** | Limits acceleration and jerk according to your speed range. |
| **BRSC — Bumpy Road Speed Controller** | Slows the car down on rough or bumpy pavement. |
| **VTSC — Vision Turn Speed Controller** | Slows for curves that the camera sees ahead, before you enter them. |
| **NSLC — Navigation Speed Limit Control** | Follows the speed limit given by your navigation. |
| **Acceleration profiles** | Choose normal, eco or sport acceleration. |
| **Adaptive following gap (optional)** | Adjusts the following distance to the situation. |
| **Speed offset** | Adds or subtracts a fixed amount from the set speed. |
| **ALCC — Always-on Lateral Control (optional)** | Keeps steering assistance on without cruise control. |
| **Lane change options** | Set the minimum speed for lane changes and an optional automatic start. |
| **Road-edge guard** | Blocks a lane change toward a road edge. |
| **Lane change checks (optional)** | Checks that the next lane is free and wide enough before changing. |
| **Lead handoff** | During a lane change, follows the car in the lane you are moving into. |
| **Smooth steering resume** | Eases steering assistance back in after you take over. |
| **Lead departure notice (optional)** | Tells you when the car ahead drives off while you are stopped. |
| **BYD support** | Support for BYD cars through our gateway. |

## Safety and legal

- NagasPilot is a modified version of **openpilot v0.10.0** by comma.ai, used under the MIT license (see [LICENSE](LICENSE)). It is not made, endorsed or supported by comma.ai.
- openpilot's safety system is unchanged. See [docs/SAFETY.md](docs/SAFETY.md).
- Features here are new and have **not** been validated on a real vehicle.

**MIT licensed.** openpilot is released under the MIT license. Some parts of the software are released under other licenses as specified.

Any user of this software shall indemnify and hold harmless Comma.ai, Inc. and its directors, officers, employees, agents, stockholders, affiliates, subcontractors and customers from and against all allegations, claims, actions, suits, demands, damages, liabilities, obligations, losses, settlements, judgments, costs and expenses (including without limitation attorneys’ fees and costs) which arise out of, relate to or result from any use of this software by user.

**THIS IS ALPHA QUALITY SOFTWARE FOR RESEARCH PURPOSES ONLY. THIS IS NOT A PRODUCT.
YOU ARE RESPONSIBLE FOR COMPLYING WITH LOCAL LAWS AND REGULATIONS.
NO WARRANTY EXPRESSED OR IMPLIED.**

# NagasPilot (NGP10) — our customization of openpilot v0.10

> **Purpose.** This repository is a fork of [commaai/openpilot](https://github.com/commaai/openpilot) v0.10. We keep openpilot's architecture and add driving policies, perception, hardware support and a product UI for our own vehicles and ExoPilot boards. Branch purpose: the comma 3 branch: openpilot v0.10 plus the NagasPilot driving policies, kept as small hooks.
>
> **This section is ours.** Below the horizontal rule is the upstream openpilot README, kept as written except that parts that do not apply to this fork were removed: comma-service install and community links, demo videos, comma-internal testing notes.



## This branch

| | |
|---|---|
| Hardware | comma 3 / 3X |
| UI | upstream UI, unchanged apart from the NGP settings panel |
| Upstream footprint in `selfdrive/` (vs v0.10.0) | 13 modified, 0 deleted, 8 new (+562/−59 lines) |
| `nagaspilot/` | 54 tracked files; none (NGP10 adds no daemons) |
| `cereal/` | 2 files edited |

**Status: development.** Nothing here has been validated on a vehicle or a production board. Tests are host-side (`python -m pytest --noconftest nagaspilot/tests` for the pure policies and `python3 nagaspilot/footprint.py` for the boundary). Full builds need the `scons` toolchain and, for the driving models, artifacts that are not in git.

## Branch lineage

```
NGP10 ──► EOP10 ──► 01M ──► 02M
```

A change lands on the earliest branch it belongs to and later branches take it by rebase (`01M` rebases onto `EOP10`; `02M` merges `01M`). Comma-3-capable policy goes on NGP10 first; RK3588 hardware work on EOP10; the Python UI and boundary work on 01M; RK3576 on 02M.

## What we changed from openpilot v0.10, step by step

### Step 0 — openpilot v0.10.0 (baseline)

- Everything in this README is measured against official openpilot v0.10.0, commit `c085b8af19438956c15592828bd082803f43dfaf` (`nagaspilot/footprint.py` uses it as `BASE`).

### Step 1 — NGP10: NagasPilot driving policies on the comma 3 ← **this branch**

- **Longitudinal** (`nagaspilot/controls/`, called from `longitudinal_planner.py`/`plannerd.py`): DLON automatic ACC/E2E switching, TJA traffic-jam gap and cut-in gate, speed-zone accel/jerk (`speed_zones.py`), BRSC bumpy-road speed reduction from the vertical IMU, lane-change lead handoff, VTSC vision turn-speed advisory, navigation speed-limit policy, adaptive acceleration limit, acceleration profiles (normal/eco/sport), optional adaptive following gap, driver speed offset.
- **Lateral** (`controlsd.py`, `modeld.py`): DLAT lane-confidence arbitration with DLP curve assist, always-on lateral (ALCC), lane-change speed/auto-start, road-edge gate, ISO vehicle-model limits, a 1.75 s steering-resume ramp.
- **Opt-in lane-change guards** (added after the 0.10 port): adjacent-lane time-to-collision gap and target-lane width checks from `modelV2` (`ngp_lane_change.py`, params `ngp_lat_lca_gap_eval` / `ngp_lat_lca_lane_width`, default off) and a stopped-lead departure notice from `radarState` (`ngp_lead_departure.py`, `ngp_lon_lead_departure`, default off; `EventName` ordinals `@98`–`@108` match EOP10).
- **Vehicle**: BYD/BrownPanda support through the exo-elec `opendbc` fork (`cereal/car.capnp` stays the upstream symlink); converted radar objects on the party bus.
- **Shape**: product code lives in `nagaspilot/` (pure policies in `nagaspilot/controls/`, Params readers in `nagaspilot/runtime/`); upstream files only carry hooks. All params are `ngp_<lat|lon>_*`, all default to the safe value, and none has been validated on a vehicle.

### Step 2 — EOP10: the ExoPilot stack on Rockchip RK3588

*Not in this branch — see `dev/EOP10`.*

### Step 3 — 01M: Python UI and the code boundary

*Not in this branch — see `dev/01M`.*

### Step 4 — 02M: Rockchip RK3576

*Not in this branch — see `dev/02M`.*

### Cross-cutting changes made while aligning the branches (2026-10)

- **Shared policies**: lane-change gap/width, lead handoff, speed-limit, longitudinal and steering policies are one module on every branch that has them; NGP10 owns the comma-3-capable ones. Opt-in guards are default off.
- **Footprint hooks**: the planner's Params readers moved to `nagaspilot/runtime/longitudinal_params.py` (budget +244 → +222).


## Where the code boundary is, and how it is checked

Rule: **`selfdrive/` stays as close to upstream as possible**; product work lives in `nagaspilot/`; heavy change is expected only in `system/` (board code) and the UI. Checked by:

- `python3 nagaspilot/footprint.py` — counts modified/deleted/new upstream files against `nagaspilot/footprint_budget.json` (a ratchet: it may only shrink unless an increase is named and justified). It measures **committed** `HEAD`, so commit first, then `--update`, then read the diff.
- `nagaspilot/tests/test_boundaries.py` — no `ngp_`/`eop_` file outside `nagaspilot/` except reviewed UI hooks; no daemon, Params or messaging imports in the pure `nagaspilot/controls/` policies.

Measured 2026-10-04 (all four branches pass both checks):

| Branch | `selfdrive/` vs v0.10: modified / deleted / new | New files in `selfdrive/` that are UI | `nagaspilot/` files | Daemons under `nagaspilot/daemons/` |
|---|---|---|---|---|
| NGP10 | 13 / 0 / 8 | 3 | 54 | 0 |
| EOP10 | 33 / 70 / 384 | 31 | 65 | 0 (still in `selfdrive/`) |
| 01M | 33 / 70 / 388 | 50 | 236 | 19 |
| 02M | 33 / 70 / 403 | 59 | 239 | 20 |


## Documentation

- `nagaspilot/docs/` — boundary plan and task list, naming rules, NGP10 feature matrix, EOP parity notes.

---

<div align="center" style="text-align: center;">

<h1>openpilot</h1>

<p>
  <b>openpilot is an operating system for robotics.</b>
  <br>
  Currently, it upgrades the driver assistance system in 300+ supported cars.
</p>

<h3>
  <a href="https://docs.comma.ai">Docs</a>
  <span> · </span>
  <a href="https://docs.comma.ai/contributing/roadmap/">Roadmap</a>
  <span> · </span>
  <a href="https://github.com/commaai/openpilot/blob/master/docs/CONTRIBUTING.md">Contribute</a>
</h3>

[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)

</div>

Using openpilot in a car
------

To use openpilot in a car, you need three things:
1. **Supported Device:** a comma 3/3X, available at [comma.ai/shop](https://comma.ai/shop/comma-3x).
2. **Supported Car:** Ensure that you have one of [the 275+ supported cars](docs/CARS.md).
3. **Car Harness:** You will also need a [car harness](https://comma.ai/shop/car-harness) to connect your comma 3/3X to your car.

We have detailed instructions for [how to install the harness and device in a car](https://comma.ai/setup). Note that it's possible to run openpilot on [other hardware](https://blog.comma.ai/self-driving-car-for-free/), although it's not plug-and-play.

### Branches
| branch           | URL                                    | description                                                                         |
|------------------|----------------------------------------|-------------------------------------------------------------------------------------|
| `release3`         | openpilot.comma.ai                      | This is openpilot's release branch.                                                 |
| `release3-staging` | openpilot-test.comma.ai                | This is the staging branch for releases. Use it to get new releases slightly early. |
| `nightly`          | openpilot-nightly.comma.ai             | This is the bleeding edge development branch. Do not expect this to be stable.      |
| `nightly-dev`      | installer.comma.ai/commaai/nightly-dev | Same as nightly, but includes experimental development features for some cars.      |
| `secretgoodopenpilot` | installer.comma.ai/commaai/secretgoodopenpilot | This is a preview branch from the autonomy team where new driving models get merged earlier than master. |

To start developing openpilot
------

openpilot is developed by [comma](https://comma.ai/) and by users like you.

* Check out [the contributing docs](docs/CONTRIBUTING.md)
* Check out the [openpilot tools](tools/)
* Code documentation lives at https://docs.comma.ai

Safety and Testing
----

* openpilot observes [ISO26262](https://en.wikipedia.org/wiki/ISO_26262) guidelines, see [SAFETY.md](docs/SAFETY.md) for more details.
* The code enforcing the safety model lives in panda and is written in C, see [code rigor](https://github.com/commaai/panda#code-rigor) for more details.
* panda has software-in-the-loop [safety tests](https://github.com/commaai/panda/tree/master/tests/safety).

<details>
<summary>MIT Licensed</summary>

openpilot is released under the MIT license. Some parts of the software are released under other licenses as specified.

Any user of this software shall indemnify and hold harmless Comma.ai, Inc. and its directors, officers, employees, agents, stockholders, affiliates, subcontractors and customers from and against all allegations, claims, actions, suits, demands, damages, liabilities, obligations, losses, settlements, judgments, costs and expenses (including without limitation attorneys’ fees and costs) which arise out of, relate to or result from any use of this software by user.

**THIS IS ALPHA QUALITY SOFTWARE FOR RESEARCH PURPOSES ONLY. THIS IS NOT A PRODUCT.
YOU ARE RESPONSIBLE FOR COMPLYING WITH LOCAL LAWS AND REGULATIONS.
NO WARRANTY EXPRESSED OR IMPLIED.**
</details>

<details>
<summary>User Data and comma Account</summary>

By default, openpilot uploads the driving data to our servers. You can also access your data through [comma connect](https://connect.comma.ai/). We use your data to train better models and improve openpilot for everyone.

openpilot is open source software: the user is free to disable data collection if they wish to do so.

openpilot logs the road-facing cameras, CAN, GPS, IMU, magnetometer, thermal sensors, crashes, and operating system logs.
The driver-facing camera and microphone are only logged if you explicitly opt-in in settings.

By using openpilot, you agree to [our Privacy Policy](https://comma.ai/privacy). You understand that use of this software or its related services will generate certain types of user data, which may be logged and stored at the sole discretion of comma. By accepting this agreement, you grant an irrevocable, perpetual, worldwide right to comma for the use of this data.
</details>

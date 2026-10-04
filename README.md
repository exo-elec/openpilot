# NagasPilot (NGP10) — our customization of openpilot v0.10

> **Purpose.** A fork of [commaai/openpilot](https://github.com/commaai/openpilot) v0.10. We keep openpilot's driving system and add smarter driving features, our own devices and a new screen. This branch: Smarter cruise control and steering on the standard comma 3.
>
> **This section is ours.** Below the horizontal rule is the upstream openpilot README, kept as written except for parts that do not apply to this fork.

**Runs on:** comma 3 / 3X  ·  **Status:** in development — not yet tested on a real vehicle. openpilot's safety system is unchanged.

## What we changed from openpilot v0.10

Based on **openpilot v0.10.0** (upstream release tag, August 2025). It does not include v0.10.1 or later. Each step below lists what is new or better compared with stock v0.10.0, and each branch includes the steps before it.

**Step 1 — NagasPilot: smarter driving on the comma 3**  ← this branch

- **Smoother cruise control**: it adapts to traffic, handles slow-moving jams and cars cutting in, and eases off for bumpy roads, sharp curves it can see ahead, and the speed limit from your navigation.
- **Your driving style**: choose how gently or sportily the car accelerates, and add a small speed offset if you like.
- **Steering that copes with poor lane lines**: it keeps working when lane markings fade, helps through curves, and eases back in smoothly after you take over.
- **Safer lane changes**: choose the minimum speed, optional automatic start, and optional checks that the next lane is free and wide enough.
- **Extras**: an optional notice when the car in front of you drives off while you are stopped, and support for BYD cars.

Branches build on each other: `dev/NGP10` → `dev/EOP10` → `dev/01M` → `dev/02M`.

*Version note: the code inside v0.10.0 already prints "0.10.1" as its version string, because upstream bumps that string before tagging. The code itself is the v0.10.0 tag.*
Technical notes for developers are in `nagaspilot/docs/`.

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

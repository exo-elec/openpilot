<div align="center" style="text-align: center;">

<h1>NagasPilot</h1>

<p><b>NagasPilot development branch based on comma.ai openpilot.</b></p>

<h3>
  <a href="#project-features">Features</a> ·
  <a href="#project-branches">Branches</a> ·
  <a href="#project-install">Install</a> ·
  <a href="#project-details">Details</a> ·
  <a href="#project-bug-reports">Bug reports</a>
</h3>

[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)
[![Branch](https://img.shields.io/badge/Branch-nagaspilot-blue)](https://github.com/exo-elec/openpilot/tree/nagaspilot)
[![Latest commit](https://img.shields.io/github/last-commit/exo-elec/openpilot/nagaspilot?label=Latest%20commit)](https://github.com/exo-elec/openpilot/commits/nagaspilot)

</div>

------

<a id="project-requirements"></a>

Using NagasPilot
------

1. **Hardware and tools:** The device, vehicle and harness appropriate to this branch; see its project notes below.
2. **Software:** this branch, `nagaspilot`.
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
| [`audit/final-ngp10`](https://github.com/exo-elec/openpilot/tree/audit/final-ngp10) | Audit: final ngp10 | Development / review |
| [`dev/EDP10`](https://github.com/exo-elec/openpilot/tree/dev/EDP10) | Project variant: EDP10 | Project variant |
| [`dev/EOP10`](https://github.com/exo-elec/openpilot/tree/dev/EOP10) | Shared ExoPilot application base | Project variant |
| [`dev/NGP10`](https://github.com/exo-elec/openpilot/tree/dev/NGP10) | NagasPilot for comma 3 / 3X | Project variant |
| [`dev/01M`](https://github.com/exo-elec/openpilot/tree/dev/01M) | ExoPilot 01M device edition | Project variant |
| [`dev/02M`](https://github.com/exo-elec/openpilot/tree/dev/02M) | ExoPilot 02M device edition | Project variant |
| [`nagaspilot` **(this branch)**](https://github.com/exo-elec/openpilot/tree/nagaspilot) | Project development: nagaspilot | Project variant |

Use the branch that matches your hardware and task. A branch name does not establish release or validation status.

<a id="project-install"></a>

🧰 How to Install
------

Check out this branch:

```bash
git clone --branch nagaspilot https://github.com/exo-elec/openpilot.git
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

<details>
<summary>Upstream reference documentation and notices</summary>

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
  <span> · </span>
  <a href="https://discord.comma.ai">Community</a>
  <span> · </span>
  <a href="https://comma.ai/shop">Try it on a comma 3X</a>
</h3>

Quick start: `bash <(curl -fsSL openpilot.comma.ai)`

[![openpilot tests](https://github.com/commaai/openpilot/actions/workflows/selfdrive_tests.yaml/badge.svg)](https://github.com/commaai/openpilot/actions/workflows/selfdrive_tests.yaml)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)
[![X Follow](https://img.shields.io/twitter/follow/comma_ai)](https://x.com/comma_ai)
[![Discord](https://img.shields.io/discord/469524606043160576)](https://discord.comma.ai)

</div>

<table>
  <tr>
    <td><a href="https://youtu.be/NmBfgOanCyk" title="Video By Greer Viau"><img src="https://github.com/commaai/openpilot/assets/8762862/2f7112ae-f748-4f39-b617-fabd689c3772"></a></td>
    <td><a href="https://youtu.be/VHKyqZ7t8Gw" title="Video By Logan LeGrand"><img src="https://github.com/commaai/openpilot/assets/8762862/92351544-2833-40d7-9e0b-7ef7ae37ec4c"></a></td>
    <td><a href="https://youtu.be/SUIZYzxtMQs" title="A drive to Taco Bell"><img src="https://github.com/commaai/openpilot/assets/8762862/05ceefc5-2628-439c-a9b2-89ce77dc6f63"></a></td>
  </tr>
</table>


Using openpilot in a car
------

To use openpilot in a car, you need four things:
1. **Supported Device:** a comma 3/3X, available at [comma.ai/shop](https://comma.ai/shop/comma-3x).
2. **Software:** The setup procedure for the comma 3/3X allows users to enter a URL for custom software. Use the URL `openpilot.comma.ai` to install the release version.
3. **Supported Car:** Ensure that you have one of [the 275+ supported cars](docs/CARS.md).
4. **Car Harness:** You will also need a [car harness](https://comma.ai/shop/car-harness) to connect your comma 3/3X to your car.

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

openpilot is developed by [comma](https://comma.ai/) and by users like you. We welcome both pull requests and issues on [GitHub](http://github.com/commaai/openpilot).

* Join the [community Discord](https://discord.comma.ai)
* Check out [the contributing docs](docs/CONTRIBUTING.md)
* Check out the [openpilot tools](tools/)
* Code documentation lives at https://docs.comma.ai
* Information about running openpilot lives on the [community wiki](https://github.com/commaai/openpilot/wiki)

Want to get paid to work on openpilot? [comma is hiring](https://comma.ai/jobs#open-positions) and offers lots of [bounties](https://comma.ai/bounties) for external contributors.

Safety and Testing
----

* openpilot observes [ISO26262](https://en.wikipedia.org/wiki/ISO_26262) guidelines, see [SAFETY.md](docs/SAFETY.md) for more details.
* openpilot has software-in-the-loop [tests](.github/workflows/selfdrive_tests.yaml) that run on every commit.
* The code enforcing the safety model lives in panda and is written in C, see [code rigor](https://github.com/commaai/panda#code-rigor) for more details.
* panda has software-in-the-loop [safety tests](https://github.com/commaai/panda/tree/master/tests/safety).
* Internally, we have a hardware-in-the-loop Jenkins test suite that builds and unit tests the various processes.
* panda has additional hardware-in-the-loop [tests](https://github.com/commaai/panda/blob/master/Jenkinsfile).
* We run the latest openpilot in a testing closet containing 10 comma devices continuously replaying routes.

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

</details>

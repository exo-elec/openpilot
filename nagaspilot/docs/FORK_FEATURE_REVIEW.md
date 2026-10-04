# What other openpilot forks do that we do not (2026-10-04)

Sources read: sunnypilot `master` (source tree), FrogPilot `FrogPilot` branch (source tree + README), CarrotPilot `carrot`
branch (source tree + README), dragonpilot `master` (README only: its source tree could not be listed, so its rows come
from the README and general knowledge and are marked *unverified*). Nothing was copied from their code; ideas only.

Rule for what we take: it must help a driver, not duplicate something we already have, and have a pure core that can be
unit-tested. Everything we add is opt-in and off by default until it has been driven.

## Already covered by us (not redundant to add)

| Their feature | Fork | Our equivalent |
|---|---|---|
| Always-on lateral (MADS / ALKA / AOL) | sunnypilot, dragonpilot, FrogPilot | ALCC |
| Dynamic / conditional experimental mode (DEC / CEM) | sunnypilot, FrogPilot | DLON |
| Speed limit assist / controller (SLA / SLC) | sunnypilot, FrogPilot | NSLC, MSLC and the speed-limit resolver |
| Smart cruise control, curve speed (vision + map) | sunnypilot, FrogPilot, CarrotPilot | VTSC, MTSC |
| Auto lane change, lane change options | sunnypilot, FrogPilot, dragonpilot | LCA options |
| Road edge detection | dragonpilot (unverified) | RED |
| Lead departure and green-light alerts | dragonpilot (unverified), FrogPilot, sunnypilot (e2e alerts) | Lead departure notice, Green-light notice (added now) |
| Personalities / drive modes, custom following distance | FrogPilot, CarrotPilot (`driving_mode`, `cruise_gap`) | Acceleration profiles, adaptive following gap, speed offset |
| Driving statistics | FrogPilot | Trips (`tripd`) |
| Neural-network lateral feed-forward (NNLC / NNFF) | sunnypilot, FrogPilot | `nnlc` on the ExoPilot branches (needs per-car models; not on NGP10) |
| Auto shutdown / power saving | dragonpilot (unverified) | ExoPilot power monitoring |

## Added now

| Feature | Idea from | What it does | Where |
|---|---|---|---|
| Blinker pause (`ngp_blinker_pause.py`) | sunnypilot `blinker_pause_lateral` | Pauses steering assistance while a turn signal is on below a chosen speed (and for 1.5 s after), so lane centering does not fight a turn or a parking manoeuvre. Param `ngp_lat_blinker_pause_mph`, 0 = off. | all branches |
| Green-light notice (`ngp_green_light.py`) | dragonpilot / FrogPilot / sunnypilot e2e alerts | Notice when the car is released from a planner stop while still standing. Param `ngp_lon_green_light`. NGP10 shows it; ExoPilot also speaks it (it already did). | all branches |

## Worth doing next, in this order

| Feature | Idea from | Why not now |
|---|---|---|
| Lane turn desire | sunnypilot `lane_turn_desire` | Feeds a turn desire to the model at low speed with a signal on. Changes model input, so it needs replay data first. |
| Driving-mode bundle | CarrotPilot `driving_mode`, FrogPilot personalities | One setting that sets profile + gap + offset together. Pure UI/params work once the pieces are validated. |
| Model selector | sunnypilot, FrogPilot, CarrotPilot | On the ExoPilot devices: choose between the RKNN driving model and the external-GPU model without a re-flash. Needs the model artifacts. |
| Radar lead validation tools | CarrotPilot `radar/tools` | Replay/validate radar leads against video. Useful for our `radar3d` and corner radars before trusting them. |
| UI personalisation (themes, brightness and timeout) | FrogPilot, sunnypilot | Only for the Python UI on 01M/02M. |
| Weather caution | FrogPilot `weather_checker` | Theirs calls an online weather service; ours would have to use the cameras and radar (02M's radar4d already feeds a low-visibility alert). |
| Torque tuning variants (jerk-aware, v0) | sunnypilot | Needs a vehicle to tune; nothing to test here. |

## Left out on purpose

Cloud and account services (sunnylink, comma connect, CarrotMan server, FrogPilot telemetry/"The Pond"), Hyundai/Kia/Genesis
specific control and button handling (CarrotPilot), second cluster display (CarrotPilot), Jetson integration (CarrotPilot),
and region-specific speed-camera data. We have our own hardware and no cloud.

# EDP10 Migration Plan

## Method

Port behavior in small commits, using `dev/EDP11` commit `20f438166` and the
proven BYD Atto 3 repository as references rather than cherry-picking either
tree wholesale. Preserve upstream and DragonPilot behavior unless a BYD or
comma 3 requirement is demonstrated.

## Stages

1. Document immutable bases, ownership boundaries, and validation criteria.
2. Add passive BYD identification, DBC/parser definitions, fingerprints,
   torque data, and deterministic parser tests.
3. Add classic Panda CAN safety rules and forwarding tests; build both standard
   Panda and `panda_tici` targets.
4. Add steering and HUD control while preserving required stock message fields.
5. Validate factory longitudinal and stock AEB as the default mode.
6. Add openpilot longitudinal as an explicit trial, with safety tests and route
   evidence independent from factory-longitudinal results.
7. Port selected EOP policies only when they fit the fixed CITY/HIGHWAY product
   model and do not weaken driver monitoring.
8. Keep MonoD/SOC in shadow mode until comma 3 resource use, camera geometry,
   temporal tracking, and guardrail behavior are demonstrated from recordings.

## Current Port State

The first BYD slice is passive and is adapted from `dev/EDP11` plus
`shemps/byd-atto3-openpilot-port` commit
`5b34194240bb831719629d2fd095fae5daaed1e0`. It registers the Atto 3,
reference fingerprints and firmware, DBC/checksum, state parser, and parser
tests. The interface uses Panda `noOutput`, reports `dashcamOnly`, advertises no
openpilot-longitudinal trial, and its controller always returns an empty CAN
send list. Actuation and Panda safety remain later, independent stages.

## Explicit Non-Goals

Do not replay wholesale UI, `cereal`, `modeld`, or `dmonitoring` API changes
from `dev/EDP11`. Do not import EOP hardware HAL/RK3588 infrastructure, stereo
or radar dependencies, or its large daemon stack. Do not delete or rebrand
DragonPilot/Dashy sources. Avoid generic Panda driver changes unless a focused
BYD test proves they are necessary.

## Per-Commit Gate

Each functional commit must state provenance and safety impact, run focused
unit/safety tests, pass `git diff --check`, and keep stock and openpilot control
paths distinguishable. Host tests are not vehicle validation; retain recorded
CAN/video, route, CANape, or HIL evidence for hardware decisions.

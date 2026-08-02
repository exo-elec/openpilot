# NGP10 Project Concept

## Product boundary

NGP10 is the comma 3 selfdrive/application proving line between the conservative
EDP10 port and EOP10 hardware integration. It starts from official openpilot
v0.10.0 and keeps the original comma 3 camera/process boundary: narrow/road and
wide-road cameras, with the driver camera optional for monitoring.

## Vehicle abstraction

The test car or any target vehicle is presented to selfdrive as a Tesla Model 3
HW3-style vehicle. TC275 FreeRTOS BrownPanda is the gateway: it translates
target-car CAN into the Tesla-format protocol and translates commands back.
Target-car-specific CAN decoding, checksums, timing, and actuation safety belong
in the gateway. NGP10 must not create a separate selfdrive brand fork for each
car.

## Responsibilities

- NGP10 proves DLAT, DLON, VTSC, MTSC, ALCC, LCA, speed policy, SOC, GridD,
  single-camera MonoD, radar2D/radar3D, and overlay contracts with unit tests
  and recorded routes. Features that
  need extra streams or compute are capability-gated rather than removed.
- BrownPanda proves Tesla-format wire compatibility, gateway safety, and target
  car translation.
- EOP10 proves HAL, packaging, camera transport, and hardware-in-the-loop
  behavior using only NGP10 commits that already passed application gates.
- EOP10 infrastructure changes (vehicled/socketd, cereal topics, v4l2d,
  inferenced, and Rockchip services) are integrated only at the EOP10 boundary;
  NGP10 keeps upstream comma 3 camera and Panda/OpenDBC behavior until Tesla
  gateway parity is demonstrated.
- `adaptd` may be ported as a pure normalized-telemetry profile computer;
  Bluetooth/NCP/OBD transport remains outside comma 3 selfdrive.

## Non-goals

Do not import RK3588/RKNN or EOP HAL implementation into the comma 3 runtime.
GridD, single-camera MonoD, radar2D/radar3D, SOC, and side/left/right/rear
overlays may exist as portable, capability-gated application modules; radar4D
remains excluded, and comma 3 must fall back to its two road cameras when those
streams are absent. Keep driver monitoring enabled and
preserve stock AEB/longitudinal authority until gateway and route evidence
supports a controlled promotion.

# NGP10 Complete Comma 3 Port Plan

## Summary

`dev/NGP10` is the application/selfdrive proving line based on openpilot
v0.10.0. Only tested application commits are promoted to EOP10 for HAL and
hardware validation. The target is original comma 3 with narrow/road and
wide-road cameras, an optional driver camera for monitoring, and the installed
D=80 mm camera geometry reference.

The vehicle boundary is Tesla Model 3 HW3-shaped: TC275 FreeRTOS BrownPanda
(or an equivalent gateway) translates the target car's CAN to Tesla-format
messages. Comma 3 selfdrive consumes that normalized interface; target-car
parsing and actuation stay in the gateway rather than becoming a new brand fork
inside NGP10.

## Omit from Comma 3

Do not port RK3588/RKNN/NPU runners, PCIe/Hailo accelerators, or EOP HAL code
into the comma 3 runtime. Implement capability-gated interfaces for GridD,
SOC, single-camera MonoD, and side/left/right/rear overlays, but keep them disabled
when their streams or compute backend are unavailable. Radar2D and radar3D are
allowed through the normalized Tesla radar protocol; radar4D,
CAN-FD, `steamd`, `sided`, `reard`, and EOP branding remain out of the comma 3
runtime. Do not replace the Tesla-format gateway with a Python vehicle daemon.
Defer YOLO/3D detection and SOC actuation until resource, geometry, and
guardrail-safety evidence passes.

## Port sequence

1. **Camera contract**: preserve v0.10.0 camerad/modeld/dmonitoring APIs;
   document narrow/wide streams, frame timing, calibration, and D=80 mm
   geometry. Missing driver camera affects monitoring only; missing road camera
   falls back safely to stock behavior.
2. **Lateral**: keep NGP10DLAT non-controlling until v0.10.0 model fields,
   lane confidence, predicted path, and hysteresis pass replay tests. Preserve
   EOP terminology: ALCC is Always Lane Centering Control; LCA is Lane Change
   Assist. LCA remains human-nudge by default with driver override and DM.
3. **Longitudinal**: port DLON trigger/state evaluation only. Factory/stock
   longitudinal, AEB, RED, traffic events, CITY/HIGHWAY limits, and driver
   monitoring remain authoritative until replay and safety evidence pass.
4. **Curve/speed features**: add VTSC as a camera/model-only shadow target;
   test MTSC pure curvature math without making maps/GPS mandatory. Resolve
   speed-limit sources before allowing any cruise-speed change. Implement ALCC
   and LCA state machines with stock driver-monitoring and human override.
5. **Perception/overlays**: implement GridD's capability contract, lazy BEV,
   single-camera MonoD shadow inference, SOC offset proposal, and side/left/right/rear overlay interfaces. On comma
   3, publish diagnostics and use narrow/wide fallbacks; on EOP10, bind real
   side/rear streams through HAL only after synchronization and calibration.
6. **Radar and lane change**: implement normalized Tesla radar2D/radar3D
   ingestion, tracking, and safety-limited LCA gap/blind-spot decisions. Keep
   radar4D, point-cloud fusion, and raw radar hardware drivers out of NGP10.
7. **Gateway/interface**: validate TC275 FreeRTOS BrownPanda translation,
   Tesla Model 3 HW3 message timing/checksums, and gateway safety. Port the
   portable Tesla-format vehicle parser/controller/safety contract only after
   those wire tests pass.
8. **Adaptive telemetry**: port `adaptd`'s pure profile computer for
   normalized `ncpVehicleData`; keep BLE/NCP/OBD transport and target-car PID
   interpretation on the gateway/NavPilot side.

## Promotion gates

Every feature must pass pure unit tests, missing-field/camera tests, recorded
route replay with no actuator changes, comma 3 CPU/memory/frame/thermal checks,
stationary gateway/Panda safety checks, Tesla-format checksum/rate tests, and
EOP10 HAL/HIL validation. Promote only after both camera profiles and optional
driver-camera behavior are reproduced without radar or stereo dependencies.

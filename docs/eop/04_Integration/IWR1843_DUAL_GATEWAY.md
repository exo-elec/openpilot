# IWR1843 dual-gateway integration tasks

2026-11-01. Source audit/design only; no new receiver is active.
The user authorizes adapting firmware and both host repositories together.

## Current contract versus new topology

Two EFR32 BLE peripherals will carry four independent IWR1843AOP radars.
Existing system/bluetoothd/ble_central.py connects to ESP32 corner peripherals,
uses one address -> one corner in CornerPairTable and authorizes the sender
before publishing radarCornerTracks. Each existing notification has an 8-byte
header and at most twelve 14-byte tracked objects. A paired address cannot yet
represent two independently calibrated IWR slots. The HAL wire decoder is in
exopilot/hal/hal/drivers/radar/radar_ble.py. Do not hard-code an ESP32 company ID
or a nonexistent EFR32 WiFi MAC to make the new gateways pass commissioning.

## Decision and implementation order

1. Keep tracks/health/control as the bounded primary BLE stream; clouds are a
   budgeted optional stream. Four clouds of 128 float32 x/y/z/v points at20Hz
   require1.31Mbit/s of payload alone. Tracks with twelve legacy records require
   112.64kbit/s across all four nodes, before gateway envelopes/radio overhead.
2. Define the versioned firmware/host contract together, with gateway identity,
   slot, node boot ID, local track ID, capture timestamp, sequence and validity.
   Generate canonical vectors before implementing either endpoint. There is no
   compatible new characteristic/parser to enable yet.
3. Extend commissioning to (gateway,slot) -> sensor/corner/extrinsic, with
   explicit supported-slot count. Retain existing ESP32 pair-table compatibility.
   Reject unassigned slots before changing state. Namespace tracks by node/boot.
4. Keep independent latest-frame/expiry state for each slot; one failed sensor
   must not remove its sibling. Bound fragment counts, reassembly buffers and
   expiry; test reboot/replay/wrap/reconnect/malformed/incomplete frame cases.
5. Reuse the single radarCornerTracks publisher. Preserve existing fusion and
   vehicle controls. TI x-right/y-forward/z-up becomes HAL azimuth-left-positive
   once, then calibrated vehicle coordinates; yRel stays left-positive.
6. Measure 01M RTL8822CE/BlueZ PHY, DLE, negotiated MTU, two-link aggregate
   goodput and p95/p99 capture age with phone BLE active. Current pinmux/setup
   docs are source evidence, not controller throughput measurements. Do not
   enable the 02M WiFi-only radar4d daemon on 01M as a substitute.
7. Fix firmware's current one64-byte SPI packet per20ms step before promising
  20Hz clouds. Use per-bus async acquisition/complete-frame queues; reducing the
   SPI clock period alone leaves that scheduling bottleneck.
8. Start TI FOV gates/GTRACK on IWR; calibrated ground/ceiling/sky ROIs and voxel
   reduction are separately qualified. Do not average away nearest or closing
   obstacles, delete slopes as ground, or claim coherent four-radar apertures.

Common runtime changes belong on dev/EOP10, then propagate to dev/01M and
dev/02M with their independent variant work preserved. Existing prunable
worktree registrations are not permission to remove or switch those checkouts.

The detailed BSP/HAL plan is exopilot docs/02-HARDWARE/IWR1843_DUAL_GATEWAY.md.
References: Silicon Labs BLE throughput documentation and TI Robotics SDK
four-radar synchronization example linked there. No board is verified.

## Side-mirror installation and CornerPilot migration decision

Owner clarification: two radars at approximately90 degrees to each other are
installed near the side fender/under the exterior side mirror, looking outward
with forward/rear coverage and rearward bias for blind spots. Repeat on the
other side: two EFR32 gateways, four independent IWR1843AOP radars in total.
These are two paired side mounts, not four sensors mounted at bumper corners.

| Gateway | Logical sensor role | Physical position | Orientation |
|---|---|---|---|
| Left EFR32 | left forward-looking | left side-mirror/fender area | outward/forward |
| Left EFR32 | left rearward-looking | same area, separately measured origin | outward/rear |
| Right EFR32 | right forward-looking | right side-mirror/fender area | outward/forward |
| Right EFR32 | right rearward-looking | same area, separately measured origin | outward/rear |

SPI slot assignment is a commissioning choice, not inferred from these names.
Store each sensor's measured XYZ and roll/pitch/yaw; do not substitute old
front_left/rear_left bumper origins. For illustration only, left yaw+45/+135
and right-45/-135 degrees give a90-degree separation in a vehicle frame with
x-forward/y-left/z-up. Rearward bias changes both bearings. These are not
calibration values; exact yaw, pitch, height and body/radome occlusion are unknown.

Usable coverage must overlap for reliable measured handover. For a simplified
horizontal model, overlap is max(0, half_FOV_A + half_FOV_B - separation).
For example two120-degree usable FOVs separated90degrees overlap30degrees;
this is geometry, not a claim of the installed IWR's usable detection coverage.
Validate with vehicles at several ranges, including the seam and close to the
door: fender/mirror/body shadow, radome loss and edge SNR can reduce coverage.

### Side boundaries and vehicle handover

- **Boundary output:** estimate persistent side surfaces/guardrails/curbs from
  time-aligned points in the vehicle frame, with uncertainty, age and support.
  Radar reflections alone do not guarantee a semantic wall. Report unavailable
  when geometry/support is insufficient; camera/ego-motion context can help.
- **Separate obstacle output:** retain moving and stationary vehicle/obstacle
  candidates. Do not classify a parked car as removable wall, or discard ground/
  wall points before the boundary estimator has used them. Boundary estimation
  and obstacle tracking are distinct consumers of the measured cloud.
- **One host track:** each IWR can run the proven TI local tracker, but its local
  IDs are independent. The ExoPilot host associates calibrated, capture-time
  aligned observations into a global vehicle track using position, motion,
  uncertainty and history. A vehicle entering the adjacent beam keeps that
  host ID only while association remains supported. Do not directly match equal
  local IDs or require identical radial velocities from different viewpoints.
- **Overlap:** jointly associate/deduplicate observations in the seam; preserve
  sensor provenance and avoid treating correlated reports as independent high
  confidence evidence. Do not merge adjacent vehicles merely because they are close.
- **Gap/loss:** bounded prediction may bridge a short gap with explicit increased
  uncertainty and age. Expire unsupported tracks; do not claim continuous
  measured coverage through an occluded gap. Reboot invalidates local IDs.

Current openpilot radar_corner_geometry.py permits per-sensor poses but uses
legacy corner names/IDs. radar_zones.py also contains front-corner-specific
association/confirmation assumptions. Audit both before enabling this paired
side layout; relabeling a rear-facing mirror sensor as a rear-bumper radar is
not sufficient. Preserve a single publisher/fusion owner, not a second competing
handover daemon. Existing safety/control behavior is not changed by this note.

### Repository ownership and staged work

1. **Now:** record this concept in exopilot and openpilot. This document is the
   decision/task checkpoint; no new sensor fusion or transport is enabled.
2. **Radar firmware source:** IWRx843_RADAR dev/IWR1843 remains the77GHz
   application baseline, inheriting shared dev/IWR and dev/BLT. Keep proven TI
   examples, licensing, build evidence and reusable layers there. Do not merge
   the60GHz indoor dev/IWR6843 profile into the road variant.
3. **Later CornerPilot migration:** migrate the relevant dev/IWR1843 firmware
   foundation into the new CornerPilot hardware work. The owner identifies the
   old reference as ../../RADAR/CornerPilot; it is not present at the checked
   /mnt/c/wsl/Radar/CornerPilot location in this session. Do not recreate/delete
   that repository or invent a source revision. Inventory its actual checkout
   when available, then port useful generic interfaces/tests individually.
4. **New CornerPilot target hardware:** EFR32MG26 BLE gateway + two independent
   SPIA-slave IWR1843AOP nodes per side, replacing its previous controller/radar
   hardware. EFR32 is master on both buses. Do not treat the old controller's
   GPIO/RTOS/radio code or kit pinmux as hardware-compatible.
5. **Host:** exopilot HAL/BSP owns device wire decode and physical calibration;
   openpilot owns BLE commissioning, global-track/boundary consumers and fusion.
   Coordinate the versioned gateway/slot contract across the new CornerPilot
   and host implementations. Keep raw and reduced outputs diagnosable.

Acceptance before claiming handover: two vehicles crossing in the overlap,
stationary vehicle beside a wall, seam passage at different ranges/speeds,
single-radar loss/reboot, gateway reconnect, packet delay/reorder and an
occluded no-overlap segment. Check global-ID switches, duplicate tracks,
association errors, track age and boundary uncertainty against reference data.
RF interference/sync and platform-specific CPU/BLE budgets remain separate tests.

## Validation of this record

Documentation-only update; no host runtime files changed. Source paths and the
existing BLE contract were inspected. openpilot ./test.sh was invoked and exits
before lint/tests at the existing NGP lineage check:
`changed: nagaspilot/runtime/bigmodel_platform.py`. This record does not fix or
claim to pass that unrelated runtime lineage gate. No hardware test was run.

# CornerPilot paired-radar integration plan — 2026-11-01

## Source ownership and hardware profiles

| Repository/branch | Current role |
|---|---|
| CornerPilot dev/EFR32 | BLE/FreeRTOS gateway, two independent SPI masters |
| CornerPilot dev/IWR1843 | Two independent77GHz radar nodes, each SPIA slave |
| IWRx843_RADAR dev/IWR | Shared TI controller/voxel source foundation |
| IWRx843_RADAR dev/IWR1843 | ADAS chip overlay and normal1.3V RF board profile |
| IWRx843_RADAR dev/IWR6843 | Separate60GHz indoor chip overlay |
| exopilot HAL/BSP | Device decode, board interfaces and calibration data |
| openpilot | Vehicle-wide occupied geometry and track fusion |

Local gateway: /mnt/c/wsl/Pilot/CornerPilot. Node worktree:
/mnt/c/wsl/Pilot/.worktrees/CornerPilot-IWR1843. The old NRF50 code is historical;
its checkout/main is preserved, not the new EFR32 product.

CornerPilot SPI1 EUSART1 uses PC02 MOSI/PC01 MISO/PC03 clock/PC04 CS.
SPI2 EUSART2 uses PD05 MOSI/PD04 MISO/PD03 clock/PA00 CS. Every IWR uses its own
SPIA slave F2/D1/D2/C2. CareMatter's separate product uses PD04/PD03/PD05 for
I2S and PA00 for3VL_EN; do not mix those pinmaps.

## Geometry and useful data

Two radars near each exterior side mirror/fender look outward/forward and
outward/rear, approximately90degrees apart. Two EFR32 gateways give four radars
vehicle-wide. Measure each sensor's XYZ, roll/pitch/yaw and usable coverage;
these are paired side mounts, not rear/front bumper origins or a coherent array.

The obstacle band is15..165cm above local ground. Keep static as well as moving
occupied surfaces: parked cars, walls, guardrails and toll-gate arms matter.
A parked car can be a boundary and a tracked object simultaneously. Preserve
observed height/range extents; a centroid/track list alone cannot show wall limits.
Sensor Z is not ground height on a tilted vehicle. Use qualified ground/extrinsic
calibration and uncertainty margins; unknown coverage is never free space.

Send complementary tracks and occupied voxels/points. Proposed geometry records
include cell dimensions, observed ground-height min/max, representative XYZ and
radial Doppler, original SNR/noise/quality and population. Four-dimensional radar
means XYZ plus radial velocity, not three independently measured velocity axes.
Retain input/kept/drop counts, frame/source/boot/session, capture clock/timestamp,
sequence, calibration/ROI revision, age and validity. Do not invent confidence.

Shared voxel code is in IWRx843_RADAR firmware/70_application/74_scene/iwr_voxel.*,
not on EFR32. Its6KiB caller workspace preserves nearest/closing/height extrema,
accepts up to1024 points/128 cells and fails explicitly on capacity loss. It is
not yet active in the target's main loop or canonical32-point transport.

## Handover and host adaptation

EFR32 owns two-slot acquisition/identity/time alignment and eventual bounded
pair-local track association. A supported handover preserves its pair-local ID
while retaining both source/local IDs and uncertainty. Reboot invalidates that
node's namespace. openpilot retains vehicle-wide IDs across both gateways.
Different view directions need not measure equal radial Doppler. Deduplicate
correlated overlap data; do not merge adjacent vehicles just because they are close.
No-overlap or occluded gaps allow only bounded prediction with increased age/
uncertainty; expire unsupported tracks, never claim continuous measured coverage.

Existing system/bluetoothd/ble_central.py uses one BLE address -> one legacy corner.
That does not identify two SPI slots per gateway. HAL radar_ble.py owns decode;
BlueZ/cereal publication remains in openpilot. Update commissioning to explicit
(gateway,slot) -> sensor/pose. Do not fake an ESP32 WiFi MAC/company ID for EFR32.
Keep one radarCornerTracks publisher; add a geometry contract rather than claiming
old ground-projected returns describe wall height. Current radar_corner_geometry.py
uses yaw/XY for leveled tracks; height needs a full3D transform. radar_zones.py's
front-corner assumptions also need review for these paired side mounts.

## Bandwidth and headroom

Four128-point clouds at20Hz and16B/point need1.31Mbit/s payload before overhead.
Two BLE connections share the host controller/radio schedule. Use tracks first,
budgeted voxel geometry, latest-frame queues and adaptive detail; dense diagnostics
may need a wired path. TI's8-byte compressed point/unit format is a proven encoding
reference, not the current packet. Existing32-point truncation must not be hidden.

IWR's new baseline MSS data-region unallocated margins are about95KiB on1843 and
47KiB on6843; one6KiB workspace fits statically. DSP L2 on1843 has only1,510B
unallocated. Reserved heaps/radar cubes are not spare; CPU/heap/stack high-water
and sustained BLE throughput remain unmeasured. Profile TI PMU and frame/output
statistics on hardware before activation. 01M's RTL8822CE/BlueZ PHY, DLE/MTU and
phone-role coexistence need actual measurements;02M WiFi radar4d is not01M proof.

## Tasks

- [x] Create separate CornerPilot hardware branches from committed EFR32/TI baselines.
- [x] Build the SDK dual-SPI BLE/FreeRTOS gateway replacement; retire copied Matter
      runtime after successful link. No paired streaming or handover is implied.
- [x] Add/test/native-compile the shared bounded voxel helper; rebuild both TI board
      profiles and correct the1843 EVM1.0V bypass assumption for1.3V inputs.
- [ ] Define versioned reduced-cloud metadata/golden vectors before runtime wiring.
- [ ] Implement qualified async dual-SPI workers and per-slot reset/fault/power handling.
- [ ] Implement BLE service, commissioning, HAL decode and geometry/track publication.
- [ ] Add calibrated pair-local/global association with source provenance and expiry.
- [ ] Test15cm low obstacles,165cm crossings, parked cars beside walls, thin gates,
      two targets crossing the overlap, node loss/reboot, delay/reorder and occlusion.
- [ ] Measure capture-age p95/p99, ID switches/duplicates, quality/drop rates,
      boundary uncertainty, target cycles and two-link BLE goodput on real hardware.

No board, road, safety or detection-range acceptance is established. Shared host
runtime changes start on dev/EOP10 and propagate to01M/02M preserving variant work.
This update changes documentation only. ./test.sh currently stops before tests at
the pre-existing NGP lineage mismatch in nagaspilot/runtime/bigmodel_platform.py.

References:
- https://docs.silabs.com/bluetooth/latest/bluetooth-fundamentals-system-performance/throughput
- https://docs.silabs.com/gecko-platform/3.0/driver/api/group-spidrv
- https://dev.ti.com/tirex/explore/content/radar_toolbox_4_00_00_05/source/ti/examples/Industrial_and_Personal_Electronics/People_Tracking/3D_People_Tracking/docs/3d_people_tracking_user_guide.html
- https://software-dl.ti.com/jacinto7/esd/robotics-sdk/08_04_00/docs/source/docs/multi_sensor_time_synchronization.html

# radar4d — corner-node WiFi/UDP point cloud (ExoPilot 01M Integration)

*(Filename kept for history/link stability — this doc used to describe a
single BGT60TR13C sensor mounted on the stereo camera bar, driven directly
over SPI. That sensor is gone: `radar4d` now comes from 4 identical
corner-mounted `~/radar/ESP32_RADAR` nodes (FL/FR/RL/RR — ESP32-S3 +
BGT60TR13C each), streaming their own onboard-CFAR point clouds over
WiFi/UDP instead. The chip name in this doc's filename is now historical,
not descriptive of the current sensor topology.)*

## Radar classification

| Socket | Source | Range | Consumer | Purpose |
|--------|--------|-------|----------|---------|
| `radar3d` | long-range UART radar (`selfdrive/controls/radar3d.py`) | 15–200m | `radard.py` → `radarState`; `gridd.py` → `stereoObjects` | ACC lead tracking + forward adjacent-lane awareness |
| `radar4d` | 4x ESP32_RADAR corner nodes (`radar4d.py`, WiFi/UDP) | 0–15m | `gridd.py` → `stereoObjects` | close-range surround maneuvering |
| `radar2d` | 4x ESP32_RADAR corner nodes (`ble_central.py`, BLE) | 0–10m | `gridd.py` → `stereoObjects` | blind-spot / lane-change gating |

`radar2d` and `radar4d` share the same 4 physical corner-node brackets —
`radar2d` reads each node's on-node-tracked BLE object stream, `radar4d`
reads the same nodes' raw CFAR point cloud over an independent WiFi/UDP
link. Two transports, one set of hardware, same corner-pose registry (see
"Driver ownership" below).

## Driver ownership — shared with VisionPilot via `hal`

Low-level wire decode lives in the `exopilot` repo's `hal` package
(`hal.drivers.radar.radar4d`), **not** duplicated here — openpilot is a
public repository and `exopilot` is not, so hardware/wire-protocol porting
can't live in this repo. This repo only owns the openpilot-specific cereal
daemon, tracker, and pointcloud pipeline (all sensor-agnostic, unchanged
from the BGT60 era):

```
../exopilot/hal/hal/drivers/radar/radar4d.py      ← UDP wire decode (RadarCornerReceiver,
                                                      CornerFrame, decode_corner_packet)
../exopilot/hal/hal/drivers/radar/bgt60tr13c.py   ← shared RadarDetection dataclass
                                                      (range_m, vel_mps, azimuth_deg,
                                                      elevation_deg, snr_db, is_static, track_id)
../exopilot/hal/hal/drivers/radar/ego_velocity.py ← RANSAC/GNC ego-speed estimation (generic)
../exopilot/hal/hal/drivers/radar/dsp.py          ← compensate_ego_velocity() (generic)

selfdrive/controls/radar4d.py           ← cereal daemon (Radar4DD, process name "radar4d")
selfdrive/controls/radar4d_tracker.py   ← KalmanTrackManager (EKF + occlusion coasting)
selfdrive/controls/radar4d_pointcloud.py ← DBSCAN cluster → shape estimation
selfdrive/controls/radar4d_geometry.py  ← corner-pose registry + transform (shared with
                                            radar2d/gridd.py), RadarStereoGeometry (FOV gating)
```

Requires `hal` installed: on-device, `exopilot/scripts/install/setup_rk3588.sh`
does this during first-boot BSP setup; on a dev PC, `pip3 install -e ../exopilot/hal`.
`radar4d.py` degrades to an idle no-op (logged once, not a crash) if `hal`
isn't importable or the UDP port fails to open.

`hal.drivers.radar.bgt60tr13c`/`dsp_gpu.py`/`dsp_gpu_kernel.py`/`intrinsics.py`
(the old SPI driver, GPU CFAR kernel, and factory intrinsics LUT) are **not
deleted** from the shared `hal` package — they may still be used by
VisionPilot (RK3576) for its own camera-bar-mounted BGT60 unit, which this
repo has no visibility into. Only this repo's *consumption* of them was
removed.

## Wire protocol (UDP, port 47000, from any of 4 corner nodes)

Each node does its own onboard range-Doppler/CFAR/AoA — no raw ADC/FFT/CFAR
on this side, `hal.drivers.radar.radar4d.decode_corner_packet()` just
parses the sensor's own binary frame:

- Packed little-endian, one datagram per frame per node, never fragmented
  (11-byte header + up to 32×10-byte detections = 331 bytes max).
- Header: `corner_id` (`0=FL,1=FR,2=RL,3=RR,0xFF=UNKNOWN` — a resistor-strap
  read at node boot; a `0xFF` frame is structurally valid, just unplaceable
  without a resolved mounting pose — skipped, not errored), `count` (0-32),
  `seq`, `capture_time_us` (monotonic per-node, not cross-node-synced),
  `protocol_version`, `dsp_time_ms`, `frame_interval_ms`.
- Detection record (×count): `range_cm` (÷100→m), `vel_mps_x100` (÷100,
  negative=approaching — **not yet bench-verified on real silicon**),
  `azimuth_deg_x10` (÷10, **sensor-local frame**, real AoA always computed),
  `elevation_deg_x10` (÷10, same caveat), `snr_db_x10` (÷10).
- No ack/retransmit — a dropped frame is just gone, by design (perishable
  stream data).
- Detections arrive in **each node's own local sensor frame**. `radar4d.py`
  transforms them into vehicle frame using `radar4d_geometry.load_corner_poses()`
  — the *same* shared registry `gridd.py` already uses for `radar2d`'s
  corner nodes (`<eop_data_root>/calibration/sensor_calibration.yaml`,
  written by visionpilot's pairing/on-road calibrator, read-only here). Same
  all-or-nothing fallback to a placeholder pose table if the registry is
  absent/incomplete.

## Data flow

```
4x ESP32_RADAR corner nodes (WiFi/UDP, port 47000, one datagram/frame/node)
    ↓  decode_corner_packet() — hal.drivers.radar.radar4d, no CFAR here (node does it onboard)
radar4d.py  →  per corner_id: look up mounting pose (load_corner_poses(), skip if
               unresolved) → corner_local_to_vehicle_frame() rotates each detection
               into vehicle frame → merge all 4 corners into one flat detection list
            →  ego-velocity compensation (liveLocationKalman → carState → HAL GNC
               radar-Doppler fallback; vehicle vs radar speed cross-checked) — unchanged
            →  RadarPointcloudProcessor (ground filter → DBSCAN cluster → shape estimate) — unchanged
               →  KalmanTrackManager (EKF + occlusion coasting, confirm/drop hysteresis) — unchanged
                 ↓  list[Track], confirmed only
              cereal "radar4d"  (Custom.Radar4D, 20Hz)
                 ↓
              gridd.py  (reads radar4d + stereoDepth + monoDetections)  — unchanged
                 ↓ _fuse_radar4d(): prefers Radar4DObject clusters (shape-aware gate),
                 ↓   falls back to raw points; FOV gate rejects clutter outside camera view
                 ↓ sets CameraObject.vRel/aRel + length/width/height/yaw,
                 ↓   boosts CameraObject.confidence from SNR/RCS + existenceProb
              cereal "stereoObjects"  (CameraObject list with velocity + shape)
                 ↓
              pathd  (lateral maneuvering in dense traffic)
```

Everything from `radar4d.py`'s ego-velocity compensation onward is
**unchanged from the BGT60 era** — that pipeline was always sensor-agnostic
(operates on a duck-typed `range_m/vel_mps/azimuth_deg/elevation_deg/
snr_db/is_static` protocol), the only rewrite was the sensor I/O feeding it.

## Pipeline architecture (Autoware-inspired) — unchanged

1. **Pointcloud** — per-corner CFAR detections, merged after vehicle-frame transform
2. **Ego-velocity compensation** — label stationary clutter vs dynamic obstacles;
   speed source is liveLocationKalman → carState → radar-Doppler GNC fallback
   (`hal.drivers.radar.estimate_ego_velocity_gnc`), with a vehicle-vs-radar
   cross-check warning on disagreement (wheel slip / tyre-size errors)
3. **Ground filter** — elevation-aware removal of road-surface returns
4. **Clustering** — DBSCAN (scipy cKDTree) in Cartesian space
5. **Shape estimation** — PCA for small clusters, L-shape search for larger ones
6. **Tracking** — constant-acceleration EKF with occlusion coasting
7. **Fusion** — gridd consumes `Radar4DObject` clusters with shape-aware association

**Removed, not carried forward**: the environment-inference stage
(precipitation/wiper/windshield-contamination/drop-off) that used to run on
BGT60's raw pre-ground-filter returns. It assumed a windshield/camera-bar-
mounted sensor with glass behind it — physically meaningless for
bumper-mounted corner nodes. The capnp fields it fed
(`precipProb`/`wiperOn`/`glassContaminated`/`weatherSeverity`/
`visionBlocked`/`dropOffHazard`/`dropOffDistM`) are unchanged in schema and
still published, just always at neutral defaults now (no schema/ordinal
churn for downstream consumers).

## Tracking: Kalman vs alpha-beta-gamma — unchanged

`radar4d_tracker.py` defaults to `KalmanTrackManager` (constant-acceleration EKF):

- **Adaptive gains** — trusts high-SNR / close-range measurements more; coasts
  through occlusion when uncertainty grows
- **Measured frame dt** — clamped to [20ms, 150ms], now measuring the UDP
  receiver's actual poll-to-poll period (WiFi + 4 independent senders jitter,
  same clamp band as the old IRQ-jitter case) instead of hardware IRQ timing
- **Crossing-yaw ghost rejection** — fast tangential tracks swept up by ego
  rotation are dropped (Autoware radar_crossing_objects_noise_filter pattern)
- **Covariance estimate** — downstream fusion can gate by uncertainty, not just
  a fixed radius
- **Occlusion handling** — confirmed tracks coast up to 10 missed frames;
  re-acquisition uses a covariance-scaled gate

`ABGTrackManager` is kept as a fallback for comparison. Still needed: unlike
`radar3d`'s NanoRadar sensor (which reports a persistent per-target ID over
UART), ESP32_RADAR's UDP wire format carries **no track ID** — every
detection is a fresh, unlabeled CFAR hit, same as BGT60's raw returns were.

## Extrinsic calibration

Two independently-sourced pieces, same factory-vs-runtime split as before:

| Component | Owner | Where it's applied |
|---|---|---|
| **Per-corner mounting pose** (x, y, yaw — dominant term) | `radar4d_geometry.load_corner_poses()`, shared `sensor_calibration.yaml` registry (visionpilot writes, openpilot reads) | `radar4d.py`'s `run()`: transforms each corner's local-frame detections into vehicle frame *before* clustering/tracking — same function (`corner_local_to_vehicle_frame()`) `gridd.py`'s `radar2d` fusion calls, extracted this session so both share one implementation |
| **On-road vehicle tilt** (pitch, yaw) | `calibrationd.py`'s `liveCalibration.rpyCalib` | `radar4d.py`: `_apply_calibration()` — unchanged, still rotates each *tracked* detection's az/el by live vehicle tilt at publish time, same as the BGT60 era |
| **FOV-gating reference mount** | `RadarMounting.load()` (`radar4d_geometry.py`), `radar_extrinsics.json` | `gridd.py`'s `RadarStereoGeometry`, used only to gate radar4d detections against the camera FOV — **now a nominal vehicle-origin reference**, not a physical sensor mount, since detections already arrive vehicle-frame-transformed by the time this gate runs. Verify `radar_extrinsics.json` doesn't still carry BGT60's old camera-bar-mount offset (pre-existing default is all-zero, so this is only a concern if that file was ever populated non-zero) |

Roll is never estimated (same convention this codebase uses everywhere:
`calibrationd.py` assumes a level mount).

## Changed files (this port)

| File | Change |
|------|--------|
| `selfdrive/controls/radar4d.py` | Rewritten sensor I/O: `RadarCornerReceiver` + corner-pose transform replaces BGT60 SPI construction/read loop. Tracker/pointcloud/publish pipeline unchanged. Environment-inference calls removed (fields stay, neutral defaults). |
| `selfdrive/controls/radar4d_geometry.py` | **NEW** `corner_local_to_vehicle_frame()` — extracted from `gridd.py`'s inline radar2d transform, now shared by both. |
| `selfdrive/gridd/gridd.py` | `_fuse_radar2d_objects` calls the extracted shared transform instead of its own inline copy. No other changes — `_fuse_radar4d*` untouched. |
| `selfdrive/controls/radar4d_calibrate.py` | **REMOVED** — BGT60-specific factory intrinsics wizard, no longer applicable. |
| `system/bluetoothd/ble_central.py` | `decode_object_datagram()` and its wire-format structs moved to `hal.drivers.radar.radar2d` (same ownership pattern) — behavior unchanged, pure relocation. |
| `../exopilot/hal/hal/drivers/radar/radar4d.py` | **NEW** — UDP wire decode (`RadarCornerReceiver`, `CornerFrame`, `decode_corner_packet`). |
| `../exopilot/hal/hal/drivers/radar/radar2d.py` | **NEW** — BLE object-datagram wire decode (`decode_object_datagram`), extracted from `ble_central.py`. |
| `../exopilot/hal/hal/platform/rk3588_pins.py` | Removed `SPI["RADAR4D"]`, `GPIO["BGT60_IRQ"/"BGT60_RST"]` — no SPI/GPIO wiring for a WiFi-attached sensor. |
| `../exopilot/kernel/dts/rk3588-lubancat-exp01.dts` | `&spi0` disabled (was BGT60-only consumer). |
| `../exopilot/kernel/rk3588/dt-overlays/exopilot01m-radar4d.dts` | **REMOVED** — IRQ/RST GPIO overlay fragment, no longer needed. |

## Custom.Radar4DPoint / Custom.Radar4DObject fields (cereal/custom.capnp)

**Unchanged** — no schema/ordinal changes were needed for this port. See the
struct definitions in `cereal/custom.capnp` directly; field semantics
(`trackId`, `rangM`, `azimuth`, `vRel`, `snrDb`, `elevation`,
`existenceProb`, `isStatic`, `dynProp`, `aRel`, plus `lengthM`/`widthM`/
`heightM`/`yawRad`/`pointCount` on `Radar4DObject`) are identical to the
BGT60 era — they were already sensor-agnostic.

## gridd.py fusion logic — unchanged

```python
# _fuse_radar4d(objects, radar4d):
if len(radar4d.objects) > 0:
    return self._fuse_radar4d_objects(objects, radar4d)   # preferred
return self._fuse_radar4d_points(objects, radar4d)       # fallback
```

No changes here — `radar4d.py`'s corner-pose transform happens *before*
publish, so gridd still sees one coherent vehicle-frame stream regardless
of how many physical corner nodes contributed to it.

## Verification

```bash
cereal_print radar4d          # tracked 4D detections at ~20Hz (confirmed tracks only)
cereal_print stereoObjects    # stereo objects — vRel should be non-zero for close targets
```

No hardware needed for decode/transform/pipeline correctness:
`../exopilot/hal/tests/test_radar4d.py` (packet decode, receiver demux/staleness),
`selfdrive/controls/tests/test_radar4d_tracker.py` (EKF + occlusion, unchanged),
`selfdrive/controls/tests/test_radar4d_pointcloud.py` (clustering + shape, unchanged),
`selfdrive/controls/tests/test_radar4d_geometry.py` (coordinate transforms,
now including the extracted `corner_local_to_vehicle_frame()`),
`selfdrive/gridd/tests/test_fuse_radar2d.py` (fusion gates, confirms the
shared-transform refactor didn't regress radar2d).

## Open items

- [ ] Bench-verify azimuth/elevation sign and velocity sign against real
      ESP32_RADAR hardware — `docs/dsp-design.md` in that repo flags all
      three as "not yet bench-verified against real silicon."
- [ ] Confirm `radar_extrinsics.json` doesn't carry a stale non-zero
      camera-bar-mount offset from the BGT60 era (see Extrinsic calibration
      above) — currently safe only because the file defaults to all-zero.
- [ ] Corner_id ↔ physical-position mapping (resistor-strap read) should be
      confirmed against the real installed harness before trusting which
      detections come from which corner.
- [ ] No pitch/roll transform is attempted for corner-local detections (2D
      yaw-only, matching `radar2d`'s existing precedent) — revisit if
      corner nodes end up mounted at a meaningful tilt.
- [ ] Tune DBSCAN `eps_m`/`min_samples`, tracker gate sizes, and
      `MAX_RANGE_M` on first real-hardware test — all currently
      best-effort/untuned for the new 4-corner geometry (previously tuned
      for a single center-mounted sensor).

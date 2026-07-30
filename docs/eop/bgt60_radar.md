# BGT60TR13C Radar — openpilot ExoPilot 01M Integration

## Radar classification

| Socket | Source | Range | Consumer | Purpose |
|--------|--------|-------|----------|---------|
| `radar3d` | car OEM CAN radar (panda/TC275) | 15–200m | `radard.py` → `radarState` | ACC, lead car tracking |
| `radar4d` | BGT60TR13C (radar4d.py, SPI0) | 0–15m | `gridd.py` → `stereoObjects` | close-range maneuvering |
| `radar2d` | *(reserved)* corner/blind-spot | — | — | future |

`radar3d` and `radar4d` are **fully independent pipelines**. `radard.py` is not touched.

## Driver ownership — shared with VisionPilot via `hal`

The BGT60TR13C register/SPI protocol driver and DSP chain live in the
`exopilot` repo's `hal` package (`hal.drivers.radar.bgt60tr13c`), **not**
duplicated in this repo — VisionPilot (02M/RK3576) uses the same driver.
This repo only owns the openpilot-specific cereal daemon, tracker, and
calibration tools:

```
../exopilot/hal/hal/drivers/radar/bgt60tr13c.py   ← SPI/GPIO acquisition, register I/O
../exopilot/hal/hal/drivers/radar/dsp.py          ← range-Doppler FFT, 2-D CA-CFAR, dual-baseline AoA
../exopilot/hal/hal/platform/rk3588_pins.py        ← SPI bus + BGT60_IRQ/BGT60_RST GPIO map

selfdrive/controls/radar4d.py           ← cereal daemon (Radar4DD, process name "radar4d")
selfdrive/controls/radar4d_tracker.py   ← KalmanTrackManager (EKF + occlusion coasting)
selfdrive/controls/radar4d_pointcloud.py ← Autoware-style pointcloud → objects pipeline
selfdrive/controls/radar4d_geometry.py  ← radar ↔ stereo camera coordinate transforms
selfdrive/controls/radar4d_calibrate.py ← intrinsic calibration wizard
```

Requires `hal` installed: on-device, `exopilot/scripts/install/setup_rk3588.sh`
does this during first-boot BSP setup; on a dev PC, `pip3 install -e ../exopilot/hal`.
`radar4d.py` degrades to an idle no-op (logged once, not a crash) if `hal`
isn't importable, or if `hal.platform.rk3588_pins.GPIO["BGT60_IRQ"/"BGT60_RST"]`
aren't yet `confirmed: True` — refusing to drive unconfirmed GPIO lines on
real hardware, same behavior as VisionPilot's `radar4d_node.py` (which
hard-fails identically unless run with `use_mock:=true`).

Import in daemon:
```python
from hal.drivers.radar import BGT60TR13C, BGT60Config, RadarPhyStatus
from hal.platform.rk3588_pins import GPIO, SPI
```

## Data flow

```
BGT60TR13C (SPI0, /dev/spidev0.0)
    ↓
hal.drivers.radar.bgt60tr13c   (acquisition + DSP: range-Doppler FFT, CA-CFAR, dual-baseline AoA)
    ↓  list[RadarDetection(range_m, vel_mps, azimuth_deg, elevation_deg, snr_db)]
radar4d.py  →  ego-velocity compensation (liveLocationKalman → carState → HAL GNC
               radar-Doppler fallback; vehicle vs radar speed cross-checked)
            →  environment inference on raw returns (precipitation, wiper motion,
               glass contamination, weather severity, drop-off → cereal radar4d msg)
            →  RadarPointcloudProcessor (ground filter → DBSCAN cluster → shape estimate)
               →  KalmanTrackManager (EKF + occlusion coasting, confirm/drop hysteresis)
                 ↓  list[Track], confirmed only
              cereal "radar4d"  (Custom.Radar4D, 20Hz)
                 ↓
              gridd.py  (reads radar4d + stereoDepth + monoDetections)
                 ↓ _fuse_radar4d(): prefers Radar4DObject clusters (shape-aware gate),
                 ↓   falls back to raw points; FOV gate rejects clutter outside camera view
                 ↓ sets CameraObject.vRel/aRel + length/width/height/yaw,
                 ↓   boosts CameraObject.confidence from SNR/RCS + existenceProb
              cereal "stereoObjects"  (CameraObject list with velocity + shape)
                 ↓
              pathd  (lateral maneuvering in dense traffic)

car OEM CAN radar (via panda/TC275)
    →  cereal "radar3d"  →  radard.py  →  "radarState"  →  controlsd/ACC
       (unchanged — renamed from liveTracks, same wire ordinal @131)
```

## Pipeline architecture (Autoware-inspired)

The radar4d pipeline applies the Autoware perception pattern to the BGT60's
sparse pointcloud (originally built for lidar, sensor-agnostic in Autoware):

1. **Pointcloud** — raw CFAR detections from `hal.drivers.radar.dsp`
2. **Ego-velocity compensation** — label stationary clutter vs dynamic obstacles;
   speed source is liveLocationKalman → carState → radar-Doppler GNC fallback
   (`hal.drivers.radar.estimate_ego_velocity_gnc`), with a vehicle-vs-radar
   cross-check warning on disagreement (wheel slip / tyre-size errors)
3. **Environment inference** — precipitation clutter, wiper motion, glass
   contamination + attenuation, weather severity, drop-off guard; runs on the
   raw returns because the evidence is what the ground filter discards
4. **Ground filter** — elevation-aware removal of road-surface returns
5. **Clustering** — DBSCAN (scipy cKDTree) in Cartesian space
6. **Shape estimation** — PCA for small clusters, L-shape search for larger ones
7. **Tracking** — constant-acceleration EKF with occlusion coasting
8. **Fusion** — gridd consumes `Radar4DObject` clusters with shape-aware association

## Tracking: Kalman vs alpha-beta-gamma

`radar4d_tracker.py` defaults to `KalmanTrackManager` (constant-acceleration EKF):

- **Adaptive gains** — trusts high-SNR / close-range measurements more; coasts
  through occlusion when uncertainty grows
- **Measured frame dt** — the real (IRQ-jittering) frame period, clamped to
  [20 ms, 150 ms], feeds the EKF prediction step instead of a nominal dt
- **Crossing-yaw ghost rejection** — fast tangential tracks swept up by ego
  rotation are dropped (Autoware radar_crossing_objects_noise_filter pattern)
- **Covariance estimate** — downstream fusion can gate by uncertainty, not just
  a fixed radius
- **Occlusion handling** — confirmed tracks coast up to 10 missed frames;
  re-acquisition uses a covariance-scaled gate

`ABGTrackManager` is kept as a fallback for comparison.

## Chirp configuration

Current tuning (`radar4d.py`):

| Parameter | Value | Rationale |
|-----------|-------|-----------|
| `n_samples` | 1280 | ~17.5 m unambiguous range (15 m gate) |
| `n_chirps` | 64 | Better velocity resolution than default 48 |
| `frame_rate_hz` | 20 | Match camera pipeline |
| `high_speed_spi` | True | Offset increased SPI volume from more chirps |
| `use_gpu` | True | Mali OpenCL CFAR via inferenced ACL backend |

Range resolution is at the 5.5 GHz hardware limit (2.7 cm).  Velocity
resolution improves with more chirps, but note: actual chirp spacing is
dominated by the software `read_fifo()` loop (~2 ms/chirp), not the RTU
register — verify `vel_mps` absolute scale on hardware.

## inferenced GPU backend

Radar CFAR routes through `InferenceClient("radar4d").acl().infer("radar_cfar")`
— the same direct-HAL pattern stereod/gridd use.  The ACL backend dispatches
to a hand-written OpenCL kernel (`dsp_gpu_kernel.py`) with CPU fallback.

No IPC schema changes are needed; the direct in-process call avoids the
serialize→queue→deserialize round trip.

## Intrinsic calibration

Factory intrinsics are owned by the exopilot HAL
(`hal/drivers/radar/intrinsics.py`: `load_intrinsics` / `save_intrinsics`);
`radar4d.py` loads the LUT from
`Paths.eop_data_root()/calibration/radar_intrinsics.json` at startup.
Mounting extrinsics are user-refined and stored by the application layer in
`Paths.eop_data_root()/calibration/radar_extrinsics.json`
(`RadarMounting.load/save` in `radar4d_geometry.py`).

Generate the intrinsics LUT with the bench wizard:

```bash
python3 selfdrive/controls/radar4d_calibrate.py
```

The wizard guides target placement at known (x, y, z) positions, captures
strongest detections, and builds a 4-band × 8-bin correction grid.  Automotive
range bands (3/6/9/12 m) replace DISP_RADAR4D's indoor 1.5/2.5/3.5 m bands.

## Changed files (this port)

| File | Change |
|------|--------|
| `cereal/custom.capnp` | `Radar4DPoint` gains `elevation`, `existenceProb`, `dynProp`, `aRel`; `Radar4DObject` struct added; `Radar4D` gains `objects` list |
| `cereal/services.py` | `radar4d` frequency `10.` → `20.` (match camera pipeline) |
| `common/params_keys.h` | Add `EOPRadar4DEnabled` (hardware-presence gate) |
| `system/manager/process_config.py` | Gate `radar4d` on `EOPRadar4DEnabled`, like `pointcloudd` |
| `selfdrive/ui/qt/offroad/eop_panel.{h,cc}` | Add `add_radar4d_toggles()`, wired into the Assistance section |
| `selfdrive/controls/radar4d.py` | Rewritten: `Radar4DD` (Class-D), 20Hz config, GPU CFAR, pointcloud pipeline, Kalman tracker |
| `selfdrive/controls/radar4d_tracker.py` | **NEW** — `KalmanTrackManager` (EKF + occlusion coasting), `ABGTrackManager` fallback |
| `selfdrive/controls/radar4d_pointcloud.py` | **NEW** — Autoware-style ground filter → DBSCAN → shape estimation |
| `selfdrive/controls/radar4d_geometry.py` | **NEW** — radar ↔ stereo camera coordinate transforms, FOV gating |
| `selfdrive/controls/radar4d_calibrate.py` | **NEW** — intrinsic calibration wizard |
| `selfdrive/controls/tests/test_radar4d*.py` | **NEW** — tracker, pointcloud, geometry, calibration tests |
| `selfdrive/gridd/gridd.py` | `_fuse_radar4d()`: objects-first fusion with shape-aware gate, FOV gating, Autoware camera-box velocity |
| `selfdrive/gridd/tests/test_fuse_radar4d.py` | **NEW** |
| `system/radar4d/` | **REMOVED** — was a duplicate of the driver now canonically in `hal.drivers.radar` |
| `../exopilot/hal/hal/drivers/radar/bgt60tr13c.py` | Rewritten against Infineon's official `sensor-xensiv-bgt60trxx` C driver; real CA-CFAR + dual-baseline AoA |
| `../exopilot/hal/hal/drivers/radar/dsp.py` | **NEW** — range-Doppler FFT, CA-CFAR, AoA |
| `../exopilot/hal/hal/platform/rk3588_pins.py` | Add `SPI["RADAR4D"]`, `GPIO["BGT60_IRQ"/"BGT60_RST"]` (unconfirmed placeholders) |
| `../exopilot/hal/hal/platform/rk3576_pins.py` | **NEW** — same shape, for VisionPilot's SPI2/gpio2 |
| `../exopilot/hal/tests/test_radar_dsp.py` | **NEW** |

## Custom.Radar4DPoint fields (cereal/custom.capnp)

```
trackId       @0 :UInt64   — stable ID (confirm/drop hysteresis, radar4d_tracker.py)
rangM         @1 :Float32  — radial distance (m); BGT60 range res = 2.7 cm
azimuth       @2 :Float32  — angle (deg, 0=forward, +left, -right); dual-baseline phase AoA
vRel          @3 :Float32  — Doppler velocity (m/s); NEGATIVE = target approaching
snrDb         @4 :Float32  — SNR dB, proxy for radar cross section (RCS)
                             motorcycle ~5-10 dB·m², car ~20 dB·m²
elevation     @5 :Float32  — angle (deg, 0=boresight, +up); dual-baseline phase AoA
existenceProb @6 :Float32  — 0-100, from tracker confirm hit-streak
isStatic      @7 :Bool     — ego-velocity compensated: true = stationary clutter
dynProp       @8 :UInt8    — ARS-style: 0=stationary, 1=moving, 2=stopped
aRel          @9 :Float32  — longitudinal relative acceleration (m/s²), negative = braking
```

## Custom.Radar4DObject fields (cereal/custom.capnp)

```
trackId       @0 :UInt64   — stable cluster/track ID
rangM         @1 :Float32  — radial distance to object center (m)
azimuth       @2 :Float32  — deg, 0=forward, +left, -right
elevation     @3 :Float32  — deg, 0=boresight, +up
vRel          @4 :Float32  — Doppler relative velocity (m/s), NEGATIVE = approaching
aRel          @5 :Float32  — longitudinal relative acceleration (m/s²)
snrDb         @6 :Float32  — peak SNR dB of cluster
existenceProb @7 :Float32  — 0-100, from cluster tracker confirm hit-streak
isStatic      @8 :Bool     — ego-velocity compensated: true = stationary clutter
dynProp       @9 :UInt8    — ARS-style: 0=stationary, 1=moving, 2=stopped
lengthM       @10 :Float32 — estimated object length (m), forward axis
widthM        @11 :Float32 — estimated object width (m), lateral axis
heightM       @12 :Float32 — estimated object height (m), vertical axis
yawRad        @13 :Float32 — estimated object heading (rad), 0=forward
pointCount    @14 :UInt8   — number of radar points in the cluster
```

## gridd.py fusion logic

```python
# _fuse_radar4d(objects, radar4d):
if len(radar4d.objects) > 0:
    return self._fuse_radar4d_objects(objects, radar4d)   # preferred
return self._fuse_radar4d_points(objects, radar4d)       # fallback

# _fuse_radar4d_objects:
for obj_msg in radar4d.objects:
    if abs(obj_msg.elevation) > _R4D_ELEV_GATE_DEG: continue
    if not self._radar4d_in_camera_fov(...): continue      # reject clutter outside FOV
    # shape-aware gate: normalized distance in object's oriented box
    # ≤1 = match; >1 = spawn new object
    # → set obj['vRel']/['aRel']/['length']/['width']/['height']/['yawRad']
    # → boost obj['confidence'] from SNR + existenceProb
```

## SPI hardware

- Bus: `/dev/spidev0.0` (SPI0, freed from MCP2518FD via DT overlay) — see
  `hal.platform.rk3588_pins.SPI["RADAR4D"]`
- IRQ + DIO3/RST GPIO: `hal.platform.rk3588_pins.GPIO["BGT60_IRQ"/"BGT60_RST"]`
  — currently the DTS overlay's PLACEHOLDER pin numbers (gpio3 PB0/PB1),
  `confirmed: False`. Still pending the LubanCat5 schematic (NAS).
- Level shifter: TXS0108E required (BGT60 is 1.8V, RK3588 GPIO is 3.3V)
- Linux `spidev` kernel module `bufsiz` must be raised above its 4096-byte
  default for this driver's burst FIFO reads (~9.2KB at the default
  `n_samples=2048`) — `hal`'s driver logs a clear warning/error via
  `_check_spidev_bufsiz()` if it can detect the configured value is too low.
- Mounting: BGT60TR13C at Y=0 (vehicle centerline), between `stereo_left`
  (-40mm) and `stereo_right` (+40mm) on the camera bar.

## DSP pipeline (`../exopilot/hal/hal/drivers/radar/{bgt60tr13c,dsp}.py`)

```
FIFO raw [n_chirps × n_samples × 3 rx, 12-bit signed]
  → mean removal (DC offset)
  → MTI IIR: α=0.5 (static clutter rejection)
  → range-Doppler FFT: rfft on range axis (ADC is real-valued, not I/Q —
      a full complex FFT produces a spurious mirror-image ghost detection
      with inverted azimuth/elevation for every real target), full FFT on
      Doppler axis
  → 2-D CA-CFAR (guard/training cells, Doppler-axis wrapped) → list[CFARHit]
  → dual-baseline phase AoA per hit: azimuth from Rx1/Rx3, elevation from
      Rx2/Rx3 (BGT60TR13C's 3 RX antennas are L-shaped, not a linear array —
      Rx3 is the shared reference for both pairs)
  → list[RadarDetection(range_m, vel_mps, azimuth_deg, elevation_deg, snr_db)]
```

Config sizing (`hal`'s `BGT60Config` defaults, see its docstring): n_samples
and adc_div are chosen so the real usable range (n_samples//2 bins, since
rfft only yields half the spectrum) clears the 0-15m target with margin,
while adc_div is lowered from a naive default to keep chirp period — and
therefore max unambiguous velocity — automotive-useful (~15 m/s) despite
the larger n_samples. Deliberately not DISP_RADAR4D's tiny embedded defaults
(32 samples/16 chirps) — that project is RAM-constrained (32KB STM32G431
SRAM); the RK3588 this driver runs on is not.

Reference: Infineon's official `sensor-xensiv-bgt60trxx` C driver (register
addresses, SPI framing, FIFO burst-read encoding) — not the unofficial
`micropython-radar-bgt60` port this driver was originally reverse-engineered
from (whose FIFO burst-trigger second byte was wrong; see
`bgt60tr13c.py::_read_single_chirp()`'s docstring for the derivation).

## Verification

```bash
cereal_print radar4d          # tracked 4D detections at ~20Hz (confirmed tracks only)
cereal_print stereoObjects    # stereo objects — vRel should be non-zero for close targets
cereal_print radarState       # car OEM ACC radar, unchanged
```

No hardware needed for driver/DSP/tracker/fusion correctness — see
`../exopilot/hal/tests/test_radar_dsp.py` (synthetic IQ, range/velocity/AoA
recovery), `selfdrive/controls/tests/test_radar4d_tracker.py` (EKF + occlusion),
`selfdrive/controls/tests/test_radar4d_pointcloud.py` (clustering + shape),
`selfdrive/controls/tests/test_radar4d_geometry.py` (coordinate transforms),
`selfdrive/controls/tests/test_radar4d_calibrate.py` (LUT building),
`selfdrive/gridd/tests/test_fuse_radar4d.py` (fusion gates).

## Open items

- [ ] Confirm `BGT60_IRQ`/`BGT60_RST` GPIO pin numbers against the LubanCat5
      schematic (NAS) and flip `hal.platform.rk3588_pins.GPIO[...]["confirmed"]` to `True`
- [ ] Confirm the RX-channel-to-antenna mapping with a known-angle target
      before trusting azimuth/elevation sign on real hardware — which
      physical FIFO channel is Rx1/Rx2/Rx3 depends on RX-enable bit order in
      the chirp config register; an off-by-one here silently swaps azimuth
      and elevation
- [ ] Confirm `spidev.bufsiz` is raised (kernel/module parameter) on the
      target image — required for the ~9.2KB burst reads at this driver's
      default `n_samples`
- [ ] Verify `vel_mps` absolute scale on hardware — actual chirp spacing is
      software-loop-dominated (~2 ms), not RTU register timing
- [ ] Tune CFAR `pfa`/guard/training cell sizes, `CLUSTER_EPS_M`,
      `MAX_ASSOC_M`, `CONFIRM_HITS`/`DROP_MISSES`, `_R4D_ELEV_GATE_DEG`,
      `_R4D_CONFIDENCE_BOOST` on first hardware test — all currently
      best-effort/untuned
- [ ] Replace `hal`'s hand-derived FSU/RSU/RTU chirp-PLL math with a
      register list generated by Infineon's `bgt60-configurator-cli` tool
      for the target chirp config, once available

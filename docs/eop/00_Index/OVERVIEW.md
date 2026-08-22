# EnhancedOpenPilot (EOP)

---

## Status

| Aspect | Status |
|--------|--------|
| **Design** | ✅ Complete |

---


**Hardware:** LubanCat5 BTB (RK3588, 4-camera) | **Base:** openpilot | **Branch:** EOP10

---

## Pipeline

```
CAMERA CAPTURE — v4l2d (20 Hz) — MIPI CSI cameras
  Dynamic discovery via sysfs (sensor-aware: OX03C10, GC4653)
  V4L2 MPLANE + zero-copy DMA-BUF (system heaps)
  road + wide_road + tele_road → VisionIPC "v4l2d" → modeld / monod
  stereo_left  (id=4) → VisionIPC "v4l2d" → gridd (depth only)
  stereo_right (id=5) → VisionIPC "v4l2d" → gridd (seg + YOLO)

  # USB/UVC camera pipeline (ExoPilot 01M):
  driver (id=2) → VisionIPC "uvcd" → driverd → driverPoseState / driverStatus
  side_left  (id=7) → VisionIPC "uvcd" → sided → blindSpotAlert
  side_right (id=8) → VisionIPC "uvcd" → sided → blindSpotAlert

  # Hailo perception pipeline (camera-tier PCIe accel, when present):
  wide_road (1.7mm) ──┐
  road (8mm) ─────────┼──► Hailo ────► monod ──► gridd (fused BEV)
  tele_road (16mm) ───┘   13/26 TOPS      (calibrated objects)
                            YOLO
                          SceneSeg
```

**MONOD** — multi-camera perception (requires camera-tier PCIe accel — Hailo-8/DX-M1, selfdrive/monod/)
- `monod.py`              Multi-camera YOLO + SceneSeg (tele_road only)   Hailo-8
- `calibration_fusion.py` Fuse with drive_vision + stereo       CPU A76
  - drive_vision.rknn: Ground truth object positions (0-150m)
  - liveCalibration: Camera extrinsics correction
  - stereo depth: Close-range refinement (0-80m)
- Publishes: `monoDetections` (calibrated 3D objects), `monoSegments` (tele_road only), `monoFeatures`
- Camera coverage:
  - Wide road (1.7mm): 150° FOV, 0-30m - YOLO only (cut-in detection)
  - Road (8mm): 40° FOV, 0-100m - YOLO only (PP-LiteSeg on RKNN for road seg)
  - Tele road (16mm): 20° FOV, 80-300m - YOLO + SceneSeg (long-range lead car + drivable path)
- TOPS Budget: 3x YOLO (~10.5) + 1x SceneSeg (~1.5) = ~12 TOPS, leaving ~1 TOPS for VisionPilot AI

**STEREOD** — 2D stereo depth (selfdrive/stereod/)
- `stereod.py`     GPU SGM → 2D disparity map                 Mali GPU (OpenCL)
- `yolo_2d.py`     YOLO-nano  → 2D detections (right camera)  NPU Core 1
- `ppliteseg.py`   PP-LiteSeg → 19-class segmentation masks   NPU Core 1
- Publishes: `stereoDepth` (disparity + confidence, 20Hz)
- Publishes: `stereoDetections` (2D YOLO boxes, 20Hz)
- Publishes: `stereoSegments` (PP-LiteSeg masks, 20Hz)
- **NO 3D reconstruction here** — downstream consumers do lazy BEV or dense 3D
- **NO CPU FALLBACK** — GPU fault → IMMEDIATE_DISABLE

**DRIVERD** — driver monitoring / DMS (selfdrive/driverd/, all platforms)
- `driverd.py`       Driver monitoring daemon
- Backend: Hailo-8 SCRFD (camera-tier PCIe accel present) → OpenCV Haar fallback → steering torque only (no accel)
- `MountCalibrator` Auto-learns camera mount yaw/pitch during driving
- `AttentionTracker` UN R79 Cat B1 timing: 15s → 30s → 60s → `tooDistracted`
- Publishes: `driverPoseState` (10Hz), `driverStatus` (2Hz), `ttsRequest` (on critical)
- Triggers: DDSC speed cap, hazard lights, standstill latch (unconscious detection)

**SIDED** — side camera perception (selfdrive/sided/, ExoPilot 01M & 02)
- `sided.py`       Side camera daemon (20Hz, ignition-gated)
- `hailo_side_detector.py` YOLOv8-nano on Hailo-8 @ 640×640, via `inferenced`
  IPC (`InferenceClient(use_ipc=True)` → `submit_job`) — shared with `reard`
  (rear camera), never a direct `client.hailo()`/`VDevice`, since the two
  daemons run concurrently against one physical Hailo-8
- `bev_reprojector.py` Ground-plane BEV reprojection (advisory only)
- `simple_tracker.py` + `handover_manager.py` Cross-camera tracking
- Publishes: `sideDetections` (BEV objects), `sideStatus` (health)
- Publishes: `blindSpotAlert` → fused with vehicle BSD → blocks lane changes
- **Advisory only** — uncalibrated extrinsics, NOT for trajectory planning

**GRIDD** — lazy BEV perception (selfdrive/gridd/)
- `gridd.py`       Consumes 2D disparity from stereod
- `lazy_bev.py`    Lazy 2D→3D reprojection → occupancy grid   CPU A76
  - Only reprojects pixels needed for BEV (efficient)
  - Probabilistic Bayes filter for temporal consistency
- `multi_camera_fusion.py` Fuses stereo + monod (if available)
- Publishes: `gridObjects` (BEV occupancy grid, 20Hz)
- **CRITICAL path** — ADAS depends on this

**POINTCLOUDD** — 3D reconstruction (selfdrive/pointcloudd/)
- Subscribes: `stereoDepth` (2D disparity), `stereoSegments` (PP-LiteSeg), `fusedPosition`
- `reconstructor_3d.py` GPU reprojection (Mali OpenCL) → dense XYZ
- `semantic_fusion.py`  2D labels → 3D semantic points
- `semantic_filter.py`  Remove vegetation, dynamic objects
- `adaptive_voxel.py`   Variable density downsampling (road=15cm, curb=2cm)
- `feature_extractor.py` Poles/corners/curbs for ICP
- Publishes: `pointcloudProcessed` (3D + semantics + geo-tags, 5Hz) → surfaced, coordinationd (SGM module)
- Saves: PCD files to SD card (`/data/media/0/pointclouds/`)
- **NON-CRITICAL** — fleet data only, ADAS continues if this fails

**GLOBALD** — Localization with OSM + SGM Fusion (selfdrive/coordinationd/)
- Consolidates OSM localization + SGM localization + position fusion
- **OSMLocalizerModule:** Map-match GPS to OSM road network (own cache)
- **SGMLocalizerModule:** ICP match stereo pointcloud to SGM maps
- **FusionEngine:** Fuses GNSS + OSM + SGM using ECEF coordinates
- Subscribes: `gpsLocationExternal`, `livePose`, `pointcloudProcessed`, `mapData` (optional)
- Publishes: `fusedPosition` (ECEF position for pathd/navd)
- **NON-CRITICAL** — navigation-grade position at 5Hz, doesn't affect 20Hz control loop
- **Replaces:** osm_localizer + sgm_localizer (merged into coordinationd)

**SURFACED** — surface perception + SQSC (selfdrive/surfaced/)
- Subscribes: `pointcloudProcessed` (from pointcloudd), `imuStates`, `gpsLocation`
- `bev_extractor.py`     3D point cloud → BEV drivable area
- `anomaly_detector.py`  Detect bumps/potholes from point cloud
- `surface_quality.py`   Estimate roughness from point distribution
- GPS history lookup → marks learned-rough cells (SQSC predictive)
- Publishes: `drivableArea` (BEV grid, 20Hz) → gridd (enhances costmap)
- Publishes: `surfaceStatus` (shocks + quality, 20Hz) → SQSC
- **NON-CRITICAL** — gridd continues with stereo-only if surfaced fails

**SQSC** — surface speed controller (selfdrive/controls/lib/sqsc.py, runs in plannerd)
- Subscribes: `surfaceStatus` from surfaced, `gpsLocation` for predictive history
- Phase 1: Immediate shock response (speed limit for shock_duration_s)
- Phase 2: Predictive — GPS history lookup 50-300m ahead
- Phase 3: Real-time quality limit from roughness score
- Phase 4: Record to `surface_quality.db` (with traffic jam guard)
- Traffic jam guard: skips recording when slow + close lead + no roughness + in curve

**PATHD** — policy layer  (selfdrive/pathd/)
- `track.py`    BEV blobs → stable tracked IDs
- `predict.py`  constant-velocity → 3s projection
- `pathd.py`    collision avoidance → enhancedTrajectory

Safety stack (in order):
1. Layer 1a: groundd road edges (groundd not implemented — pass-through)
2. Layer 1b: stereoGround.leftBoundary/rightBoundary (active)
3. Layer 2:  gridObjects occupancy grid collision
4. Layer 3:  lane line warning (modelV2)

Subscribes: gridObjects, stereoGround, drivingModelData, carState, navInstruction

**MODELD** — driving NN  (selfdrive/modeld/)
- road + wide_road → path, lead detection, lanes
- Publishes: modelV2, drivingModelData

**CONTROLSD** — control loop (selfdrive/controls/)
- Subscribes enhancedTrajectory from pathd
- EOP override: critical → accel cap -3.0 m/s²; warning → accel cap from speedAdjustment
- Publishes: carControl, controlsState (100 Hz)

---

## Tiered Vision Architecture

EOP is designed to scale across different hardware grades by separating features based on their camera dependencies.

### 1. Core Vision (Road/Wide road 8mm Only)
These baseline features are available on all RK3588 hardware grades.

| Feature | Description | Priority | Status | Doc |
|---------|-------------|----------|--------|-----|
| **ALCC** | Always Lane Centering Control - baseline lateral | P0 | ✅ Done | [ALCC.md](../03_Software/Controllers/ALCC.md) |
| **TJA** | Traffic Jam Assist - smooth stop-and-go acceleration | P1 | ✅ Done | [TJA.md](../03_Software/Controllers/TJA.md) |
| **VTSC** | Vision Turn Speed Control - curve slowing (0-150m) | P2 | ✅ Done | [VTSC.md](../03_Software/Controllers/VTSC.md) |
| **LCA** | Lane Change Assist - nudgeless auto-lane-change | P3 | ✅ Done | [LCA.md](../03_Software/Controllers/LCA.md) |
| **DLAT** | Dynamic Lateral - Laneful/Laneless switching | P4 | ✅ Done | [DLAT.md](../03_Software/Controllers/DLAT.md) |
| **DLON** | Dynamic Longitudinal - Chill/Experimental switching | P5 | ✅ Done | [DLON.md](../03_Software/Controllers/DLON.md) |
| **MTSC** | Map Turn Speed Control - OSM-based slowing (150-500m) | P6 | ✅ Done | [MTSC.md](../03_Software/Controllers/MTSC.md) |
| **MSLC** | Map Speed Limit Control - OSM speed limit compliance | P7 | ✅ Done | [MSLC.md](../03_Software/Controllers/MSLC.md) |
| **SOC** | Smart Offset Controller - truck nudge | P8 | ✅ Done | [SOC.md](../03_Software/Controllers/SOC.md) |
| **RED** | Road Edge Detection - laneless guardrail | P9 | ✅ Done | [RED.md](../03_Software/Controllers/RED.md) |
| **TLSC** | Traffic Light Speed Control - stereo YOLO + HSV color detection | P10 | ✅ Done | [TLSC.md](../03_Software/Controllers/TLSC.md) |
| **NSLC** | Navigation Speed Limit Control - Mapbox/navd speed limits | P11 | ✅ Done | `selfdrive/controls/lib/nslc.py` |
| **DDSC** | Driver Distraction Speed Control + unconscious detection | P12 | ✅ Done | [DDSC.md](../03_Software/Controllers/DDSC.md) |

**Reference Forks:** FrogPilot, sunnypilot, dragonpilot, carrotpilot
**Full Status:** IMPLEMENTATION_STATUS.md

### 2. Enhanced Vision (Stereo Pipeline)
Requires dual-CSI hardware sync and additional NPU compute.

| Feature | Description | Status | Doc |
|---------|-------------|--------|-----|
| **GRIDD** | Occupancy grid mapping and BEV projection | ✅ Done | `selfdrive/gridd/` |
| **PATHD** | Policy daemon with LatNudge/LonNudge stereo-enhanced controllers | ✅ Done | `selfdrive/pathd/` |
| **RECORDD** | 2K high-resolution DVR recording from stereo | ✅ Done | `selfdrive/recordd/` |
| **LatNudge** | Stereo-based lateral obstacle nudge | ✅ Done | [LAT_NUDGE.md](../03_Software/Controllers/LAT_NUDGE.md) |
| **LonNudge** | Stereo-based forward-distance speed trim | ✅ Done | [LON_NUDGE.md](../03_Software/Controllers/LON_NUDGE.md) |
| **RCD** | Road Condition Detection - wet/snow/ice detection | ✅ Done | `selfdrive/controls/lib/rcd.py` |
| **AEB** | Automatic Emergency Braking (urban-focused, 0-50m) | ✅ Done | `selfdrive/controls/lib/aeb.py` |
| **BSD** | Blind Spot Detection - stereo-based with chime + TTS | ✅ Done | `selfdrive/controls/lib/bsd.py` |
| **DDSC** | Driver Distraction Speed Control + unconscious detection | ✅ Done | [DDSC.md](../03_Software/Controllers/DDSC.md) |

**Full Index:** ENHANCED_FEATURES_INDEX.md

---

## NPU Core Allocation: Maximize to 85% Safety Line

### Strategy: No Core Exclusive, Optimize for 85% Utilization

**Core 0 is NOT exclusive** - distribute load to maximize NPU budget up to 85% safety line.

| Platform | Cores | Per-Core | 85% Target | Core 0 Strategy |
|----------|-------|----------|------------|-----------------|
| **RK3588** | 3 | 2.0 TOPS | 1.7 TOPS | driving_vision only (2.0 = 100%, latency critical) |

### RK3588 (ExoPilot 01M): 3 cores × 2 TOPS = 6 TOPS

| Core | Models | TOPS | Utilization | Status |
|------|--------|------|-------------|--------|
| 0 | driving_vision | 2.0 | 100% | Latency-critical, keep exclusive |
| 1 | SceneSeg (1.0) + PP-LiteSeg (0.5) | 1.5 | 75% | Under 85% ✅ |
| 2 | Policy (0.5) + YOLOs (0.8) | 1.3 | 65% | Under 85% ✅ |

**Total: 4.8 / 6.0 TOPS (80%)**

```python
from openpilot.selfdrive.modeld.runners.rknn_platform import get_core_mask
# RK3588: policy → Core 2 (yolo shared)
core_mask = get_core_mask('policy')
```

Optional Hailo-8 PCIe (26 TOPS): unlocks multi-camera inference and advanced vision processing.

---

## CPU Core Allocation (BSP Best Practice)

Following RK3588 BSP guidelines:

| Cores | Type | Processes |
|-------|------|-----------|
| **A76 (0-3)** | Big Cores | **All ADAS functions**: v4l2d, modeld, gridd, pathd, controlsd |
| **A55 (4-7)** | Little Cores | **Driver/I/O only**: imud, socketd, pigeond, hardwared, ui, mapd, navd |

**Principle:** Keep A55 free for system/driver functions. Don't spread ADAS across little cores.

---

## Naming Conventions

See [NAMING_CONVENTIONS.md](../01_Core/NAMING_CONVENTIONS.md) for detailed file, class, and process naming standards.

Quick reference:
- **Daemons**: Files end with `d` (`pathd.py`), classes end with `D` (`PathD`)
- **Controllers**: `<feature>.py` classes (e.g., `VTSC`, `DLAT`)
- **UI/Utilities**: No `d` suffix (`ui.py`, `deleter.py`)
- **Docs**: Daemon docs in `ALL_CAPS_D.md`, controllers in `ALL_CAPS.md`

---

## Daemon Reference

| Daemon | Role | Rate | Doc |
|--------|------|------|-----|
| `v4l2d` | All camera capture (dynamic discovery, MPLANE) | 20 Hz | `system/v4l2d/` |
| `uvcd` | USB/UVC camera capture (driver, side cameras) | 20 Hz | `system/uvcd/` |
| `tripd` | Trip statistics (distance, time, engagement) | 1 Hz | `selfdrive/tripd/` |
| `modeld` | Driving NN (vision + policy) — NPU 0 + 1/2 | 20 Hz | `selfdrive/modeld/` |
| `calibrationd` | Camera pitch/yaw calibration from cameraOdometry | 4 Hz | [CALIBRATIOND.md](../03_Software/Daemons/CALIBRATIOND.md) |
| `camera_calibrationd` | Multi-camera calibration (EXO2/EXO3, cross-camera validation) | 4 Hz | [CALIBRATIOND.md](../03_Software/Daemons/CALIBRATIOND.md) |
| `gridd` | Depth + SceneSeg + PP-LiteSeg + Dual YOLO + BEV — NPU 1 | 20 Hz | `selfdrive/gridd/` |
| `pathd` | Track + predict + collision avoidance | 20 Hz | `selfdrive/pathd/` |
| `controlsd` | Lateral + longitudinal control + EOP accel override | 100 Hz | `selfdrive/controls/` |
| `imud` | LSM6DS3 accel/gyro (104 Hz) + RK3588 temp (2 Hz) | — | `system/imud/` |
| `socketd` | SocketCAN (SBU-aware dynamic remap) ↔ cereal + comfort-bounded Layer 1 Safety + explicit AEB envelope | always-on | `system/socketd/` |
| `hardwared` | Thermal, power, fan | 2 Hz | `system/hardware/` |
| `bluetoothd` | BLE SPP server — NavPilot companion app integration (NCP v4.1) | always-on | `system/bluetoothd/` |
| `recordd` | DVR ring-buffer recording from stereo_right | always-on | `selfdrive/recordd/` |
| `pigeond` | GPS (NEO-M8U-06B UDR, RK3588) | always-on | `system/ubloxd/` |
| `navd` | On-device Valhalla routing; NavPilot turn-by-turn via BLE SPP | always-on | `selfdrive/navd/` |
| `driverd` | Driver monitoring — Hailo SCRFD + attention tracker | 20 Hz | `selfdrive/driverd/` *(not implemented)* |
| `sided` | Side camera BSD/RCTA (camera-tier accel) | 20 Hz | `selfdrive/sided/` |
| `stereod` | Stereo depth (SGM + semantic fusion) | 20 Hz | `selfdrive/stereod/` |
| `surfaced` | Road surface monitoring (IMU + stereo shock detection) | 20 Hz | `selfdrive/surfaced/` |
| `pointcloudd` | 3D reconstruction + geo-tagging (feeds coordinationd SGM) | 5 Hz | `selfdrive/pointcloudd/` |
| `mapd` | OSM map data for MTSC/MSLC (feeds coordinationd optionally) | 1 Hz | `selfdrive/mapd/` |
| `coordinationd` | OSM + SGM localization + fusion | 5 Hz | `selfdrive/coordinationd/` |
| `mcapd` | Parallel MCAP logging for Foxglove visualization | 20 Hz | `system/mcapd/` |
| `obd2d` | OBD2/UDS vehicle diagnostics over BLE | 1 Hz | `selfdrive/obd2d/` |
| `rtkd` | RTK GPS NTRIP correction client | always-on | `system/rtkd/` |
| `micd` | Microphone capture and sound pressure level | 10 Hz | `system/micd/` |
| `monod` | Multi-camera perception with Hailo-8 (camera-tier accel) | 20 Hz | `selfdrive/monod/` |
| `inferenced` | Unified NPU/GPU/RGA/MPP inference backend | always-on | `system/inferenced/` |
| `soundd` | TTS + alert tone generation | — | `selfdrive/soundd/` |
| `spkd` | I2S speaker output (PCM5102A / MAX98357A) | — | `system/spkd/` |
| `wdgd` | Hardware watchdog | — | `system/wdgd/` |
| `stated` | State persistence | — | `system/stated/` |

---

## EOP Parameters (`common/params_keys.h`)

| Key | Default | Purpose |
|-----|---------|---------|
| `EOPStereoEnabled` | 1 | Stereo vision pipeline |
| `EOPGridEnabled` | 1 | BEV occupancy grid |
| `EOPLatALCC` | 1 | Lateral ALCC offset |
| `EOPLatRoadEdgeDetection` | 1 | Road edge detection |
| `EOPLCAControllerEnabled` | 1 | Lane change assist |
| `EOPDLPCurvesEnabled` | 1 | DLAT curves |
| `EOPDLATMode` | 2 | Dynamic Lateral: 0=Laneful, 1=Laneless, 2=Dynamic |
| `EOPDLONMode` | 2 | Dynamic Longitudinal: 0=ACC, 1=E2E, 2=Dynamic |
| `EOPALCCAllowAlways` | 0 | ALCC unconditional |
| `EOPALCCHoldAtStandstill` | 0 | ALCC at stop |
| `EOPVTSCEnabled` | 0 | Vision turn speed control (0-150m, with learned speeds) |
| `EOPMTSCEnabled` | 0 | Map turn speed control (150-500m, with learned speeds) |
| `EOPMSLCEnabled` | 0 | Map speed limit control |
| `EOPCurveSpeedLearnEnabled` | 1 | Curve speed learning (for VTSC/MTSC) |
| `EOPSurfaceEnabled` | 1 | Road surface monitoring |
| `EOPSQSCEnabled` | 1 | Surface Quality Speed Controller |
| `EOPMSLCOffsetPercent` | 0 | Speed limit offset (percent) |
| `EOPMSLCOffsetFixed` | 0 | Speed limit offset (km/h) |
| `EOPTSCTargetLatAccel` | 1.8 | TSC comfort threshold (m/s²) |
| `EOPAccelerationProfile` | "normal" | eco/normal/sport/traffic accel profile |
| `EOPAdaptiveGapEnabled` | 0 | Dynamic gap modulation |
| `EOPTJAEnabled` | 0 | Traffic Jam Assist toggle |
| `EOPTJAMaxHoldMinutes` | 10 | TJA max hold at standstill |
| `EOPMapdEnabled` | 0 | Background MAPD service |
| `EOPNavEnabled` | 0 | On-device Valhalla routing daemon |
| `EOPNavVoiceEnabled` | 1 | Navigation voice announcements (Azure) |
| `EOPTTSAlertsEnabled` | 1 | Alert voice announcements (Azure) |
| `EOPTTSVoice` | "en_US-amy-medium" | Azure voice model (no local Piper) |
| `EOPNavBleEnabled` | 0 | Enable NavPilot BLE SPP navigation |

| `EOPBluetoothCANInterface` | canmpc | SocketCAN for BLE/OBD2 (semantic name; HAL resolves to can0/can1 at runtime) |
| `EOPRecordEnabled` | 1 | DVR on/off |
| `EOPRecordDurationMin` | 5 | Ring buffer depth (min) |
| `EOPRecordSnapOnCrash` | 1 | Auto-snap on exit |
| `EOPRecordSnap` | 0 | Manual snap trigger |
| `EOPTripTotalDistance` | 0.0 | Lifetime total distance (meters) |
| `EOPTripTotalDrives` | 0 | Lifetime total drives count |
| `EOPTripUptimeOnroad` | 0.0 | Lifetime onroad time (seconds) |
| `EOPTripLifetimeEngagementRatio` | 0.0 | Lifetime engagement percentage |
| `EOPRCDEnabled` | 1 | Road condition detection |
| `EOPAEBEnabled` | 1 | Automatic emergency braking |
| `EOPGlobaldEnabled` | 1 | Enable global position daemon |
| `EOPOsmLocalizerEnabled` | 0 | Enable OSM road matching |
| `EOPSGMLocalizerEnabled` | 0 | Enable SGM geometry matching |
| `EOPSGMMode` | live | SGM mode: live, map, fused |
| `EOPMultiCameraCalibEnabled` | 0 | Use camera_calibrationd (multi-camera) |
| `EOPFactoryCalibrated` | 0 | Device has factory ChArUco calibration |

### SocketD Safety Parameters (Layer 1)

| Key | Default | Purpose |
|-----|---------|---------|
| `EOPSafetyMaxSteeringAngle` | 2700 | Max steering angle (0.1 deg, 270°) |
| `EOPSafetyMaxSteeringRate` | 200 | Max steering rate (0.1 deg/s, 20°/s) |
| `EOPSafetyMaxAngleError` | 240 | Max angle error (0.1 deg, 24°) |
| `EOPSafetyMaxAccel` | 340 | Max acceleration (Tesla units, ~1.6 m/s²) |
| `EOPSafetyMinAccel` | 312 | Normal-control floor (Tesla units, ~-2.5 m/s²); explicit AEB has a separately checked envelope |
| `EOPSafetyHeartbeatTimeout` | 200 | Heartbeat timeout (ms) |
| `EOPSafetyEventLogEnabled` | 1 | Enable safety event logging |
| `EOPSafetyEventLogPath` | "/data/safety_violations.log" | Safety log file path |
| `EOPSafetyEventLogMaxEvents` | 1000 | Max events to keep in log |

---

## Calibration Parameters (Binary)

| Key | Type | Purpose |
|-----|------|---------|
| `FactoryCalibrationParams` | BYTES | Factory intrinsics (IMMUTABLE) |
| `CalibrationParams` | BYTES | Runtime extrinsics (pitch/yaw/height) |
| `CameraCalibrationParams` | BYTES | Multi-camera runtime extrinsics |

See [CALIBRATION_PIPELINE.md](../03_Software/Architecture/CALIBRATION_PIPELINE.md) for full documentation.

---

## Bluetooth / BLE

The device exposes two transports simultaneously via `system/bluetoothd/`:

### Classic SPP (RFCOMM)
Any ELM327-compatible OBD scanner app (Torque, Car Scanner, OBD2 Scan) connects via Bluetooth Classic SPP. The device acts as an ELM327 bridge: AT commands and hex PIDs are forwarded to `obd2d` (CAN bus), and raw hex responses are returned. NavPilot can also use this path (Android only — iOS blocks Classic SPP for non-MFi apps).

### BLE GATT — Nordic UART Service (NUS)
NavPilot (`../navpilot`) connects via BLE GATT using the Nordic UART Service:
- RX characteristic (`6E400002…`): NavPilot writes NCP v4.1 frames
- TX characteristic (`6E400003…`): device notifies NCP v4.1 frames at 10 Hz

NCP v4.1 carries: navigation commands, interpreted vehicle data, driving profiles, auth tokens, telemetry (speed, gear, OBD data, nav instructions, route geometry).

### NCP v4.1 Clean-Separation Architecture
```
obd2d ──(raw OBD bytes)──► SPP/GATT ──► NavPilot
NavPilot ──(Mode 22 decoded, subscription)──► CMD_VEHICLE_DATA ──► adaptd
adaptd ──(adaptiveDrivingState)──► controlsd
```
obd2d never interprets proprietary Mode 22 PIDs. All Chinese EV decoding (BYD, MG, GAC, etc.) lives in NavPilot.

### Device Pairing
Device advertises as `EXOPILOT 01` / `EXOPILOT 02M` (set via `EOPDeviceName` param). During OS pairing, the BlueZ agent uses `DisplayOnly` capability → phone user must type the 6-digit code shown on the ADAS screen (Passkey Entry, not just tap-to-confirm).

See `docs/eop/04_Integration/BLE_DESIGN.md` for full architecture.

---

## Tracking Status (as of 2026-03-19)

| Feature / Daemon | Status | Implementation Reference |
|------------------|--------|--------------------------|
| **v4l2d** (Capture) | ✅ Done | `system/v4l2d/v4l2d.py` |
| **gridd** (Perception) | ✅ Done | `selfdrive/gridd/gridd.py` |
| **pathd** (Policy) | ✅ Done | `selfdrive/pathd/pathd.py` |
| **modeld** (Driving) | ✅ Done | `selfdrive/modeld/modeld.py` |
| **socketd** (CAN) | ✅ Done | `system/socketd/socketd.py` |
| **pigeond** (GPS) | ✅ Done | `system/ubloxd/pigeond.py` |
| **bluetoothd** (BLE) | ✅ Done | `system/bluetoothd/bluetoothd.py` |
| **recordd** (DVR) | ✅ Done | `selfdrive/recordd/recordd.py` |
| **hardwared** (Thermal) | ✅ Done | `system/hardware/hardwared.py` |
| **tripd** (Statistics) | ✅ Done | `selfdrive/tripd/tripd.py` |
| **NPU Allocation** | ✅ Done | `selfdrive/modeld/runners/rknn_runner.py` |
| **ALCC** | ✅ Done | `selfdrive/controls/controlsd.py` |
| **TJA** | ✅ Done | `selfdrive/controls/lib/longcontrol.py` |
| **VTSC** | ✅ Done | `selfdrive/controls/lib/vtsc.py` |
| **LCA** | ✅ Done | `selfdrive/controls/lib/desire_helper.py` |
| **DLAT** | ✅ Done | `selfdrive/controls/lib/dlat.py` |
| **DLON** | ✅ Done | `selfdrive/controls/lib/dlon.py` |
| **MTSC** | ✅ Done | `selfdrive/controls/lib/mtsc.py` |
| **MSLC** | ✅ Done | `selfdrive/controls/lib/mslc.py` |
| **SOC** | ✅ Done | `selfdrive/pathd/soc.py` |
| **RED** | ✅ Done | `selfdrive/controls/lib/red.py` |
| **LatNudge** | ✅ Done | `selfdrive/pathd/lat_nudge.py` (PathD module) |
| **LonNudge** | ✅ Done | `selfdrive/pathd/lon_nudge.py` (PathD module) |
| **RCD** | ✅ Done | `selfdrive/controls/lib/rcd.py` |
| **AEB** | ✅ Done | `selfdrive/controls/lib/aeb.py` |
| **GLOBALD** | ✅ Done | `selfdrive/coordinationd/coordinationd.py` (OSM+SGM localization + fusion) |
| **NAVD** | ✅ Done | `selfdrive/navd/navd.py` |
| **MCAPD** | ✅ Done | `system/mcapd/mcapd.py` (Foxglove MCAP logging) |
| **OBD2D** | ✅ Done | `selfdrive/obd2d/obd2d.py` (OBD2/UDS over BLE) |
| **RTKD** | ✅ Done | `system/rtkd/rtkd.py` (NTRIP RTK corrections) |
| **MICD** | ⚠️ Partial | `system/micd/micd.py` (Microphone capture and SPL) |
| **MONOD** | ✅ Done | `selfdrive/monod/monod.py` (3-camera Hailo perception) |

---

## Integration with Stock Vehicle Features

EOP replaces certain stock vehicle features while preserving others:

### Replaced Features (when EOP is engaged)
- **Stock Lane Keep Assist (LKA)** → Replaced by EOP ALC/ALCC
- **Stock Lane Departure Warning (LDW)** → Replaced by EOP LDW
- **Stock ACC** → Replaced by EOP longitudinal control (on supported cars)

### Preserved Stock Features
EOP preserves all other vehicle safety features:
- Stock FCW (EOP FCW operates in addition)
- Stock AEB (automated emergency braking)
- Auto high-beam
- Blind spot warning (BSM)¹
- Side collision warning

¹ **Note:** EOP LCA uses stock BSM signals from vehicle CAN. When a camera-tier PCIe accelerator is present, `sided` provides additional stereo-based blind spot monitoring via side cameras.

### EOP-Specific Feature Integration

| EOP Feature | Stock System Interaction |
|-------------|-------------------------|
| **ALCC** | Replaces stock LKA when engaged; works with stock BSM |
| **LCA** | Uses stock BSM CAN signals for gap check |
| **VTSC/MTSC** | Modifies EOP target speed, respects stock ACC limits |
| **TJA** | Smooths EOP longitudinal control at low speeds |
| **AEB** | EOP collision mitigation uses confirmed built-in forward 77 GHz radar; corner radar is advisory-only |

---

## Document Index

| Document | Purpose |
|----------|---------|
| IMPLEMENTATION_STATUS.md | Consolidated implementation status |
| STRATEGY.md | Reference fork analysis and roadmap |
| [NAMING_CONVENTIONS.md](../01_Core/NAMING_CONVENTIONS.md) | Coding standards |

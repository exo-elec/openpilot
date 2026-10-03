# `selfdrive/sided`

SideD — Side Camera Perception Daemon (Blind Spot / RCTA)

## Overview

SideD provides side-mounted blind-spot camera perception for **ExoPilot 01M (RK3588)**. It consumes BGR frames from `uvcd` VisionIPC, runs YOLOv8 object detection on the SoC's RKNN NPU (core 1), reprojects detections into BEV (Bird's Eye View), and publishes tracked objects for BSD/RCTA warnings and lane-change blocking.

**Platform:** ExoPilot 01M (requires `EOPSideCamerasEnabled=True`)

**Where the models run:**
- **Object detection** (what BSD and lane-change blocking use): YOLOv8n on RKNN, in-process (`yolo_detector.py`). Left and right both run every frame, 20 Hz each. It does not depend on the PCIe card.
- **Semantic segmentation** of both side cameras: on the camera-tier card (Hailo-8 or DX-M1M) in `segd`, published on `monoSegments` (`side_left`, `side_right`). Without a card it simply stops.

## Cameras

| Camera | Source | Resolution | FOV | Position | Purpose |
|--------|--------|------------|-----|----------|---------|
| `side_left` | AHD → UVC | 1280×720 | 98° H (vendor "120°" is diagonal), 2.8 mm fisheye | Left front fender, side turn-lamp / badge spot, y +0.9 m, ~0.85 m high | Left blind spot |
| `side_right` | AHD → UVC | 1280×720 | same | Right front fender, y −0.9 m | Right blind spot |

Both cameras look back along the car, turned ~15° outward (yaw 165° / 195°)
and ~10° down. These are nominal values: calibration solves the real angles
and height, clamped to the mounting limits in exopilot's HAL.

## Architecture

```
┌─────────────┐     VisionIPC      ┌─────────────────────────────────────┐
│  side_left  │───────────────────▶│                                     │
│  (uvcd)     │   NV12 @ 20Hz      │              SideD                  │
└─────────────┘                    │  ┌─────────────────────────────┐   │
                                   │  │ YoloDetector (RKNN core 1)  │   │
┌─────────────┐     VisionIPC      │  │ (YOLOv8-nano @ 640×640)     │   │
│  side_right │───────────────────▶│  └─────────────────────────────┘   │
│  (uvcd)     │   NV12 @ 20Hz      │              │                     │
└─────────────┘                    │              ▼                     │
                                   │  ┌─────────────────────────────┐   │
                                   │  │ BEVReprojector              │   │
                                   │  │ (ground-plane intersection) │   │
                                   │  └─────────────────────────────┘   │
                                   │              │                     │
                                   │              ▼                     │
                                   │  ┌─────────────────────────────┐   │
                                   │  │ SimpleTracker + HandoverMgr │   │
                                   │  │ (cross-camera UID handover) │   │
                                   │  └─────────────────────────────┘   │
                                   │              │                     │
                                   └──────────────┼─────────────────────┘
                                                  │
                          ┌───────────────────────┴───────────────────────┐
                          ▼                                               ▼
                   ┌─────────────┐                               ┌─────────────┐
                   │sideDetections│                              │blindSpotAlert│
                   │  (20 Hz)    │                               │  (20 Hz)    │
                   └─────────────┘                               └─────────────┘
```

## Pipeline

1. **Capture** — `uvcd` publishes BGR frames via VisionIPC (streams 7/8)
2. **Inference** — `YoloDetector` runs YOLOv8-nano (`yolo_640`, SoC-tagged `.rknn`) at 640×640 on RKNN core 1, left and right every frame (20 Hz each)
   - Classes: person, bicycle, motorcycle, car, van, bus, truck
   - Confidence threshold: 0.35, NMS: 0.45
   - No RKNN model found → `SideProcessor.detect()` placeholder returns `[]`
3. **BEV Reprojection** — `bev_reprojector.py` projects 2D bbox bottom-centre to ground plane
   - Uses calibrated or default `SideCameraGeometry` (yaw/pitch/height)
   - **Advisory only** — uncalibrated extrinsics are unsafe for trajectory planning
4. **Tracking** — `SimpleTracker` (per-camera) + `HandoverManager` (cross-camera)
   - Maintains global UIDs across left→right handover
5. **Publishing** — `sideDetections` + `sideStatus` + `blindSpotAlert`

## Output Messages

| Message | Fields | Rate | Consumers |
|---------|--------|------|-----------|
| `sideDetections` | `detections[]` (x, y, confidence, className) | 20 Hz | `gridd` (advisory), `modeld` |
| `sideStatus` | `enabled`, `fault`, `numTracks` | 20 Hz | `selfdrived` |
| `blindSpotAlert` | `leftDetected`, `rightDetected`, `leftAlertLevel`, `rightAlertLevel` | 20 Hz | `selfdrived`, `controlsd`, `modeld` |

## Calibration

Side cameras have **no FOV overlap** with the forward array, so they are
calibrated against the front camera's *motion* instead. Road features must
move as the front camera's odometry predicts for the right tilt, heading and
height.

exopilot's `hal.calibration` owns side/rear calibration; openpilot only
feeds it. `camera_calibrationd` (`side_rear_calibration.py`) hands it the
front camera's `cameraOdometry` (scaled by wheel speed, moved to base_link),
the road camera's own calibration, and the uvcd frames. It stores each
camera's link in `sensors_tf.yaml` once it reads calibrated, and reports
progress in `calibrationState.cameraCalibrations` (ROS roll/pitch/yaw with
pitch positive down, and height above the road). sided/reard place their
cameras from that tf tree (`bev_reprojector.hal_geometry`). Without hal (a
dev PC) they fall back to the nominal mounting in `bev_reprojector.py`.

At startup the geometry comes from `hal_geometry()` (the tf tree, including
any stored calibration), then `CalibrationStorage`, then
`make_default_geometry()`.

## Configuration

| Parameter | Type | Default | Description |
|-----------|------|---------|-------------|
| `EOPSideCamerasEnabled` | bool | `true` | Master side camera toggle |
There is no eGPU side path: the USB eGPU runs openpilot's Chestnut driving
model only. Side detection is RKNN; side segmentation is the camera-tier card
(`segd`, `seg_side`). See `docs/eop/05_Features/CAMERA_ACCEL_TIER.md`.

## Files

| File | Description |
|------|-------------|
| `sided.py` | Main daemon — VisionIPC client, inference orchestration, publisher |
| `yolo_detector.py` | YOLOv8-nano on RKNN (`YoloDetector`), shared YOLO decode; used by sided, reard, monod (road, 02M telephoto) |
| `bev_reprojector.py` | Ground-plane BEV reprojection + `SideCameraGeometry` |
| `simple_tracker.py` | Kalman-filter-based 2D tracker per camera |
| `handover_manager.py` | Cross-camera global UID assignment |

## Safety Notes

- Side camera detections are **advisory only** until extrinsics are calibrated
- `blindSpotAlert` is fused with vehicle-native BSD (`carState.leftBlindspot` / `rightBlindspot`)
- Lane change blocking uses OR logic: vehicle BSD OR EOP side-camera BSD
- Side detections do **NOT** feed into `gridd` main occupancy grid (uncalibrated = unsafe)

# `selfdrive/sided`

SideD — Side Camera Perception Daemon (Blind Spot / RCTA)

## Overview

SideD provides side-mounted blind-spot camera perception for **ExoPilot 01M (RK3588)**. It consumes BGR frames from `uvcd` VisionIPC, runs per-camera inference (when a Hailo-8 PCIe module is present), reprojects detections into BEV (Bird's Eye View), and publishes tracked objects for BSD/RCTA warnings and lane-change blocking.

**Platform:** ExoPilot 01M (requires `EOPSideCamerasEnabled=True`)

**Important distinction:**
- **Without Hailo-8**: Side camera *video streams* work, but AI object detection does not. There is no CPU fallback policy.
- **With Hailo-8**: Full AI-powered blind-spot detection with YOLOv8-nano.

## Cameras

| Camera | Source | Resolution | FOV | Position | Purpose |
|--------|--------|------------|-----|----------|---------|
| `side_left` | UVC | 1280×720 | ~120° | Left fender, +0.85m | Left blind spot |
| `side_right` | UVC | 1280×720 | ~120° | Right fender, −0.85m | Right blind spot |

Both cameras are rear-pointing, yawed 30° outward (150° / 210° yaw), parallel to ground.

## Architecture

```
┌─────────────┐     VisionIPC      ┌─────────────────────────────────────┐
│  side_left  │───────────────────▶│                                     │
│  (uvcd)     │   NV12 @ 20Hz      │              SideD                  │
└─────────────┘                    │  ┌─────────────────────────────┐   │
                                   │  │ HailoSideDetector           │   │
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
2. **Inference** — `HailoSideDetector` runs YOLOv8-nano (`models/hef/yolov8n.hef`, downloaded by `download_models.sh`) at 640×640 *(Hailo-8 PCIe module required)*
   - Classes: person, bicycle, motorcycle, car, van, bus, truck
   - Confidence threshold: 0.35, NMS: 0.45
   - **ExoPilot 01M**: No fallback — `SideProcessor.detect()` returns `[]` by design; Hailo-8 required for actual inference
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

Side cameras have **no FOV overlap** with the forward array, so traditional feature-matching calibration fails. Instead:

1. Forward camera VO gives ego trajectory
2. IMU provides angular velocity
3. Road surface is a known plane from forward lane detection
4. Side camera optical flow of ground features must match predicted flow
5. Non-linear optimization solves for yaw/pitch/height

Calibration is loaded from `CalibrationStorage.get_merged_calibration()` at daemon startup. Falls back to `make_default_geometry()` if uncalibrated.

## Configuration

| Parameter | Type | Default | Description |
|-----------|------|---------|-------------|
| `EOPSideCamerasEnabled` | bool | `true` | Master side camera toggle |
| `EOPSideEGPUMode` | string | `off` | Optional `off`/`shadow` tinygrad eGPU side-model validation |

Rear eGPU validation is separately owned by `reard` and controlled by
`EOPRearEGPUMode`; it does not share the side model session. See
`docs/eop/05_Features/EGPU_CAMERA_SHADOW.md`.

## Files

| File | Description |
|------|-------------|
| `sided.py` | Main daemon — VisionIPC client, inference orchestration, publisher |
| `hailo_side_detector.py` | Hailo-8 YOLOv8-nano detector via `InferenceClient` |
| `bev_reprojector.py` | Ground-plane BEV reprojection + `SideCameraGeometry` |
| `simple_tracker.py` | Kalman-filter-based 2D tracker per camera |
| `handover_manager.py` | Cross-camera global UID assignment |

## Safety Notes

- Side camera detections are **advisory only** until extrinsics are calibrated
- `blindSpotAlert` is fused with vehicle-native BSD (`carState.leftBlindspot` / `rightBlindspot`)
- Lane change blocking uses OR logic: vehicle BSD OR EOP side-camera BSD
- Side detections do **NOT** feed into `gridd` main occupancy grid (uncalibrated = unsafe)

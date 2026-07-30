# `system/uvcd`

UVCD — USB/UVC Camera Daemon

## Overview

`uvcd` captures frames from **USB Video Class (UVC)** cameras using OpenCV + V4L2. It handles cameras that are not connected to the Rockchip ISP / MIPI CSI pipeline:

- **Driver driver camera** — USB webcam for driver monitoring (all platforms)
- **Side cameras** — USB UVC cameras via USB 3.0 hub (RTS5411S) (ExoPilot 01M & 02M)

Unlike `v4l2d` (which manages MIPI CSI cameras through the ISP), `uvcd` uses pure V4L2 via `cv2.VideoCapture(device, cv2.CAP_V4L2)` with no ISP involvement, no HDR, and no I2C sensor configuration.

## Cameras

| Camera | Platform | Device Path | Resolution | FPS | VisionIPC Stream | Purpose |
|--------|----------|-------------|------------|-----|------------------|---------|
| `driver` | All | `/dev/video-driver` | 640×480 | 20 | `VISION_STREAM_DRIVER` | Driver face monitoring |
| `side_left` | ExoPilot 01M & 02M | `/dev/video-side-left` | 1280×720 | 20 | `VISION_STREAM_SIDE_LEFT` | Left blind spot |
| `side_right` | ExoPilot 01M & 02M | `/dev/video-side-right` | 1280×720 | 20 | `VISION_STREAM_SIDE_RIGHT` | Right blind spot |
| `rear_camera` | ExoPilot 01M & 02M | `/dev/video-rear` | 640×480 | 20 | `VISION_STREAM_REAR` | Rear view (reverse) |

**Device discovery:** `uvcd` scans a fallback list of device nodes (`/dev/video10`, `/dev/video20`, `/dev/video11`, `/dev/video21`, etc.) if the canonical symlinks are not present.

## Architecture

```
┌─────────────────────────────────────────────────────────────────────────────┐
│                              uvcd (20 Hz)                                    │
├─────────────────────────────────────────────────────────────────────────────┤
│                                                                              │
│  ┌─────────────┐   V4L2 (OpenCV)    ┌─────────────────────────────────────┐ │
│  │ driver      │───────────────────▶│                                     │ │
│  │ (USB/UVC)   │   MJPG → BGR       │         VisionIPC Server            │ │
│  └─────────────┘                    │            "uvcd"                   │ │
│                                     │                                     │ │
│  ┌─────────────┐   V4L2 (OpenCV)    │  ┌─────────────┐ ┌─────────────┐  │ │
│  │ side_left   │───────────────────▶│  │DRIVER       │ │SIDE_LEFT    │  │ │
│  │ (UVC)       │   MJPG → BGR       │  │(640×480)    │ │(1280×720)   │  │ │
│  └─────────────┘                    │  └─────────────┘ └─────────────┘  │ │
│                                     │  ┌─────────────┐                   │ │
│  ┌─────────────┐   V4L2 (OpenCV)    │  │SIDE_RIGHT   │                   │ │
│  │ side_right  │───────────────────▶│  │(1280×720)   │                   │ │
│  │ (UVC)   │   MJPG → BGR       │  └─────────────┘                   │ │
│  └─────────────┘                    └─────────────────────────────────────┘ │
│                                                                              │
└─────────────────────────────────────────────────────────────────────────────┘
```

## VisionIPC Server

- **Server name:** `"uvcd"`
- **Buffer format:** BGR uint8 (`width × height × 3`)
- **Streams published:**
  - `VISION_STREAM_DRIVER` (id=2) — driver camera
  - `VISION_STREAM_SIDE_LEFT` (id=7) — side left
  - `VISION_STREAM_SIDE_RIGHT` (id=8) — side right
  - `VISION_STREAM_REAR` (id=9) — rear camera

## Consumers

| Consumer | Stream | Purpose |
|----------|--------|---------|
| `driverd` | `VISION_STREAM_DRIVER` | Driver monitoring / driver monitoring |
| `sided` | `VISION_STREAM_SIDE_LEFT`, `VISION_STREAM_SIDE_RIGHT` | Blind spot detection |
| `ui` | `VISION_STREAM_REAR` | Full-screen rear camera overlay (reverse gear) |

## Published Messages

| Message | Publisher | Content | Rate |
|---------|-----------|---------|------|
| `driverCameraState` | uvcd | Frame metadata (frameId, timestamp) | 20 Hz |
| `leftCameraState` | uvcd | Side left frame metadata | 20 Hz |
| `rightCameraState` | uvcd | Side right frame metadata | 20 Hz |
| `rearCameraState` | uvcd | Rear camera frame metadata | 20 Hz |

## Health Monitoring

`uvcd` writes boolean params for each camera:
- `DriverCameraReady`
- `SideLeftCameraReady`
- `SideRightCameraReady`

These are consumed by `selfdrived` for fault detection.

## Configuration

| Parameter | Type | Default | Description |
|-----------|------|---------|-------------|
| `EOPDriverCameraEnabled` | bool | `false` | Enable driver driver camera stream |
| `EOPDriverCameraWidth` | int | `640` | Face cam width |
| `EOPDriverCameraHeight` | int | `480` | Face cam height |
| `EOPSideCamerasEnabled` | bool | `true` | Enable side camera streams |
| `EOPSideCameraWidth` | int | `1280` | Side cam width |
| `EOPSideCameraHeight` | int | `720` | Side cam height |
| `EOPSideCamerasSwapped` | bool | `false` | Swap left/right side cameras |

## Relationship to v4l2d

| Aspect | v4l2d | uvcd |
|--------|-------|------|
| Interface | MIPI CSI | USB / UVC |
| ISP | RKISP (AE/AWB/HDR) | None |
| Sensors | OX03C10, GC4653 | USB webcam, UVC camera |
| Cameras | road, wide_road, tele_road, stereo_left, stereo_right | driver, side_left, side_right, rear_camera |
| Server name | `"v4l2d"` | `"uvcd"` |
| Consumers | modeld, gridd, monod | driverd, sided |

## Files

| File | Description |
|------|-------------|
| `uvcd.py` | Main daemon — V4L2 capture, VisionIPC server, health monitoring |

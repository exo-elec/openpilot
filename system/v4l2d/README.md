# `system/v4l2d`

V4L2 Camera Daemon - All cameras (road, wide_road, stereo_left, stereo_right)

## Overview

v4l2d handles camera capture for **all cameras** using V4L2 (Video4Linux2) API:
- **Road camera** (OX03C10) - forward driving view
- **Wide road camera** (OX03C10) - wide-angle view  
- **Stereo left** (GC4653) - depth estimation
- **Stereo right** (GC4653) - depth estimation

It publishes frames via VisionIPC for consumption by:
- `modeld` - consumes road/wide_road
- `gridd` - consumes stereo_left/right (for depth_map and pp_liteseg)

## ISP Integration (Hardware 3A)

v4l2d integrates with the Rockchip ISP for hardware-accelerated:
- **Auto-Exposure (AE)** - Center-weighted metering with anti-flicker
- **Auto-White Balance (AWB)** - Gray world algorithm
- **Auto-Focus (AF)** - Contrast detection (trigger mode)

### ISP Features
| Feature | Status | Description |
|---------|--------|-------------|
| Hardware AE | ✅ | 5x5 zone statistics, anti-flicker (50/60Hz) |
| Hardware AWB | ✅ | RGB statistics, color temperature estimation |
| IQ Tuning | ✅ | JSON-based per-sensor tuning files |
| HDR Merge | ✅ | 2-frame HDR for OX03C10 |
| 3D NR | ✅ | Temporal noise reduction |
| Lens Shading | ✅ | Vignette correction |

### IQ Tuning Files

IQ tuning files ship from the closed `exopilot` HAL package and are installed
to `/etc/iqfiles` by `exopilot/scripts/install/setup_rk3588.sh`:

- `ox03c10_default_default.json` — Road/Wide road tuning
- `gc4653_CMK-OT2117-PC1_30IRC-F16.json` — Stereo pair tuning (CMK module)
- `gc4653_YT10120_30IRC-4M-F20.json` — Stereo pair tuning (YT module)

Source files live in `../exopilot/hal/hal/tuning/isp/`. They are **not**
copied into the public EOP10 repo; the BSP setup script installs them on-device.

RKIAQ loads the correct file automatically by sensor name from `/etc/iqfiles`.
Custom per-unit tuning can be placed there and will override the factory files.

## Architecture

```
┌─────────────────────────────────────────────────────────────┐
│                    v4l2d (All Cameras)                       │
├─────────────────────────────────────────────────────────────┤
│                                                              │
│  ┌─────────────┐    VisionIPC     ┌─────────────┐           │
│  │ road        │ ─────────────────►│   modeld    │           │
│  │ (OX03C10)   │                   │             │           │
│  └─────────────┘                   └─────────────┘           │
│                                                              │
│  ┌─────────────┐    VisionIPC     ┌─────────────┐           │
│  │ wide_road   │ ─────────────────►│             │           │
│  │ (OX03C10)   │                   │             │           │
│  └─────────────┘                   └─────────────┘           │
│                                                              │
│  ┌─────────────┐    VisionIPC     ┌─────────────────────┐   │
│  │ stereo_left │ ─────────────────►│       gridd         │   │
│  │ (GC4653)    │                   │  ┌───────────────┐  │   │
│  └─────────────┘                   │  │  depth_map.py │  │   │
│                                    │  │  (SGBM XYZ)   │  │   │
│  ┌─────────────┐    VisionIPC     │  └───────────────┘  │   │
│  │ stereo_right│ ─────────────────►│  ┌───────────────┐  │   │
│  │ (GC4653)    │                   │  │pp_liteseg.py  │  │   │
│  └─────────────┘                   │  │ (road mask)   │  │   │
│                                    │  └───────────────┘  │   │
│                                    │  ┌───────────────┐  │   │
│                                    │  │  lazy_bev.py  │  │   │
│                                    │  │ (occupancy)   │  │   │
│                                    │  └───────────────┘  │   │
│                                    └─────────────────────┘   │
│                                                              │
└─────────────────────────────────────────────────────────────┘
```

## Output

- **VisionIPC Streams**:
  - `VISION_STREAM_ROAD` - Road camera NV12
  - `VISION_STREAM_WIDE_ROAD` - Wide road camera NV12
  - `VISION_STREAM_STEREO_LEFT` (4) - Stereo left NV12
  - `VISION_STREAM_STEREO_RIGHT` (5) - Stereo right NV12
  - `VISION_STREAM_SIDE_LEFT` (7) - Side left NV12
  - `VISION_STREAM_SIDE_RIGHT` (8) - Side right NV12

- **cereal messaging**:
  - `roadCameraState` - Road camera metadata
  - `wideRoadCameraState` - Wide road camera metadata
  - `stereoCameraState` - Stereo left metadata
  - `stereoCameraStateRight` - Stereo right metadata
  - `sideCameraState` - Side camera metadata

## Camera Configuration

v4l2d uses **dynamic sensor-aware discovery** to handle non-deterministic `/dev/videoN` numbering on RK3588. It scans sysfs for nodes matching specific sensor strings:

### Front Camera Bar (MIPI CSI)

| Camera | Sensor | Default Node(s) | Discovery String | Y Offset |
|--------|--------|-----------------|------------------|----------|
| Road | OX03C10 | `/dev/video0` | `ox03c10` + `mainpath` | 0 mm |
| Wide road | OX03C10 | `/dev/video1` | `ox03c10` + `mainpath` | +80 mm |
| Stereo Left | GC4653 | `/dev/video22` | `gc4653` + `mainpath` | +80 mm |
| Stereo Right | GC4653 | `/dev/video31` | `gc4653` + `mainpath` | 0 mm |

### Side Cameras (UVC)

| Camera | Type | Default Node | Y Offset | Purpose |
|--------|------|--------------|----------|---------|
| side_left | UVC | `/dev/video-side-left` | +850 mm | Left blind spot |
| side_right | UVC | `/dev/video-side-right` | −850 mm | Right blind spot |

**Features:**
- **MPLANE Support:** Fully implements V4L2 Multi-Plane API for Rockchip ISP compatibility.
- **Zero-Copy DMA-BUF:** Uses system heaps for zero-copy frame passing to VisionIPC.
- **Unified HAL:** RK3588 and RK3588 are supported via the same driver stack.

## VisionIPC Server Names

- **"v4l2d"** - Publishes road, wide_road, stereo_left, stereo_right
- **"uvcd"** - Publishes driver, side_left, side_right, rear_camera

## Consumers

| Consumer | Consumes | Purpose |
|----------|----------|---------|
| `modeld` | road, wide_road | Driving model inference |
| `gridd` | stereo_left, stereo_right | Depth + segmentation + BEV grid |
| `gridd.depth_map` | stereo_left, stereo_right | SGBM stereo XYZ |
| `gridd.pp_liteseg` | stereo_left (rectified) | Road segmentation |
| `sided` | side_left, side_right | Blind spot detection |
| `monod` | road, wide_road | RKNN NPU YOLO + SceneSeg |

## See Also

- `system/uvcd/` — USB/UVC camera daemon (driver face camera, side cameras)
- `selfdrive/driverd/` — Driver monitoring (consumes `VISION_STREAM_DRIVER` from uvcd)
- `selfdrive/sided/` — Side camera perception (consumes `VISION_STREAM_SIDE_LEFT/RIGHT` from uvcd)
- `selfdrive/gridd/` — Consumes stereo frames, produces BEV grid
- `selfdrive/gridd/depth_map.py` — SGBM depth from stereo
- `selfdrive/gridd/pp_liteseg.py` — Road segmentation
- `common/hardware/rk3588/hardware.py` — Camera configuration

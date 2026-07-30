# `selfdrive/monod`

MonoD — 2-Camera Mono Detection Daemon (RKNN NPU)

## Overview

MonoD runs neural network inference on **ExoPilot 01M (RK3588)** using the on-chip RKNN NPU. It consumes frames from `v4l2d` VisionIPC, runs YOLO object detection and PP-LiteSeg scene segmentation, and publishes calibrated detections fused with stereo depth and driving model outputs.

**Platform:** RK3588 only (`EOPMonoDEnabled=True`)

## Cameras

| Camera | Stream | Sensor | Lens | FOV | Range | Purpose |
|--------|--------|--------|------|-----|-------|---------|
| `wide_road` | `VISION_STREAM_WIDE_ROAD` | OX03C10 | 1.7mm | 150° | 0–30m | Cut-in detection |
| `road` | `VISION_STREAM_ROAD` | OX03C10 | 8.0mm | 40° | 0–100m | Lead car + lane keep |

## Architecture

```
┌─────────────────────────────────────────────────────────────────────────────┐
│                              MonoD (20 Hz)                                   │
├─────────────────────────────────────────────────────────────────────────────┤
│                                                                              │
│  ┌─────────────┐  ┌─────────────┐                                          │
│  │  wide_road  │  │    road     │                                          │
│  │  (VisionIPC)│  │  (VisionIPC)│                                          │
│  └──────┬──────┘  └──────┬──────┘                                          │
│         │                │                                                  │
│         └────────────────┘                                                  │
│                          ▼                                                   │
│              ┌─────────────────────┐                                         │
│              │   RKNN NPU (Core 2) │                                         │
│              │  ┌───────────────┐  │                                         │
│              │  │ YOLO (road)   │  │  object detection                       │
│              │  └───────────────┘  │                                         │
│              │  ┌───────────────┐  │                                         │
│              │  │ SceneSeg      │  │  road scene segmentation                │
│              │  │ (road)        │  │                                         │
│              │  └───────────────┘  │                                         │
│              │  ┌───────────────┐  │                                         │
│              │  │ PP-LiteSeg    │  │  wide-camera fast segmentation          │
│              │  │ (wide, 10Hz)  │  │                                         │
│              │  └───────────────┘  │                                         │
│              └─────────────────────┘                                         │
│                          │                                                   │
│                          ▼                                                   │
│              ┌─────────────────────┐                                         │
│              │  MultiCameraFusion  │  road + wide track fusion               │
│              └─────────────────────┘                                         │
│                          │                                                   │
│              ┌───────────┴───────────┐                                       │
│              ▼                       ▼                                       │
│     ┌─────────────┐         ┌─────────────┐                                 │
│     │monoDetections│         │monoSegments │                                 │
│     │  (20 Hz)    │         │  (20 Hz)    │                                 │
│     └─────────────┘         └─────────────┘                                 │
│                                                                              │
└─────────────────────────────────────────────────────────────────────────────┘
```

## Models

| Model | Hardware | Input | Output |
|-------|----------|-------|--------|
| YOLO | RKNN NPU | 640×640 RGB | bbox + class |
| SceneSeg | RKNN NPU | 640×640 RGB | road mask |
| PP-LiteSeg | RKNN NPU | 320×320 RGB | fast segmentation mask |

## Output Messages

| Message | Content | Rate | Consumers |
|---------|---------|------|-----------|
| `monoDetections` | Fused objects (x, y, class, confidence) | 20 Hz | `gridd` |
| `monoSegments` | Road/wide segmentation masks | 20 Hz | `gridd` |
| `monoStatus` | Enabled/fault state | 20 Hz | — |

## Files

| File | Description |
|------|-------------|
| `monod.py` | Main daemon — VisionIPC client, RKNN inference orchestration, publisher |
| `calibration_fusion.py` | Multi-source depth fusion + camera geometry |

## Configuration

| Parameter | Type | Default | Description |
|-----------|------|---------|-------------|
| `EOPMonoDEnabled` | bool | `true` | Master monod toggle |

## Safety Notes

- RKNN NPU is on-chip (RK3588) — always available, no optional accelerator dependency
- Detections are fused with stereo depth and driving model for redundancy
- `monoDetections` feeds into `gridd` main occupancy grid (calibrated = safe for planning)

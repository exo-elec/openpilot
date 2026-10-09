# `selfdrive/monod`

MonoD — 2-Camera Mono Detection Daemon (RKNN NPU)

## Overview

MonoD runs neural network inference on **ExoPilot 01M (RK3588)** using the on-chip RKNN NPU. It consumes frames from `v4l2d` VisionIPC, runs YOLOv8 object detection, and publishes calibrated detections fused with stereo depth and driving model outputs. Semantic segmentation is handled by `segd` on the camera-tier card (Hailo-8 / DX-M1M).

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
│              │  │ YOLO (road)   │  │  object detection + traffic lights      │
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
│     ┌─────────────┐                                                        │
│     │monoDetections│                                                        │
│     │  (20 Hz)    │         (monoSegments from segd on card)                │
│     └─────────────┘                                                        │                                 │
│                                                                              │
└─────────────────────────────────────────────────────────────────────────────┘
```

## Models

| Model | Hardware | Input | Output |
|-------|----------|-------|--------|
| YOLOv8 | RKNN NPU | 640×640 RGB | bbox + class + confidence |
| Traffic lights | post-process | YOLO output | traffic light state + confidence |

## Output Messages

| Message | Content | Rate | Consumers |
|---------|---------|------|-----------|
| `monoDetections` | Fused objects (x, y, class, confidence, traffic lights) | 20 Hz | `gridd` |
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

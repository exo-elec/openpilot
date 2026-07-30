# Models Directory

This directory contains compiled model files for EOP (ExoPilot).

## Quick Start

Download all models:
```bash
./download_models.sh
```

Or download specific platform:
```bash
./download_models.sh rknn   # Rockchip NPU models only
./download_models.sh hailo  # Hailo-8 models only
```

## Model Sources

### 1. RKNN Model Zoo (Rockchip NPU)

**Repository:** https://github.com/airockchip/rknn_model_zoo

| Model | Format | Input Size | Purpose |
|-------|--------|------------|---------|
| PP-LiteSeg | ONNX | 512x512 | Semantic segmentation |
| YOLOv8n | ONNX | 640x640 | Object detection |

**Download:**
```bash
./download_models.sh rknn
```

### 2. Hailo Model Zoo (Hailo-8 NPU)

**Repository:** https://github.com/hailo-ai/hailo_model_zoo

| Model | Format | Input Size | Hardware | Download URL |
|-------|--------|------------|----------|--------------|
| YOLOv8n | HEF | 640x640 | Hailo-8 | `hailo8/yolov8n.hef` |
| YOLOv5n | HEF | 640x640 | Hailo-8 | `hailo8/yolov5n.hef` |
| YOLOv11n | HEF | 640x640 | Hailo-8 | `hailo8/yolov11n.hef` |
| **SCRFD 2.5G** (face DMS) | HEF | 640x640 | Hailo-8 | `hailo8/scrfd_2.5g.hef` |

### Face DMS pipeline (VisionPilot only — `driverd` *not implemented* in openpilot)

`driverd` uses **SCRFD 2.5G** for driver monitoring and computes head pose
(yaw/pitch/roll) on the CPU via OpenCV `solvePnP` over SCRFD's 5 keypoints
(eyes, nose, mouth corners). No second Hailo model is needed.

**Camera mounting (flexible — but pick one and calibrate the offset):**
the driver-facing USB UVC camera should ideally be at the **top-front of
the windscreen**, directly in front of the driver, pointed slightly
downward — but this position can be physically blocked by the sun visor.
Other supported mounts: **A-pillar (mid- or lower-height)** and **dashboard
center / lower windscreen** looking upward. PnP returns yaw/pitch *relative
to the camera*, so any off-axis mount produces a constant bias. The daemon
subtracts a configurable **mount offset** (params `EOPDriverMountYawDeg` and
`EOPDriverMountPitchDeg`) before comparing to the attention thresholds.

Typical offsets (degrees):

| Mount | yaw | pitch |
|-------|-----|-------|
| Top-front windscreen, dead ahead of driver | 0 | 0 |
| Left A-pillar (LHD), mid-height | +25 | 0 |
| Right A-pillar (RHD), mid-height | -25 | 0 |
| Dashboard center / lower windscreen, looking up | 0 | -15 |

Calibrate once per install: ask the driver to look straight at the road,
read the reported yaw/pitch from `facePoseState`, and set the params to
those values (so corrected output is ~0 when forward).

**Night / low-light operation:** the camera carries an IR LED illuminator
and switches to **grayscale (near-IR) capture** at night. The Hailo backend
detects single-channel input and replicates it across 3 channels before
SCRFD inference. SCRFD was trained on RGB faces, so detection mAP drops
~5–10% on IR — still well above what `AttentionTracker` needs at a 25° yaw
threshold. The 5 SCRFD landmarks (eyes, nose, mouth corners) remain
reliably localized under IR, so PnP head-pose is unaffected.

**Sunglasses:** the choice of face pose over eye-gaze is what makes this
DMS sunglasses-invariant. SCRFD detects most faces wearing shades fine;
the eye keypoints land on lens centers ≈ true eye positions, so PnP
yaw/pitch accuracy degrades from ~5–10° to ~10–15° — still well inside
the 25° / 20° thresholds. Extreme wraparound shades that defeat SCRFD
entirely fall back to the steering-torque engagement signal.

**Direct Download (Hailo-8):**
```bash
# YOLOv8n for Hailo-8
wget https://hailo-model-zoo.s3.eu-west-2.amazonaws.com/ModelZoo/Compiled/v2.14.0/hailo8/yolov8n.hef

# YOLOv5n for Hailo-8
wget https://hailo-model-zoo.s3.eu-west-2.amazonaws.com/ModelZoo/Compiled/v2.14.0/hailo8/yolov5n.hef

# SCRFD 2.5G for Hailo-8 (face DMS)
wget https://hailo-model-zoo.s3.eu-west-2.amazonaws.com/ModelZoo/Compiled/v2.18.0/hailo8/scrfd_2.5g.hef
```

**Download via script:**
```bash
./download_models.sh hailo
```

### 3. Internal Models (EOP)

| Model | Format | Size | Purpose |
|-------|--------|------|---------|
| driving_vision | RKNN | 35.6 MB | Main driving model |
| driving_policy | RKNN | 8.2 MB | Driving policy model |

## Directory Structure

```
models/
├── download_models.sh      # Unified download script
├── hef/                    # Hailo-8 models (.hef)
│   └── yolov8n.hef
├── onnx/                   # ONNX source models
│   ├── pp_liteseg_cityscapes.onnx
│   └── yolov8n.onnx
├── rknn/                   # Rockchip NPU models (.rknn)
│   ├── driving_policy.rknn
│   └── driving_vision.rknn
└── README.md
```

## Runtime Location

Models should be installed at:
```
/data/openpilot/models/
```

Or use the local development path:
```
models/  (this directory)
```

## Converting ONNX to RKNN

For RKNN models, convert from ONNX:

```bash
python tools/convert_models_to_rknn.py \
    --input models/onnx/yolov8n.onnx \
    --output models/rknn/yolov8n.rknn \
    --target rk3588
```

## References

- **RKNN Model Zoo:** https://github.com/airockchip/rknn_model_zoo
- **Hailo Model Zoo:** https://github.com/hailo-ai/hailo_model_zoo
- **Hailo Models S3:** https://hailo-model-zoo.s3.eu-west-2.amazonaws.com/

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

SCRFD 2.5G (face detection for `driverd`'s DMS pipeline) was previously
fetched here. Removed — this hardware has no driver-facing camera, and
`driverd`'s face-DMS is VisionPilot-only anyway (it is *not implemented* in
openpilot). Re-add `hailo8/scrfd_2.5g.hef` if a driver camera is ever fitted.

**Direct Download (Hailo-8):**
```bash
# YOLOv8n for Hailo-8
wget https://hailo-model-zoo.s3.eu-west-2.amazonaws.com/ModelZoo/Compiled/v2.14.0/hailo8/yolov8n.hef

# YOLOv5n for Hailo-8
wget https://hailo-model-zoo.s3.eu-west-2.amazonaws.com/ModelZoo/Compiled/v2.14.0/hailo8/yolov5n.hef
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
├── onnx/                   # ONNX models (.onnx) — Chestnut's big model only;
│   │                       # this dev PC does not run the driving model via
│   │                       # ONNX Runtime
│   └── big_driving_supercombo.onnx
├── rknn/                   # Rockchip NPU models (.rknn)
│   ├── driving_policy.rknn
│   └── driving_vision.rknn
├── axmodel/                # reserved — no AX-M1/AXCL backend implemented yet
├── dxnn/                   # reserved — DeepX DX-M1 backend exists, no models yet
└── README.md
```

Folders are named by file format, not backend brand — see
`MODEL_MANIFEST.md`'s "Folder naming" section before adding a new one.

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
- **DeepX DX Model Zoo** (`dxnn/`, reserved): https://github.com/DEEPX-AI/dx-modelzoo
- **Axera AXCL / axmodel** (`axmodel/`, reserved): https://github.com/AXERA-TECH
  — LLM-on-AX650 specifically: https://github.com/AXERA-TECH/ax-llm

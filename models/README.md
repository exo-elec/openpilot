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
| TwinLiteNet+ Large (drivable area + lanes) | HEF / DXNN | 384x640 | Hailo-8 / DX-M1M | in neither zoo — built by `tools/card_drivable_model.py` |

The camera-tier card runs drivable-area segmentation (TwinLiteNet+ Large,
1.94 M params, one Hailo-8 context) for every camera; object detection
(YOLOv8) runs on RKNN. Both cards run the same network, compiled from one
ONNX. Weights: TwinLiteNetPlus's pretrained `large.pth` (Google Drive link in
its README), or a fine-tune from `tools/finetune_drivable.py`.

No face model (SCRFD): ExoPilot has no driver-facing camera and no camera
DMS. The card runs segmentation only.

**Build (both cards):** one command on an x86-64 Linux machine with Hailo's
DFC and/or DeepX's DX-COM installed — fetches the upstream weights (or pass
`--weights`, e.g. a fine-tune), runs every step, installs into `models/`:
```bash
python3 tools/card_drivable_model.py all --images <ExoPilot frames>
```
Or step by step:
```bash
python3 tools/card_drivable_model.py export --weights large.pth   # torch + onnx, any host
python3 tools/card_drivable_model.py calib --images <frames>      # ExoPilot road frames
python3 tools/card_drivable_model.py check --overlays /tmp/ov     # ONNX sanity check
python3 tools/card_drivable_model.py compile-hailo --verify       # Hailo DFC, x86-64
python3 tools/card_drivable_model.py compile-dx                   # DeepX DX-COM, x86-64
cp build/card_drivable/twinlitenet_plus_large_384x640.hef  models/hef/
cp build/card_drivable/twinlitenet_plus_large_384x640.dxnn models/dxnn/
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
├── hef/                    # Hailo-8 models (.hef): drivable area
│   └── twinlitenet_plus_large_384x640.hef   # built, not downloaded
├── onnx/                   # ONNX models (.onnx) — Chestnut's big model only;
│   │                       # this dev PC does not run the driving model via
│   │                       # ONNX Runtime
│   └── big_driving_supercombo.onnx
├── rknn/                   # Rockchip NPU models (.rknn)
│   ├── driving_policy.rknn
│   └── driving_vision.rknn
├── dxnn/                   # DeepX DX-M1M segmentation, placed by hand from the DX Model Zoo
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

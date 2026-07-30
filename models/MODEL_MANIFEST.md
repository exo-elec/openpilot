# EOP Model Manifest

All model binaries are downloaded at install time via `download_models.sh`.
They are NOT stored in git. Verify downloads with the sha256 checksums below.

## RKNN Models (Rockchip NPU — driving_vision + driving_policy)

| File | SHA256 | Source | Notes |
|------|--------|--------|-------|
| `rknn/driving_vision.rknn` | *(set after first verified build)* | internal CDN | Road + wide-road vision model |
| `rknn/driving_policy.rknn` | *(set after first verified build)* | internal CDN | Policy model |

## Hailo HEF Models (Hailo-8 NPU)

| File | SHA256 | Source | Notes |
|------|--------|--------|-------|
| `hef/yolov8n.hef` | *(set after first verified build)* | Hailo Model Zoo | YOLO v8 nano — monod/sided/reard |
| `hef/scrfd_2.5g.hef` | *(set after first verified build)* | Hailo Model Zoo | Face detection — driverd |
| `hef/whisper_base_5s_encoder.hef` | *(set after first verified build)* | Hailo Model Zoo | Whisper encoder — soundd TTS |

## ONNX Models (RKNN conversion pipeline)

| File | SHA256 | Source | Notes |
|------|--------|--------|-------|
| `onnx/driving_vision.onnx` | *(set after first verified build)* | internal CDN | Pre-conversion source for RKNN |
| `onnx/driving_policy.onnx` | *(set after first verified build)* | internal CDN | Pre-conversion source for RKNN |
| `onnx/egolanes_lite_int8.onnx` | *(set after first verified build)* | internal CDN | Lane detection |
| `onnx/scene3d_lite_int8.onnx` | *(set after first verified build)* | internal CDN | 3D scene model |
| `onnx/sceneseg_lite_int8.onnx` | *(set after first verified build)* | internal CDN | Scene segmentation |
| `onnx/autosteer_full_int8.onnx` | *(set after first verified build)* | internal CDN | Steer model |
| `onnx/autospeed_full_int8.onnx` | *(set after first verified build)* | internal CDN | Speed model |

## Adding New Models

1. Download and place the file in the appropriate subdirectory.
2. Run `sha256sum <file>` and record the checksum here.
3. Add an entry to `download_models.sh` so CI and fresh installs can fetch it.

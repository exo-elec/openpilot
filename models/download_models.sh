#!/usr/bin/env bash
# Download models from RKNN Model Zoo and Hailo Model Zoo
# Usage: ./download_models.sh [all|rknn|hailo]

set -e

MODELS_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
TEMP_DIR=$(mktemp -d)
DOWNLOAD_TYPE="${1:-all}"

cd "$TEMP_DIR"

mkdir -p "$MODELS_DIR/onnx" "$MODELS_DIR/rknn" "$MODELS_DIR/hef" "$MODELS_DIR/axmodel" "$MODELS_DIR/dxnn"

echo "=== EOP Model Downloader ==="
echo "Download type: $DOWNLOAD_TYPE"
echo ""
echo "Usage: ./download_models.sh [all|dev-pc|rknn|hailo]"
echo "  dev-pc  - ONNX models only (x86 dev PC / CARLA testing)"
echo "  rknn    - RKNN models (requires RKNN model zoo)"
echo "  hailo   - Hailo HEF models (requires Hailo model zoo)"
echo "  all     - Everything above"
echo ""
echo "Folders are named by file format, not backend brand:"
echo "  rknn/ (.rknn, Rockchip NPU — driving model)   hef/ (.hef, Hailo-8 — yolov8n)"
echo "  onnx/ (.onnx — Chestnut's big model only)   axmodel/ (.axmodel, AX-M1 — reserved)"
echo "  dxnn/ (.dxnn, DeepX DX-M1 — reserved)"
echo ""

# ============================================================================
# Dev PC / CARLA — Chestnut's big-model ONNX + eGPU shadow YOLO exports
# ============================================================================
if [ "$DOWNLOAD_TYPE" = "dev-pc" ] || [ "$DOWNLOAD_TYPE" = "all" ]; then
    echo "=== ONNX Models (x86 dev PC) ==="

    copy_verified() {  # <src> <dst> <expected_sha256>
        local src="$1" dst="$2" want="$3"
        [ -f "$src" ] || return 1
        [ "$(sha256sum "$src" | cut -d' ' -f1)" = "$want" ] || return 1
        cp "$src" "$dst"
    }

    # NOTE: this used to also fetch bukapilot's driving_vision.onnx/
    # driving_policy.onnx as a dev-PC ONNX-runtime substitute for the RKNN
    # driving pair, plus a reference-only Autoware vision suite (egolanes/
    # scene3d/sceneseg/autosteer/autospeed). Removed — onnx/ stores only
    # Chestnut's big model; this dev PC does not run the driving model via
    # ONNX Runtime. See MODEL_MANIFEST.md and git history if that capability
    # is needed again.

    # YOLOv8n ONNX for monod (640×640 object detection)
    if [ ! -f "$MODELS_DIR/onnx/yolo_640.onnx" ]; then
        echo ""
        echo "Downloading YOLOv8n ONNX for monod..."
        # Export from ultralytics (requires pip install ultralytics)
        python3 -c "
from ultralytics import YOLO
m = YOLO('yolov8n.pt')
m.export(format='onnx', imgsz=640, opset=12)
import shutil, pathlib
src = pathlib.Path('yolov8n.onnx')
if src.exists():
    shutil.copy(src, '$MODELS_DIR/onnx/yolo_640.onnx')
    print('✓ yolo_640.onnx saved')
" 2>/dev/null || echo "✗ YOLOv8n export failed — install: pip install ultralytics"
    fi

    # Keep distinct artifacts and model sessions for side and rear. They start
    # from the same generic COCO export for hardware validation, then can be
    # replaced independently with viewpoint-specific trained models.
    if [ -f "$MODELS_DIR/onnx/yolo_640.onnx" ]; then
        cp "$MODELS_DIR/onnx/yolo_640.onnx" "$MODELS_DIR/onnx/yolo_side.onnx"
        cp "$MODELS_DIR/onnx/yolo_640.onnx" "$MODELS_DIR/onnx/yolo_rear.onnx"
        echo "✓ separate side/rear eGPU validation models saved"
    fi

    # Chestnut's raw ONNX is not wired to any runner yet (ChestnutDrivingRunner
    # is deliberately fail-closed — see task.md/CHESTNUT_EGPU_ADOPTION.md); this
    # only pre-positions the artifact for a future gated compile step. Prefer
    # a real commaai/openpilot checkout; fall back to sunnypilot (cross-verified
    # byte-identical against it as of 2026-08-23).
    EXT_GPU_UPSTREAM_DIR="${EXT_GPU_UPSTREAM_DIR:-$(dirname "$MODELS_DIR")/../ext_gpu/openpilot-upstream/openpilot}"
    SUNNYPILOT_DIR="${SUNNYPILOT_DIR:-$(dirname "$MODELS_DIR")/../sunnypilot/openpilot}"
    CHESTNUT_SHA="10926f2c0911821ca0e72439c1c3bf3ec11f0a08789aa14b7ee8f25379b2afa4"
    if copy_verified "$EXT_GPU_UPSTREAM_DIR/selfdrive/modeld/models/big_driving_supercombo.onnx" \
        "$MODELS_DIR/onnx/big_driving_supercombo.onnx" "$CHESTNUT_SHA"; then
        echo "✓ big_driving_supercombo.onnx (commaai/openpilot@master, hash-verified)"
    elif copy_verified "$SUNNYPILOT_DIR/selfdrive/modeld/models/big_driving_supercombo.onnx" \
        "$MODELS_DIR/onnx/big_driving_supercombo.onnx" "$CHESTNUT_SHA"; then
        echo "✓ big_driving_supercombo.onnx (sunnypilot@master fallback, hash-verified)"
    else
        echo "✗ big_driving_supercombo.onnx — not fetched. This is expected to go stale:"
        echo "  comma updates this model upstream periodically (hash WILL change genuinely,"
        echo "  not just on error). Re-verify against a fresh commaai/openpilot checkout"
        echo "  and update the sha256 above + MODEL_MANIFEST.md before trusting a mismatch."
    fi
fi

# ============================================================================
# RKNN Model Zoo (Rockchip NPU)
# ============================================================================
if [ "$DOWNLOAD_TYPE" = "all" ] || [ "$DOWNLOAD_TYPE" = "rknn" ]; then
    echo "=== RKNN Driving Models (bukapilot KA2, hash-verified) ==="

    # Proven RK3588 driving pair from a local bukapilot checkout. No public
    # direct-download URL exists (git-LFS on gitlab.com/iXcess/openpilot-lfs);
    # clone bukapilot with LFS and re-run, or set BUKAPILOT_DIR.
    BUKAPILOT_DIR="${BUKAPILOT_DIR:-$(dirname "$MODELS_DIR")/../bukapilot}"
    copy_verified() {  # <src> <dst> <expected_sha256>
        local src="$1" dst="$2" want="$3"
        [ -f "$src" ] || return 1
        [ "$(sha256sum "$src" | cut -d' ' -f1)" = "$want" ] || return 1
        cp "$src" "$dst"
    }
    copy_verified "$BUKAPILOT_DIR/selfdrive/modeld/models/driving_vision.rknn" \
        "$MODELS_DIR/rknn/driving_vision.rknn" \
        "34da99c3b818df565d36a9729a5e186acb64174d2486ae4f75873b1a3cc8e78f" && \
        echo "✓ driving_vision.rknn (bukapilot KA2, hash-verified)" || \
        echo "✗ driving_vision.rknn — clone ../bukapilot with git-lfs or set BUKAPILOT_DIR"
    copy_verified "$BUKAPILOT_DIR/selfdrive/modeld/models/driving_policy.rknn" \
        "$MODELS_DIR/rknn/driving_policy.rknn" \
        "988db22cbed43fd9c50a91a937a091d810b160059c010f61d80a32fc2236d708" && \
        echo "✓ driving_policy.rknn (bukapilot KA2, hash-verified)" || \
        echo "✗ driving_policy.rknn — clone ../bukapilot with git-lfs or set BUKAPILOT_DIR"

    echo "=== Downloading from RKNN Model Zoo ==="
    
    # Clone RKNN Model Zoo (shallow clone)
    echo "Cloning RKNN Model Zoo..."
    git clone --depth 1 -b v2.3.2 https://github.com/airockchip/rknn_model_zoo.git
    
    # Download PP-LiteSeg
    echo ""
    echo "Downloading PP-LiteSeg..."
    cd rknn_model_zoo/examples/ppseg/model
    if [ -f "./download_model.sh" ]; then
        chmod +x ./download_model.sh
        ./download_model.sh 2>/dev/null || echo "Note: download_model.sh failed, may need manual download"
    fi
    
    if [ -f "pp_liteseg_cityscapes.onnx" ]; then
        cp pp_liteseg_cityscapes.onnx "$MODELS_DIR/onnx/"
        echo "✓ PP-LiteSeg saved to models/onnx/"
    else
        echo "✗ PP-LiteSeg not found - download manually from:"
        echo "  https://github.com/airockchip/rknn_model_zoo/tree/v2.3.2/examples/ppseg"
    fi
    
    cd "$TEMP_DIR/rknn_model_zoo"
    
    # Download YOLOv8n
    echo ""
    echo "Downloading YOLOv8n (RKNN version)..."
    cd examples/yolov8/model
    if [ -f "./download_model.sh" ]; then
        chmod +x ./download_model.sh
        ./download_model.sh 2>/dev/null || echo "Note: download_model.sh failed, may need manual download"
    fi
    
    if [ -f "yolov8n.onnx" ]; then
        cp yolov8n.onnx "$MODELS_DIR/onnx/"
        echo "✓ YOLOv8n (RKNN) saved to models/onnx/"
    else
        echo "✗ YOLOv8n not found - download manually from:"
        echo "  https://github.com/airockchip/rknn_model_zoo/tree/v2.3.2/examples/yolov8"
    fi
    
    cd "$TEMP_DIR"
fi

# ============================================================================
# Hailo Model Zoo (Hailo-8 NPU)
# ============================================================================
if [ "$DOWNLOAD_TYPE" = "all" ] || [ "$DOWNLOAD_TYPE" = "hailo" ]; then
    echo ""
    echo "=== Downloading from Hailo Model Zoo ==="
    echo "Source: https://github.com/hailo-ai/hailo_model_zoo"
    
    # Hailo Model Zoo direct download URLs for Hailo-8
    HAILO_VERSION="v2.14.0"
    HAILO_BASE_URL="https://hailo-model-zoo.s3.eu-west-2.amazonaws.com/ModelZoo/Compiled"
    
    # Download YOLOv8n for Hailo-8
    echo ""
    echo "Downloading YOLOv8n (Hailo-8 version)..."
    wget -q --show-progress -O yolov8n_hailo8.hef \
        "${HAILO_BASE_URL}/${HAILO_VERSION}/hailo8/yolov8n.hef" 2>/dev/null || \
        curl -L -o yolov8n_hailo8.hef \
        "${HAILO_BASE_URL}/${HAILO_VERSION}/hailo8/yolov8n.hef" 2>/dev/null || \
        { echo "✗ Download failed"; echo "  Manual download: ${HAILO_BASE_URL}/${HAILO_VERSION}/hailo8/yolov8n.hef"; }
    
    if [ -f "yolov8n_hailo8.hef" ]; then
        cp yolov8n_hailo8.hef "$MODELS_DIR/hef/yolov8n.hef"
        echo "✓ YOLOv8n (Hailo-8) saved to models/hef/yolov8n.hef"
    fi
    
    # Download YOLOv5n for Hailo-8 (alternative)
    echo ""
    echo "Downloading YOLOv5n (Hailo-8 version)..."
    wget -q --show-progress -O yolov5n_hailo8.hef \
        "${HAILO_BASE_URL}/${HAILO_VERSION}/hailo8/yolov5n.hef" 2>/dev/null || \
        curl -L -o yolov5n_hailo8.hef \
        "${HAILO_BASE_URL}/${HAILO_VERSION}/hailo8/yolov5n.hef" 2>/dev/null || \
        { echo "✗ Download failed"; echo "  Manual download: ${HAILO_BASE_URL}/${HAILO_VERSION}/hailo8/yolov5n.hef"; }
    
    if [ -f "yolov5n_hailo8.hef" ]; then
        cp yolov5n_hailo8.hef "$MODELS_DIR/hef/yolov5n.hef"
        echo "✓ YOLOv5n (Hailo-8) saved to models/hef/yolov5n.hef"
    fi

    # NOTE: SCRFD 2.5G (face detection for driver-monitoring/`driverd`) used to
    # be fetched here. Removed — this hardware has no driver-facing camera;
    # `driverd`'s face-DMS pipeline is VisionPilot-only anyway (see
    # models/README.md). Re-add if a driver camera is ever fitted.

    # NOTE: whisper_base_5s_encoder.hef intentionally NOT fetched here. A
    # .hef-compiled build exists in ../visionpilot, but hef/ is the
    # CAMERA_INFERENCE tier and whisper is VOICE_INFERENCE (destined for
    # axmodel/, once an AX-M1 backend and .axmodel build exist) — wrong tier
    # for this folder's purpose. See MODEL_MANIFEST.md.
fi

# Cleanup
cd "$MODELS_DIR"
rm -rf "$TEMP_DIR"

echo ""
echo "=== Download complete ==="
echo ""
echo "Downloaded models location:"
echo "  - RKNN ONNX models: models/onnx/"
echo "  - Hailo HEF models: models/hef/"
echo ""
echo "Next steps:"
echo "1. For RKNN models: Convert ONNX to RKNN using tools/convert_models_to_rknn.py"
echo "2. For Hailo models: Use .hef files directly with HailoRT"
echo ""
echo "Model Zoo references:"
echo "  - RKNN Model Zoo: https://github.com/airockchip/rknn_model_zoo"
echo "  - Hailo Model Zoo: https://github.com/hailo-ai/hailo_model_zoo"
echo "  - DeepX DX Model Zoo (dxnn/, reserved): https://github.com/DEEPX-AI/dx-modelzoo"
echo "  - Axera AXCL / axmodel (axmodel/, reserved): https://github.com/AXERA-TECH"
echo "    (LLM-on-AX650 specifically: https://github.com/AXERA-TECH/ax-llm)"

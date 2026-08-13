#!/usr/bin/env bash
# Download models from RKNN Model Zoo and Hailo Model Zoo
# Usage: ./download_models.sh [all|rknn|hailo]

set -e

MODELS_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
TEMP_DIR=$(mktemp -d)
DOWNLOAD_TYPE="${1:-all}"

cd "$TEMP_DIR"

mkdir -p "$MODELS_DIR/onnx" "$MODELS_DIR/rknn" "$MODELS_DIR/hef"

echo "=== EOP Model Downloader ==="
echo "Download type: $DOWNLOAD_TYPE"
echo ""
echo "Usage: ./download_models.sh [all|dev-pc|rknn|hailo]"
echo "  dev-pc  - ONNX models only (x86 dev PC / CARLA testing)"
echo "  rknn    - RKNN models (requires RKNN model zoo)"
echo "  hailo   - Hailo HEF models (requires Hailo model zoo)"
echo "  all     - Everything above"
echo ""

# ============================================================================
# Dev PC / CARLA — ONNX driving models from dragonpilot
# ============================================================================
if [ "$DOWNLOAD_TYPE" = "dev-pc" ] || [ "$DOWNLOAD_TYPE" = "all" ]; then
    echo "=== ONNX Driving Models (x86 dev PC) ==="

    # Primary: extract from local dragonpilot repo if available
    DRAGONPILOT_DIR="$(dirname "$MODELS_DIR")/../dragonpilot"
    if [ -d "$DRAGONPILOT_DIR" ]; then
        echo "Extracting from dragonpilot pre-build branch..."
        (cd "$DRAGONPILOT_DIR" && \
            git show pre-build:selfdrive/modeld/models/driving_vision.onnx > \
                "$MODELS_DIR/onnx/driving_vision.onnx" && \
            git show pre-build:selfdrive/modeld/models/driving_policy.onnx > \
                "$MODELS_DIR/onnx/driving_policy.onnx" && \
            echo "✓ driving_vision.onnx ($(du -sh "$MODELS_DIR/onnx/driving_vision.onnx" | cut -f1))" && \
            echo "✓ driving_policy.onnx ($(du -sh "$MODELS_DIR/onnx/driving_policy.onnx" | cut -f1))" \
        ) 2>/dev/null || echo "✗ dragonpilot extraction failed — see fallback below"
    fi

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
fi

# ============================================================================
# RKNN Model Zoo (Rockchip NPU)
# ============================================================================
if [ "$DOWNLOAD_TYPE" = "all" ] || [ "$DOWNLOAD_TYPE" = "rknn" ]; then
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

    # SCRFD 2.5G — face detection backbone for `driverd` (compiled with v2.18.0).
    SCRFD_URL="https://hailo-model-zoo.s3.eu-west-2.amazonaws.com/ModelZoo/Compiled/v2.18.0/hailo8/scrfd_2.5g.hef"
    echo ""
    echo "Downloading SCRFD 2.5G (Hailo-8, face DMS)..."
    wget -q --show-progress -O scrfd_2.5g.hef "$SCRFD_URL" 2>/dev/null || \
        curl -L -o scrfd_2.5g.hef "$SCRFD_URL" 2>/dev/null || \
        { echo "✗ Download failed"; echo "  Manual download: $SCRFD_URL"; }

    if [ -f "scrfd_2.5g.hef" ]; then
        cp scrfd_2.5g.hef "$MODELS_DIR/hef/scrfd_2.5g.hef"
        echo "✓ SCRFD 2.5G (Hailo-8) saved to models/hef/scrfd_2.5g.hef"
    fi
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

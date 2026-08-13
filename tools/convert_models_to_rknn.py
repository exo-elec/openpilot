#!/usr/bin/env python3
"""
Convert OpenPilot driving models from ONNX to RKNN format.

This script converts:
- driving_vision.onnx -> driving_vision.rknn (NPU Core 0)
- driving_policy.onnx -> driving_policy.rknn (NPU Core 0 or 2)

Usage:
    python3 tools/convert_models_to_rknn.py

Requirements:
- rknn-toolkit2 installed
- ONNX models in selfdrive/modeld/models/
- Output RKNN models go to /data/models/ (deployment) or selfdrive/modeld/models/ (testing)
"""

import argparse
import sys
from pathlib import Path

# Add openpilot to path
sys.path.insert(0, str(Path(__file__).parents[1]))


def convert_driving_vision(input_path: Path, output_path: Path, target_platform: str = "rk3588"):
    """Convert driving vision model to RKNN format."""
    try:
        from rknn.api import RKNN
    except ImportError:
        print("❌ rknn-toolkit2 not installed. Install with: pip install rknn-toolkit2")
        return False

    print("🔄 Converting driving vision model...")
    print(f"   Input: {input_path}")
    print(f"   Output: {output_path}")
    print(f"   Target: {target_platform}")

    rknn = RKNN(verbose=False)

    # Configure RKNN
    print("   Configuring RKNN...")
    rknn.config(
        target_platform=target_platform,
        optimization_level=3,
        mean_values=[[127.5, 127.5, 127.5]],  # Normalize to [-1, 1]
        std_values=[[127.5, 127.5, 127.5]],
        quantize=False,  # Use FP16 for better accuracy
    )

    # Load ONNX model
    print("   Loading ONNX model...")
    ret = rknn.load_onnx(
        model=str(input_path),
        # driving_vision takes two loadyuv-transformed frames (img, big_img):
        # 12 channels (6/frame) at half of MODEL_WIDTH x MODEL_HEIGHT
        # (512x256 -> 256x128), i.e. [1, 12, 128, 256] each.
        input_size_list=[[1, 12, 128, 256], [1, 12, 128, 256]],
    )
    if ret != 0:
        print(f"❌ Failed to load ONNX model: {ret}")
        return False

    # Build RKNN model
    print("   Building RKNN model (this may take a few minutes)...")
    ret = rknn.build(do_quantization=False, dataset=None)
    if ret != 0:
        print(f"❌ Failed to build RKNN model: {ret}")
        return False

    # Export RKNN model
    print("   Exporting RKNN model...")
    output_path.parent.mkdir(parents=True, exist_ok=True)
    ret = rknn.export_rknn(str(output_path))
    if ret != 0:
        print(f"❌ Failed to export RKNN model: {ret}")
        return False

    # Release
    rknn.release()

    print(f"✅ Successfully converted: {output_path}")
    return True


def convert_driving_policy(input_path: Path, output_path: Path, target_platform: str = "rk3588"):
    """Convert driving policy model to RKNN format."""
    try:
        from rknn.api import RKNN
    except ImportError:
        print("❌ rknn-toolkit2 not installed. Install with: pip install rknn-toolkit2")
        return False

    print("🔄 Converting driving policy model...")
    print(f"   Input: {input_path}")
    print(f"   Output: {output_path}")
    print(f"   Target: {target_platform}")

    rknn = RKNN(verbose=False)

    # Configure RKNN
    print("   Configuring RKNN...")
    rknn.config(
        target_platform=target_platform,
        optimization_level=3,
        quantize=False,  # Use FP16 for better accuracy
    )

    # Load ONNX model
    print("   Loading ONNX model...")
    ret = rknn.load_onnx(
        model=str(input_path),
        # input_size_list omitted: policy inputs are feature tensors, not images
    )
    if ret != 0:
        print(f"❌ Failed to load ONNX model: {ret}")
        return False

    # Build RKNN model
    print("   Building RKNN model...")
    ret = rknn.build(do_quantization=False, dataset=None)
    if ret != 0:
        print(f"❌ Failed to build RKNN model: {ret}")
        return False

    # Export RKNN model
    print("   Exporting RKNN model...")
    output_path.parent.mkdir(parents=True, exist_ok=True)
    ret = rknn.export_rknn(str(output_path))
    if ret != 0:
        print(f"❌ Failed to export RKNN model: {ret}")
        return False

    # Release
    rknn.release()

    print(f"✅ Successfully converted: {output_path}")
    return True


def main():
    parser = argparse.ArgumentParser(description="Convert OpenPilot models to RKNN format")
    parser.add_argument("--target", default="rk3588", choices=["rk3588"],
                       help="Target platform (default: rk3588)")
    parser.add_argument("--output-dir", default="/data/models",
                       help="Output directory for RKNN models")
    parser.add_argument("--test", action="store_true",
                       help="Test conversion (output to selfdrive/modeld/models)")

    args = parser.parse_args()

    # Determine output directory
    if args.test:
        output_dir = Path("selfdrive/modeld/models")
    else:
        output_dir = Path(args.output_dir)

    # Model paths
    vision_onnx = Path("selfdrive/modeld/models/driving_vision.onnx")
    policy_onnx = Path("selfdrive/modeld/models/driving_policy.onnx")

    vision_rknn = output_dir / "driving_vision.rknn"
    policy_rknn = output_dir / "driving_policy.rknn"

    # Check if ONNX files exist
    if not vision_onnx.exists():
        print(f"❌ Vision ONNX not found: {vision_onnx}")
        print("   Please ensure models are downloaded")
        return 1

    if not policy_onnx.exists():
        print(f"❌ Policy ONNX not found: {policy_onnx}")
        print("   Please ensure models are downloaded")
        return 1

    print(f"🚀 Starting model conversion for {args.target}")
    print(f"   Output directory: {output_dir}")
    print()

    # Convert models
    success = True

    if not convert_driving_vision(vision_onnx, vision_rknn, args.target):
        success = False

    print()

    if not convert_driving_policy(policy_onnx, policy_rknn, args.target):
        success = False

    print()

    if success:
        print("✅ All models converted successfully!")
        print()
        print("Next steps:")
        print("  1. Deploy RKNN models to /data/models/ on target device")
        print("  2. Update modeld to use RKNN runtime")
        print("  3. Restart modeld daemon")
        return 0
    else:
        print("❌ Some conversions failed")
        return 1


if __name__ == "__main__":
    sys.exit(main())

"""
Unit tests for path_corridor_fusion module.

Phase 4 Road Segmentation Integration - tests multi-source corridor boundary fusion.

Tests:
  * Priority-based source selection
  * Confidence thresholding
  * Boundary validation
  * Fallback behavior
  * Edge cases
"""

import numpy as np
import sys
sys.path.insert(0, '/home/vcar/EnhancedOpenPilot')

from cereal import log
import cereal.messaging as messaging

# Direct import to avoid pathd init chain
import importlib.util
spec = importlib.util.spec_from_file_location('path_corridor_fusion', 'selfdrive/pathd/path_corridor_fusion.py')
fusion_module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(fusion_module)

# Extract functions and constants
fuse_corridor_boundaries = fusion_module.fuse_corridor_boundaries
validate_corridor_boundaries = fusion_module.validate_corridor_boundaries
SEGMENTATION_HIGH_CONFIDENCE = fusion_module.SEGMENTATION_HIGH_CONFIDENCE
VISION_DEFAULT_CONFIDENCE = fusion_module.VISION_DEFAULT_CONFIDENCE
STEREO_DEPTH_CONFIDENCE = fusion_module.STEREO_DEPTH_CONFIDENCE
DEFAULT_CORRIDOR_WIDTH = fusion_module.DEFAULT_CORRIDOR_WIDTH


def create_mock_xyzdata(y_values: list) -> log.XYZTData:
    """Create mock XYZTData with specified lateral positions."""
    msg = log.XYZTData.new_message()
    msg.x = list(range(33))  # 0-32
    msg.y = y_values if len(y_values) == 33 else y_values * 33  # Replicate if single value
    msg.z = [0.0] * 33
    msg.t = []
    msg.xStd = [0.5] * 33
    msg.yStd = [0.1] * 33
    msg.zStd = [0.05] * 33
    return msg


def create_mock_ground_objects(left_y: float, right_y: float, confidence: float) -> log.GroundObjects:
    """Create mock groundObjects with roadGeometry."""
    msg = messaging.new_message("groundObjects")
    ground_msg = msg.groundObjects

    # Create road edges
    left_edge = create_mock_xyzdata([left_y] * 33)
    right_edge = create_mock_xyzdata([right_y] * 33)

    ground_msg.roadGeometry.roadEdges = [left_edge, right_edge]
    ground_msg.roadGeometry.roadEdgeConfidence = confidence
    ground_msg.roadGeometry.detectionSource = log.GroundObjects.RoadGeometry.DetectionSource.hybrid

    return ground_msg


def create_mock_model_v2(left_y: float, right_y: float) -> log.ModelDataV2:
    """Create mock modelV2 with roadEdges."""
    msg = messaging.new_message("modelV2")
    model_msg = msg.modelV2

    left_edge = create_mock_xyzdata([left_y] * 33)
    right_edge = create_mock_xyzdata([right_y] * 33)

    model_msg.roadEdges = [left_edge, right_edge]

    return model_msg


def create_mock_stereo_ground(left_boundary: list, right_boundary: list) -> log.StereoGround:
    """Create mock stereoGround with 7-point boundaries."""
    msg = messaging.new_message("stereoGround")
    stereo_msg = msg.stereoGround

    stereo_msg.hasStereoDepth = True
    # Convert to Python floats (not numpy types)
    stereo_msg.leftBoundary = [float(x) for x in (left_boundary[:7] if len(left_boundary) >= 7 else left_boundary)]
    stereo_msg.rightBoundary = [float(x) for x in (right_boundary[:7] if len(right_boundary) >= 7 else right_boundary)]

    return stereo_msg


def test_path_corridor_fusion_priority_segmentation():
    """High-confidence segmentation should override vision."""
    # Create mock data
    model_v2 = create_mock_model_v2(left_y=2.0, right_y=-2.0)
    ground_objects = create_mock_ground_objects(left_y=2.5, right_y=-2.5, confidence=0.9)

    # Fuse corridors
    left, right, source, conf = fuse_corridor_boundaries(model_v2, ground_objects, None)

    # Validate
    assert source == log.EnhancedTrajectory.BoundarySource.segmentation, "Should use segmentation"
    assert np.allclose(left[0], 2.5), f"Should use segmentation left edge: {left[0]}"
    assert np.allclose(right[0], -2.5), f"Should use segmentation right edge: {right[0]}"
    assert np.isclose(conf, 0.9, rtol=1e-5), f"Confidence should be ~0.9, got {conf}"

    print("✅ Priority: High-confidence segmentation overrides vision")


def test_path_corridor_fusion_fallback_to_vision():
    """Low-confidence segmentation should fall back to vision."""
    # Create mock data with LOW confidence segmentation
    model_v2 = create_mock_model_v2(left_y=2.0, right_y=-2.0)
    ground_objects = create_mock_ground_objects(left_y=2.5, right_y=-2.5, confidence=0.4)

    # Fuse corridors
    left, right, source, conf = fuse_corridor_boundaries(model_v2, ground_objects, None)

    # Validate
    assert source == log.EnhancedTrajectory.BoundarySource.vision, "Should fall back to vision"
    assert np.allclose(left[0], 2.0), f"Should use vision left edge: {left[0]}"
    assert np.allclose(right[0], -2.0), f"Should use vision right edge: {right[0]}"
    assert conf == VISION_DEFAULT_CONFIDENCE, f"Confidence should be {VISION_DEFAULT_CONFIDENCE}"

    print("✅ Fallback: Low-confidence segmentation falls back to vision")


def test_path_corridor_fusion_vision_only():
    """Vision-only mode when no segmentation available."""
    # Create mock data with NO groundObjects
    model_v2 = create_mock_model_v2(left_y=2.2, right_y=-2.2)

    # Fuse corridors
    left, right, source, conf = fuse_corridor_boundaries(model_v2, None, None)

    # Validate
    assert source == log.EnhancedTrajectory.BoundarySource.vision, "Should use vision"
    assert np.allclose(left[0], 2.2), f"Should use vision left edge: {left[0]}"
    assert np.allclose(right[0], -2.2), f"Should use vision right edge: {right[0]}"
    assert conf == VISION_DEFAULT_CONFIDENCE

    print("✅ Vision-only: Uses vision when no segmentation available")


def test_path_corridor_fusion_stereo_depth_fallback():
    """Stereo depth fallback when no vision or segmentation."""
    # Create mock stereoGround with 7-point boundaries
    stereo_ground = create_mock_stereo_ground(
        left_boundary=[2.0, 2.1, 2.2, 2.3, 2.4, 2.5, 2.6],
        right_boundary=[-2.0, -2.1, -2.2, -2.3, -2.4, -2.5, -2.6]
    )

    # Fuse corridors (no vision, no segmentation)
    left, right, source, conf = fuse_corridor_boundaries(None, None, stereo_ground)

    # Validate
    assert source == log.EnhancedTrajectory.BoundarySource.stereoDepth, "Should use stereo depth"
    assert len(left) == 33, "Should interpolate to 33 points"
    assert len(right) == 33, "Should interpolate to 33 points"
    assert conf == STEREO_DEPTH_CONFIDENCE

    # Check interpolation worked (first point should be close to input)
    assert 1.9 < left[0] < 2.1, f"Interpolated left edge reasonable: {left[0]}"
    assert -2.1 < right[0] < -1.9, f"Interpolated right edge reasonable: {right[0]}"

    print("✅ Stereo depth fallback: Uses interpolated stereo boundaries")


def test_path_corridor_fusion_default_corridor():
    """Default corridor when no sources available."""
    # Fuse corridors with ALL sources None
    left, right, source, conf = fuse_corridor_boundaries(None, None, None)

    # Validate
    assert source == log.EnhancedTrajectory.BoundarySource.vision, "Default marked as vision"
    assert len(left) == 33, "Should have 33 points"
    assert len(right) == 33, "Should have 33 points"
    assert conf == 0.0, "Default corridor has zero confidence"

    # Check default values
    assert np.allclose(left, DEFAULT_CORRIDOR_WIDTH), f"Left should be {DEFAULT_CORRIDOR_WIDTH}m"
    assert np.allclose(right, -DEFAULT_CORRIDOR_WIDTH), f"Right should be -{DEFAULT_CORRIDOR_WIDTH}m"

    print(f"✅ Default corridor: ±{DEFAULT_CORRIDOR_WIDTH}m when no sources available")


def test_path_corridor_validate_boundaries_valid():
    """Valid boundaries should pass validation."""
    left = np.full(33, 2.5, dtype=np.float32)
    right = np.full(33, -2.5, dtype=np.float32)

    is_valid = validate_corridor_boundaries(left, right, min_width=2.0, max_width=6.0)

    assert is_valid, "Should be valid"
    print("✅ Boundary validation: Valid corridors pass")


def test_path_corridor_validate_boundaries_too_narrow():
    """Too-narrow corridor should fail validation."""
    left = np.full(33, 0.5, dtype=np.float32)   # 1m width total
    right = np.full(33, -0.5, dtype=np.float32)

    is_valid = validate_corridor_boundaries(left, right, min_width=2.0, max_width=6.0)

    assert not is_valid, "Should fail: too narrow"
    print("✅ Boundary validation: Too-narrow corridors rejected")


def test_path_corridor_validate_boundaries_too_wide():
    """Too-wide corridor should fail validation."""
    left = np.full(33, 5.0, dtype=np.float32)   # 10m width total
    right = np.full(33, -5.0, dtype=np.float32)

    is_valid = validate_corridor_boundaries(left, right, min_width=2.0, max_width=6.0)

    assert not is_valid, "Should fail: too wide"
    print("✅ Boundary validation: Too-wide corridors rejected")


def test_path_corridor_validate_boundaries_invalid_length():
    """Invalid boundary length should fail."""
    left = np.full(20, 2.5, dtype=np.float32)  # Wrong length
    right = np.full(33, -2.5, dtype=np.float32)

    is_valid = validate_corridor_boundaries(left, right)

    assert not is_valid, "Should fail: wrong length"
    print("✅ Boundary validation: Invalid lengths rejected")


def test_path_corridor_validate_boundaries_non_finite():
    """Non-finite values should fail validation."""
    left = np.full(33, 2.5, dtype=np.float32)
    right = np.full(33, -2.5, dtype=np.float32)
    left[10] = np.nan  # Inject NaN

    is_valid = validate_corridor_boundaries(left, right)

    assert not is_valid, "Should fail: non-finite values"
    print("✅ Boundary validation: Non-finite values rejected")


def test_path_corridor_fusion_confidence_threshold():
    """Test exact confidence threshold behavior."""
    model_v2 = create_mock_model_v2(left_y=2.0, right_y=-2.0)

    # Test at threshold (0.7) - should use segmentation with >= comparison
    ground_objects_at_threshold = create_mock_ground_objects(
        left_y=2.5, right_y=-2.5, confidence=float(SEGMENTATION_HIGH_CONFIDENCE)
    )
    left, right, source, conf = fuse_corridor_boundaries(model_v2, ground_objects_at_threshold, None)
    assert source == log.EnhancedTrajectory.BoundarySource.segmentation, f"Should use segmentation at threshold {SEGMENTATION_HIGH_CONFIDENCE}, got {source}"

    # Test just below threshold (0.69) - should fall back to vision
    ground_objects_below_threshold = create_mock_ground_objects(
        left_y=2.5, right_y=-2.5, confidence=float(SEGMENTATION_HIGH_CONFIDENCE - 0.01)
    )
    left, right, source, conf = fuse_corridor_boundaries(model_v2, ground_objects_below_threshold, None)
    assert source == log.EnhancedTrajectory.BoundarySource.vision, f"Should use vision below threshold, got {source}"

    print(f"✅ Confidence threshold: Correctly uses {SEGMENTATION_HIGH_CONFIDENCE} cutoff")


def test_path_corridor_fusion_boundary_format():
    """Test that returned boundaries have correct format."""
    model_v2 = create_mock_model_v2(left_y=2.0, right_y=-2.0)

    left, right, source, conf = fuse_corridor_boundaries(model_v2, None, None)

    # Check types
    assert isinstance(left, np.ndarray), "Left should be numpy array"
    assert isinstance(right, np.ndarray), "Right should be numpy array"
    assert left.dtype == np.float32, "Left should be float32"
    assert right.dtype == np.float32, "Right should be float32"

    # Check shape
    assert left.shape == (33,), f"Left should be (33,), got {left.shape}"
    assert right.shape == (33,), f"Right should be (33,), got {right.shape}"

    # Check values are finite
    assert np.all(np.isfinite(left)), "All left values should be finite"
    assert np.all(np.isfinite(right)), "All right values should be finite"

    print("✅ Boundary format: Correct dtype and shape")


if __name__ == "__main__":
    print("\n" + "="*70)
    print("  Path Corridor Fusion Unit Tests")
    print("="*70 + "\n")

    tests = [
        ("Priority: Segmentation overrides vision", test_path_corridor_fusion_priority_segmentation),
        ("Fallback: Low confidence to vision", test_path_corridor_fusion_fallback_to_vision),
        ("Vision-only mode", test_path_corridor_fusion_vision_only),
        ("Stereo depth fallback", test_path_corridor_fusion_stereo_depth_fallback),
        ("Default corridor", test_path_corridor_fusion_default_corridor),
        ("Validate: Valid boundaries", test_path_corridor_validate_boundaries_valid),
        ("Validate: Too narrow", test_path_corridor_validate_boundaries_too_narrow),
        ("Validate: Too wide", test_path_corridor_validate_boundaries_too_wide),
        ("Validate: Invalid length", test_path_corridor_validate_boundaries_invalid_length),
        ("Validate: Non-finite values", test_path_corridor_validate_boundaries_non_finite),
        ("Confidence threshold behavior", test_path_corridor_fusion_confidence_threshold),
        ("Boundary format validation", test_path_corridor_fusion_boundary_format),
    ]

    passed = 0
    failed = 0

    for name, test_func in tests:
        try:
            test_func()
            passed += 1
        except Exception as e:
            print(f"❌ FAILED: {name}")
            print(f"   Error: {e}\n")
            import traceback
            traceback.print_exc()
            failed += 1

    print("\n" + "="*70)
    print(f"  Test Results: {passed}/{len(tests)} passed, {failed}/{len(tests)} failed")
    print("="*70 + "\n")

    if failed == 0:
        print("🎉 ALL TESTS PASSED! Corridor fusion ready for integration.\n")
        exit(0)
    else:
        print(f"⚠️  {failed} test(s) failed. Please review.\n")
        exit(1)

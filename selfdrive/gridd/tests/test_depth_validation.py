#!/usr/bin/env python3
"""
Depth Model Validation Test for EOP10 Stereo Pipeline.

Validates that cv2.reprojectImageTo3D produces correct metric distances
with the Q matrix from stereo calibration.

Usage:
    pytest selfdrive/gridd/tests/test_depth_validation.py -v
    python selfdrive/gridd/tests/test_depth_validation.py  # Run synthetic test
"""

from __future__ import annotations

import numpy as np
import cv2
import pytest


class TestDepthValidation:
    """Test suite for stereo depth validation."""

    # Test parameters
    IMAGE_WIDTH = 1280
    IMAGE_HEIGHT = 720
    BASELINE_M = 0.08  # 80mm stereo baseline
    FOCAL_PX = 700.0  # Typical focal length in pixels

    def _create_test_q_matrix(self, baseline_m: float, focal_px: float, cx: float, cy: float) -> np.ndarray:
        """
        Create Q matrix for stereo rectification.

        Q = [
            [1, 0, 0, -cx],
            [0, 1, 0, -cy],
            [0, 0, 0,  f],
            [0, 0, -1/B, (cx-cx')/B]  # cx' should be same as cx after rectification
        ]
        """
        Q = np.float64([
            [1, 0, 0, -cx],
            [0, 1, 0, -cy],
            [0, 0, 0,  focal_px],
            [0, 0, -1.0 / baseline_m, 0],
        ])
        return Q

    def _create_synthetic_disparity(self, true_depth_m: float, add_noise: bool = False) -> np.ndarray:
        """
        Create synthetic disparity map with known depth.

        depth = f * B / disparity
        disparity = f * B / depth
        """
        disparity = (self.FOCAL_PX * self.BASELINE_M) / true_depth_m
        # Create disparity map with optional noise
        disp_map = np.full((self.IMAGE_HEIGHT, self.IMAGE_WIDTH), disparity, dtype=np.float32)
        if add_noise:
            # Add small noise for realistic simulation
            noise = np.random.normal(0, 0.1, disp_map.shape).astype(np.float32)
            disp_map += noise
        return disp_map

    def test_q_matrix_properties(self):
        """Test that Q matrix has correct properties."""
        cx = self.IMAGE_WIDTH / 2.0
        cy = self.IMAGE_HEIGHT / 2.0
        Q = self._create_test_q_matrix(self.BASELINE_M, self.FOCAL_PX, cx, cy)

        # Check Q matrix structure
        assert Q[0, 0] == 1.0, "Q[0,0] should be 1"
        assert Q[1, 1] == 1.0, "Q[1,1] should be 1"
        assert Q[0, 3] == -cx, "Q[0,3] should be -cx"
        assert Q[1, 3] == -cy, "Q[1,3] should be -cy"
        assert Q[2, 3] == self.FOCAL_PX, "Q[2,3] should be focal length"
        assert Q[3, 2] == -1.0 / self.BASELINE_M, "Q[3,2] should be -1/B"

    def test_reprojection_accuracy_close_range(self):
        """Test depth accuracy at close range (5-15m)."""
        cx = self.IMAGE_WIDTH / 2.0
        cy = self.IMAGE_HEIGHT / 2.0
        Q = self._create_test_q_matrix(self.BASELINE_M, self.FOCAL_PX, cx, cy)

        test_depths = [5.0, 10.0, 15.0]  # meters
        tolerance = 1.0  # 1m tolerance for synthetic test

        for true_depth in test_depths:
            disparity = self._create_synthetic_disparity(true_depth, add_noise=False)
            xyz = cv2.reprojectImageTo3D(disparity, Q)

            # Check center pixel depth (should be most accurate)
            center_y, center_x = self.IMAGE_HEIGHT // 2, self.IMAGE_WIDTH // 2
            # OpenCV stereoRectify produces negative Z (forward in camera coords)
            # We take absolute value for distance
            measured_depth = abs(xyz[center_y, center_x, 2])

            error = abs(measured_depth - true_depth)
            assert error < tolerance, (
                f"Depth error too large at {true_depth}m: " +
                f"measured={measured_depth:.2f}m, error={error:.2f}m"
            )

    def test_reprojection_accuracy_mid_range(self):
        """Test depth accuracy at mid range (20-50m)."""
        cx = self.IMAGE_WIDTH / 2.0
        cy = self.IMAGE_HEIGHT / 2.0
        Q = self._create_test_q_matrix(self.BASELINE_M, self.FOCAL_PX, cx, cy)

        test_depths = [20.0, 30.0, 50.0]  # meters
        tolerance_pct = 0.15  # 15% tolerance at mid range for synthetic test

        for true_depth in test_depths:
            disparity = self._create_synthetic_disparity(true_depth, add_noise=False)
            xyz = cv2.reprojectImageTo3D(disparity, Q)

            center_y, center_x = self.IMAGE_HEIGHT // 2, self.IMAGE_WIDTH // 2
            # OpenCV stereoRectify produces negative Z (forward in camera coords)
            measured_depth = abs(xyz[center_y, center_x, 2])

            error_pct = abs(measured_depth - true_depth) / true_depth
            assert error_pct < tolerance_pct, (
                f"Depth error too large at {true_depth}m: " +
                f"measured={measured_depth:.2f}m, error={error_pct*100:.1f}%"
            )

    def test_coordinate_system(self):
        """Test that coordinate system follows OpenCV stereoRectify conventions.

        OpenCV stereoRectify convention (camera coordinates):
        - X: right (positive right)
        - Y: down (positive down)
        - Z: forward (positive forward) - but OpenCV produces negative Z!

        Note: OpenCV's reprojectImageTo3D produces points in camera coordinates
        where Z is negative for points in front of the camera. The EOP10 code
        takes absolute value or uses the Z component appropriately.
        """
        cx = self.IMAGE_WIDTH / 2.0
        cy = self.IMAGE_HEIGHT / 2.0
        Q = self._create_test_q_matrix(self.BASELINE_M, self.FOCAL_PX, cx, cy)

        # Create disparity with known depth
        true_depth = 10.0
        disparity = self._create_synthetic_disparity(true_depth)
        xyz = cv2.reprojectImageTo3D(disparity, Q)

        # Check center pixel - Z should be approximately -true_depth (negative forward)
        center_y, center_x = self.IMAGE_HEIGHT // 2, self.IMAGE_WIDTH // 2
        z_forward = xyz[center_y, center_x, 2]

        # OpenCV convention: Z is negative for points in front of camera
        assert z_forward < 0, f"OpenCV Z should be negative (forward), got {z_forward}"
        # With clean synthetic data, should be within 0.5m
        assert abs(abs(z_forward) - true_depth) < 0.5, (
            f"Forward distance should be close to depth, got {abs(z_forward)} vs {true_depth}"
        )

    def test_disparity_to_depth_formula(self):
        """Verify the disparity to depth formula: Z = f*B/disparity."""
        test_depths = [5.0, 10.0, 20.0, 50.0]

        for true_depth in test_depths:
            # Expected disparity
            expected_disp = (self.FOCAL_PX * self.BASELINE_M) / true_depth

            # Convert back to depth
            computed_depth = (self.FOCAL_PX * self.BASELINE_M) / expected_disp

            assert abs(computed_depth - true_depth) < 1e-6, (
                f"Depth formula error: expected {true_depth}, got {computed_depth}"
            )

    def test_q_matrix_with_calibration(self):
        """Test Q matrix loading from calibration format."""
        # Simulate calibration file format used by v4l2d/gridd
        calibration = {
            'baseline_m': self.BASELINE_M,
            'focal_px': self.FOCAL_PX,
            'cx': self.IMAGE_WIDTH / 2.0,
            'cy': self.IMAGE_HEIGHT / 2.0,
        }

        # Build Q matrix as done in gridd.py
        f_px = calibration['focal_px']
        cx, cy = calibration['cx'], calibration['cy']
        baseline_m = calibration['baseline_m']

        Q = np.float64([
            [1, 0, 0, -cx],
            [0, 1, 0, -cy],
            [0, 0, 0,  f_px],
            [0, 0, -1.0 / baseline_m, 0],
        ])

        # Validate with reprojection
        true_depth = 15.0
        disparity = np.full((100, 100), (f_px * baseline_m) / true_depth, dtype=np.float32)
        xyz = cv2.reprojectImageTo3D(disparity, Q)

        # OpenCV produces negative Z for forward points
        measured_depth = abs(xyz[50, 50, 2])
        assert abs(measured_depth - true_depth) < 0.5, (
            f"Calibration-based depth error: expected {true_depth}, got {measured_depth}"
        )


class TestDepthWithCalibrationFile:
    """Test depth validation using actual calibration files (if available)."""

    CALIBRATION_PATH = "/data/calibration/stereo_intrinsics.npz"

    @pytest.mark.skipif(
        not pytest.path.exists(CALIBRATION_PATH) if hasattr(pytest, 'path') else False,
        reason="Calibration file not available"
    )
    def test_with_real_calibration(self):
        """Test depth reprojection with real calibration data."""
        cal = np.load(self.CALIBRATION_PATH)
        Q = cal['Q']

        # Verify Q matrix is valid
        assert Q.shape == (4, 4), f"Q matrix should be 4x4, got {Q.shape}"
        assert np.isfinite(Q).all(), "Q matrix should contain finite values"

        # Test reprojection with synthetic data
        focal_px = abs(Q[2, 3])
        baseline_m = -1.0 / Q[3, 2]

        true_depth = 10.0
        disparity = (focal_px * baseline_m) / true_depth
        disp_map = np.full((720, 1280), disparity, dtype=np.float32)

        xyz = cv2.reprojectImageTo3D(disp_map, Q)
        measured_depth = xyz[360, 640, 2]

        # With real calibration, we expect better accuracy
        assert abs(measured_depth - true_depth) < 0.1, (
            f"Real calibration depth error too large: {measured_depth} vs {true_depth}"
        )


def run_synthetic_tests():
    """Run synthetic tests without pytest."""
    print("Running Depth Model Validation Tests (Synthetic)")
    print("=" * 60)

    test_class = TestDepthValidation()

    tests = [
        ("Q Matrix Properties", test_class.test_q_matrix_properties),
        ("Reprojection Accuracy (Close Range)", test_class.test_reprojection_accuracy_close_range),
        ("Reprojection Accuracy (Mid Range)", test_class.test_reprojection_accuracy_mid_range),
        ("Coordinate System", test_class.test_coordinate_system),
        ("Disparity to Depth Formula", test_class.test_disparity_to_depth_formula),
        ("Q Matrix with Calibration", test_class.test_q_matrix_with_calibration),
    ]

    passed = 0
    failed = 0

    for name, test_func in tests:
        try:
            test_func()
            print(f"✓ {name}")
            passed += 1
        except AssertionError as e:
            print(f"✗ {name}: {e}")
            failed += 1
        except Exception as e:
            print(f"✗ {name}: Unexpected error: {e}")
            failed += 1

    print("=" * 60)
    print(f"Results: {passed} passed, {failed} failed")
    return failed == 0


if __name__ == "__main__":
    import sys
    success = run_synthetic_tests()
    sys.exit(0 if success else 1)

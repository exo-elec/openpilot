"""Metric fixtures for the stereo-to-grid boundary, independent of hardware IPC."""
import importlib.util
from pathlib import Path

import numpy as np
import pytest

# Import the numerical boundary without gridd/__init__ starting daemon imports.
spec = importlib.util.spec_from_file_location('stereo_geometry', Path(__file__).resolve().parents[1] / 'stereo_geometry.py')
assert spec is not None and spec.loader is not None
geometry = importlib.util.module_from_spec(spec)
spec.loader.exec_module(geometry)
reproject_disparity = geometry.reproject_disparity


def calibration():
  # OpenCV rectified stereo: f=100 px, baseline=0.08 m, principal point=(4,4).
  return np.array([[1, 0, 0, -4], [0, 1, 0, -4],
                   [0, 0, 0, 100], [0, 0, 12.5, 0]], dtype=float)


def test_metric_depth_and_original_pixel_coordinates_with_true_stride():
  disparity = np.full((9, 9), 2.0)
  points = reproject_disparity(disparity, calibration())
  assert points.shape == (9, 3)  # exactly rows/cols 0,4,8; no offsets 2 or 3
  np.testing.assert_allclose(points[:, 2], 4.0)
  np.testing.assert_allclose(points[0], [-0.16, -0.16, 4.0])
  np.testing.assert_allclose(points[4], [0.0, 0.0, 4.0])


def test_complete_q_including_homogeneous_offset_and_scale():
  q = calibration()
  q[3, 3] = 25.0
  points = reproject_disparity(np.full((1, 1), 2.0), q)
  np.testing.assert_allclose(points, [[-0.08, -0.08, 2.0]])
  np.testing.assert_allclose(reproject_disparity(np.full((1, 1), 2.0), q * 3), points)


def test_invalid_disparity_and_confidence_never_create_geometry():
  disparity = np.array([[2, 0, -2, np.nan, np.inf, 2, 2, 2]], dtype=float)
  confidence = np.array([[1, 1, 1, 1, 1, np.nan, 0.2, np.inf]])
  points = reproject_disparity(disparity, calibration(), confidence, step=1)
  assert points.shape == (1, 3)
  assert points.dtype == np.float32


def test_behind_near_far_and_lateral_outliers_are_excluded():
  disparity = np.array([[16.0, 0.01, 2.0]])  # 0.5 m, 800 m, 4 m
  points = reproject_disparity(disparity, calibration(), step=1)
  assert points.shape == (1, 3)
  assert reproject_disparity(disparity, -calibration(), step=1).shape == (1, 3)
  q = calibration()
  q[2, 3] = -100
  assert reproject_disparity(disparity, q, step=1).shape == (0, 3)
  assert reproject_disparity(disparity, calibration(), step=1, max_lateral=0.01).shape == (0, 3)


def test_zero_homogeneous_denominator_is_unknown():
  q = calibration()
  q[3] = 0
  assert reproject_disparity(np.ones((4, 4)), q).shape == (0, 3)


@pytest.mark.parametrize('q', [np.eye(3), np.full((4, 4), np.nan)])
def test_malformed_calibration_rejected(q):
  with pytest.raises(ValueError):
    reproject_disparity(np.ones((4, 4)), q)


def test_mismatched_confidence_and_invalid_sampling_rejected():
  with pytest.raises(ValueError):
    reproject_disparity(np.ones((4, 4)), calibration(), np.ones((2, 2)))
  with pytest.raises(ValueError):
    reproject_disparity(np.ones((4, 4)), calibration(), step=0)

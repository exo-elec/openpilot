"""Metric optical-frame reference for lazy stereo reprojection.

Q belongs to the rectified image resolution. Output is right/down/forward;
camera-to-vehicle extrinsics remain the caller's responsibility. This NumPy
reference does not claim GPU execution.
"""
from __future__ import annotations

import numpy as np


def reproject_disparity(disparity: np.ndarray, q: np.ndarray,
                        confidence: np.ndarray | None = None, *, step: int = 4,
                        min_confidence: float = 0.3, min_depth: float = 0.5,
                        max_depth: float = 80.0, max_lateral: float = 15.0) -> np.ndarray:
  """Project finite confident samples with the complete homogeneous Q matrix.

  Disparity is floating-point pixels, not a matcher's fixed-point encoding.
  Sample coordinates stay in the original image; subsampling does not resize
  the image or change calibration. Invalid/missing returns provide no points.
  """
  disparity = np.asarray(disparity)
  q = np.asarray(q, dtype=np.float64)
  if disparity.ndim != 2 or q.shape != (4, 4) or not np.isfinite(q).all():
    raise ValueError('Expected a disparity image and a finite 4x4 Q matrix')
  if not isinstance(step, int) or step < 1:
    raise ValueError('Sampling step must be a positive integer')
  if not (0 <= min_confidence <= 1 and 0 <= min_depth < max_depth and max_lateral > 0):
    raise ValueError('Invalid confidence or metric range limits')
  if confidence is not None and np.shape(confidence) != disparity.shape:
    raise ValueError('Confidence shape must match disparity')

  sampled = disparity[::step, ::step]
  valid = np.isfinite(sampled) & (sampled > 0)
  if confidence is not None:
    certainty = np.asarray(confidence)[::step, ::step]
    valid &= np.isfinite(certainty) & (certainty > min_confidence)
  rows, cols = np.nonzero(valid)
  if not len(rows):
    return np.empty((0, 3), dtype=np.float32)

  pixels = np.column_stack((cols * step, rows * step, sampled[valid], np.ones(len(rows))))
  homogeneous = pixels @ q.T
  denominator = homogeneous[:, 3]
  usable = np.isfinite(homogeneous).all(axis=1) & (np.abs(denominator) > np.finfo(np.float64).tiny)
  with np.errstate(over='ignore', invalid='ignore', divide='ignore'):
    xyz = homogeneous[usable, :3] / denominator[usable, None]
  inside = (np.isfinite(xyz).all(axis=1) & (xyz[:, 2] > min_depth) &
            (xyz[:, 2] < max_depth) & (np.abs(xyz[:, 0]) < max_lateral))
  return xyz[inside].astype(np.float32)

"""
Unit tests for path_corridor_fusion.

Frame: the calibrated frame of modelV2 and pathd's path, y right positive
(CLAUDE.md "Frame conventions", as sunnypilot/comma). The left road edge is
negative, the right positive.
"""
import importlib.util

import numpy as np

from cereal import log
import cereal.messaging as messaging

# Direct import to avoid the pathd init chain
_spec = importlib.util.spec_from_file_location('path_corridor_fusion', 'selfdrive/pathd/path_corridor_fusion.py')
fusion_module = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(fusion_module)

fuse_corridor_boundaries = fusion_module.fuse_corridor_boundaries
validate_corridor_boundaries = fusion_module.validate_corridor_boundaries
SEGMENTATION_HIGH_CONFIDENCE = fusion_module.SEGMENTATION_HIGH_CONFIDENCE
VISION_DEFAULT_CONFIDENCE = fusion_module.VISION_DEFAULT_CONFIDENCE
DEFAULT_CORRIDOR_WIDTH = fusion_module.DEFAULT_CORRIDOR_WIDTH
Source = log.EnhancedTrajectory.BoundarySource


def _edge(y: float):
  msg = log.XYZTData.new_message()
  msg.x = [float(i) for i in range(33)]
  msg.y = [y] * 33
  msg.z = [0.0] * 33
  return msg


def _model(left_y: float, right_y: float):
  msg = messaging.new_message("modelV2").modelV2
  msg.roadEdges = [_edge(left_y), _edge(right_y)]
  return msg


def _ground(left_y: float, right_y: float, confidence: float):
  msg = messaging.new_message("groundObjects").groundObjects
  msg.roadGeometry.roadEdges = [_edge(left_y), _edge(right_y)]
  msg.roadGeometry.roadEdgeConfidence = confidence
  return msg


def test_confident_segmentation_wins():
  left, right, source, _ = fuse_corridor_boundaries(_model(-2.0, 2.0), _ground(-2.5, 2.5, 0.9))
  assert source == Source.segmentation
  assert np.allclose(left, -2.5) and np.allclose(right, 2.5)


def test_low_confidence_segmentation_falls_back_to_vision():
  left, right, source, conf = fuse_corridor_boundaries(_model(-2.0, 2.0), _ground(-2.5, 2.5, 0.4))
  assert source == Source.vision
  assert conf == VISION_DEFAULT_CONFIDENCE
  assert np.allclose(left, -2.0) and np.allclose(right, 2.0)


def test_confidence_threshold_is_inclusive():
  model = _model(-2.0, 2.0)
  _, _, source, _ = fuse_corridor_boundaries(model, _ground(-2.5, 2.5, float(SEGMENTATION_HIGH_CONFIDENCE)))
  assert source == Source.segmentation
  _, _, source, _ = fuse_corridor_boundaries(model, _ground(-2.5, 2.5, float(SEGMENTATION_HIGH_CONFIDENCE) - 0.01))
  assert source == Source.vision


def test_model_road_edges_pass_validation():
  # The regression: modelV2 edges (left negative) used to fail a
  # left-positive width check, so pathd always fell back to +-1.5 m.
  left, right, _, _ = fuse_corridor_boundaries(_model(-2.0, 2.0), None)
  assert validate_corridor_boundaries(left, right)


def test_default_corridor_is_left_negative():
  left, right, source, conf = fuse_corridor_boundaries(None, None)
  assert source == Source.vision and conf == 0.0
  assert np.allclose(left, -DEFAULT_CORRIDOR_WIDTH) and np.allclose(right, DEFAULT_CORRIDOR_WIDTH)


def test_validate_width_limits():
  assert not validate_corridor_boundaries(np.full(33, -0.5), np.full(33, 0.5))  # 1 m
  assert not validate_corridor_boundaries(np.full(33, -5.0), np.full(33, 5.0))  # 10 m
  assert not validate_corridor_boundaries(np.full(33, 2.5), np.full(33, -2.5))  # left/right swapped


def test_validate_rejects_bad_input():
  assert not validate_corridor_boundaries(np.full(20, -2.5), np.full(33, 2.5))
  left = np.full(33, -2.5)
  left[3] = np.nan
  assert not validate_corridor_boundaries(left, np.full(33, 2.5))

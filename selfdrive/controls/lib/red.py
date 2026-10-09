"""
red.py - Road Edge Detection (RED), ExoPilot version

The shared core (states, temporal filter, repulsive cost, path override, vision-only edges) lives in
nagaspilot/controls/ngp_red.py and is also on NGP10. ExoPilot extends it by corroborating each vision edge with YOLO
barrier detections and stereo depth, classifying the edge type, and running only when EOPRedControllerEnabled is set.

Multi-sensor fusion: Vision roadEdges + YOLO barriers + Stereo depth. An edge is kept when the fused confidence reaches
MIN_FUSED_CONF, or when vision alone is already that confident (so this is never weaker than the vision-only core).
"""

import time
import numpy as np
from typing import Any
from nagaspilot.controls.ngp_red import (
  CRITICAL_DISTANCE, MIN_EDGE_DISTANCE, MIN_FUSED_CONF, MIN_VISION_CONF, TYPE_MULTIPLIERS, WARNING_DISTANCE,
  NGPRED, REDState, RoadEdge, RoadEdgeType,
)
from openpilot.common.params import Params

__all__ = ["RED", "REDState", "RoadEdge", "RoadEdgeType", "WARNING_DISTANCE", "CRITICAL_DISTANCE", "MIN_EDGE_DISTANCE",
           "MIN_VISION_CONF", "MIN_FUSED_CONF", "TYPE_MULTIPLIERS", "BARRIER_CLASSES"]

# YOLO classes that indicate barriers
BARRIER_CLASSES = ['guardrail', 'wall', 'barrier', 'fence', 'curb']


class RED(NGPRED):
  """
  Road Edge Detection Controller.

  Provides safety guardrail for Laneless mode by detecting and
  avoiding physical road boundaries using multi-sensor fusion.
  """

  PARAM_REFRESH_S = 2.0

  def __init__(self):
    super().__init__()
    self.params = Params()
    self.enabled = False
    self._last_param_t = 0.0
    self.detected_edges: list[RoadEdge] = []
    self.prev_edges: list[RoadEdge] = []

  def detect_road_edges(self, model_v2, yolo_detections, stereo_data) -> list[RoadEdge]:
    """
    Multi-sensor road edge detection.

    Args:
      model_v2: modelV2 cereal message with roadEdges (confidence from roadEdgeStds)
      yolo_detections: List of YOLO detection objects
      stereo_data: Stereo depth data from gridd (optional)

    Returns:
      List of RoadEdge objects with fused confidence
    """
    edges = []
    if model_v2 is None or not hasattr(model_v2, 'roadEdges'):
      return edges

    stds = list(getattr(model_v2, 'roadEdgeStds', ()) or ())
    for i, edge in enumerate(model_v2.roadEdges):
      if hasattr(edge, 'prob'):
        vision_conf = edge.prob
      elif i < len(stds):
        vision_conf = max(0.0, min(1.0, 1.0 - float(stds[i])))  # modelV2 edges carry a std, not a probability
      else:
        continue
      if vision_conf < MIN_VISION_CONF:
        continue

      side = 'left' if i == 0 else 'right'

      if hasattr(edge, 'x') and hasattr(edge, 'y'):
        points = list(zip(edge.x, edge.y, strict=False))
      else:
        continue
      if len(points) < 2:
        continue

      road_edge = RoadEdge(
        side=side,
        points=points,
        edge_type=self._classify_edge_type(edge, yolo_detections, points),
        vision_confidence=vision_conf,
        yolo_confidence=self._validate_with_yolo(side, points, yolo_detections),
        stereo_confidence=self._verify_with_stereo(points, stereo_data),
      )

      if road_edge.fused_confidence >= MIN_FUSED_CONF or vision_conf >= MIN_FUSED_CONF:
        edges.append(road_edge)

    return edges

  def _validate_with_yolo(self, side: str, points: list[tuple[float, float]],
                          yolo_detections) -> float:
    """
    Validate edge with YOLO barrier detection.

    Returns confidence boost (0.0 - 0.8) if YOLO confirms barrier.
    """
    if yolo_detections is None or len(points) == 0:
      return 0.0

    # Get average Y position of edge (first few points)
    edge_y = np.mean([p[1] for p in points[:5]])

    for det in yolo_detections:
      # Check if detection is a barrier type
      class_label = getattr(det, 'class_label', getattr(det, 'label', ''))
      if class_label.lower() not in BARRIER_CLASSES:
        continue

      # Check lateral alignment with edge
      det_y = getattr(det, 'y', getattr(det, 'center_y', 0))
      if abs(det_y - edge_y) < 0.5:  # Within 0.5m
        return 0.8  # High confidence boost

    return 0.0

  def _verify_with_stereo(self, points: list[tuple[float, float]],
                          stereo_data) -> float:
    """
    Verify edge with stereo depth discontinuity.

    Returns confirmation ratio (0.0 - 1.0).
    """
    if stereo_data is None or len(points) == 0:
      return 0.0

    confirmed = 0
    check_points = min(5, len(points))

    for point in points[:check_points]:
      if self._check_depth_discontinuity(point, stereo_data):
        confirmed += 1

    return confirmed / check_points if check_points > 0 else 0.0

  def _check_depth_discontinuity(self, point: tuple[float, float],
                                 stereo_data) -> bool:
    """
    Check for depth discontinuity at point indicating physical edge.

    A significant depth change (>30cm) indicates physical boundary.
    """
    if stereo_data is None:
      return False

    # Get depth values on either side of point
    # This requires access to gridd depth map format
    if hasattr(stereo_data, 'depth_map'):
      try:
        x, y = int(point[0]), int(point[1])
        # Sample inside and outside the edge
        depth_in = stereo_data.depth_map[y, x - 5] if x >= 5 else 0
        depth_out = stereo_data.depth_map[y, x + 5] if x + 5 < stereo_data.depth_map.shape[1] else 0

        # Check for step change
        if abs(depth_in - depth_out) > 0.3:  # 30cm
          return True
      except (IndexError, AttributeError):
        pass

    return False

  def _classify_edge_type(self, vision_edge, yolo_detections,
                          points: list[tuple[float, float]]) -> RoadEdgeType:
    """
    Classify edge type from vision and YOLO data.
    """
    # Check YOLO for specific barrier types
    if yolo_detections is not None:
      edge_y = np.mean([p[1] for p in points[:5]]) if points else 0

      for det in yolo_detections:
        class_label = getattr(det, 'class_label', getattr(det, 'label', '')).lower()
        det_y = getattr(det, 'y', getattr(det, 'center_y', 0))

        if abs(det_y - edge_y) < 1.0:
          if 'guardrail' in class_label:
            return RoadEdgeType.GUARDRAIL
          elif 'wall' in class_label or 'barrier' in class_label:
            return RoadEdgeType.WALL
          elif 'curb' in class_label:
            return RoadEdgeType.CURB
          elif 'grass' in class_label or 'dirt' in class_label:
            return RoadEdgeType.GRASS

    return RoadEdgeType.UNKNOWN

  # Names kept from the pre-refactor class
  def _temporal_filter(self, new_edges: list[RoadEdge]) -> list[RoadEdge]:
    return self.temporal_filter(new_edges)

  def _lateral_distance(self, position: tuple[float, float], edge: RoadEdge) -> float:
    return self.lateral_distance(position, edge)

  def calculate_repulsive_cost(self, vehicle_y: float, road_edge: RoadEdge, v_ego: float) -> float:
    return self.repulsive_cost(vehicle_y, road_edge, v_ego)

  def apply_path_safety_override(self, planned_path: list[tuple[float, float]],
                                 road_edges: list[RoadEdge]) -> tuple[list[tuple[float, float]], bool]:
    return self.path_override(planned_path, road_edges)

  def update(self, model_v2, yolo_detections, stereo_data,
             vehicle_position: tuple[float, float], v_ego: float,
             planned_path: list[tuple[float, float]],
             is_laneless: bool) -> dict[str, Any]:
    """
    Main update method - called each control cycle.

    Returns:
      Dict with lateral_cost, path_override, state, edges info. edge_side: -1 = edge on the left (push right),
      +1 = edge on the right (push left).
    """
    now = time.monotonic()
    if now - self._last_param_t >= self.PARAM_REFRESH_S:
      self._last_param_t = now
      self.enabled = self.params.get_bool("EOPRedControllerEnabled")

    # RED only active when enabled AND in Laneless mode
    if not self.enabled or not is_laneless:
      self.detected_edges = []
      return self.inactive_output(planned_path)

    self.detected_edges = self.detect_road_edges(model_v2, yolo_detections, stereo_data)
    return self.evaluate(self.detected_edges, vehicle_position, v_ego, planned_path)

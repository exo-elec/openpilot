"""
red.py - Road Edge Detection (RED)

Provides a safety layer for Laneless mode by detecting physical road
boundaries (curbs, grass, guardrails, walls) and preventing the vehicle
from drifting into non-navigable areas.

Multi-sensor fusion: Vision roadEdges + YOLO barriers + Stereo depth
"""

import time
import numpy as np
from enum import Enum
from dataclasses import dataclass
from typing import Any
from openpilot.common.params import Params


class REDState(Enum):
  """RED operating states."""
  INACTIVE = 0    # No edges detected or disabled
  MONITORING = 1  # Edges detected, far enough
  WARNING = 2     # Edge within 1.0m, cost added
  CRITICAL = 3    # Edge within 0.5m, path override


class RoadEdgeType(Enum):
  """Types of road edges detectable."""
  CURB = 0        # Concrete curb
  GRASS = 1       # Grass/dirt shoulder
  GUARDRAIL = 2   # Metal guardrail
  WALL = 3        # Concrete wall/barrier
  UNKNOWN = 4     # Unclassified boundary


@dataclass
class RoadEdge:
  """Represents a detected road edge with fused confidence."""
  side: str  # 'left' or 'right'
  points: list[tuple[float, float]]  # [(x, y), ...]
  edge_type: RoadEdgeType
  vision_confidence: float
  yolo_confidence: float
  stereo_confidence: float

  @property
  def fused_confidence(self) -> float:
    """
    Combined confidence from all sources.

    Weights: Vision 0.5, YOLO 0.3, Stereo 0.2
    """
    weights = [0.5, 0.3, 0.2]
    confs = [self.vision_confidence, self.yolo_confidence, self.stereo_confidence]
    return sum(w * c for w, c in zip(weights, confs))


# Distance thresholds (meters)
WARNING_DISTANCE = 1.0
CRITICAL_DISTANCE = 0.5
MIN_EDGE_DISTANCE = 0.3  # Absolute minimum

# Confidence thresholds
MIN_VISION_CONF = 0.5
MIN_FUSED_CONF = 0.6

# Edge type danger multipliers
TYPE_MULTIPLIERS = {
  RoadEdgeType.CURB: 1.5,
  RoadEdgeType.GUARDRAIL: 1.3,
  RoadEdgeType.WALL: 1.4,
  RoadEdgeType.GRASS: 1.0,
  RoadEdgeType.UNKNOWN: 1.2,
}

# YOLO classes that indicate barriers
BARRIER_CLASSES = ['guardrail', 'wall', 'barrier', 'fence', 'curb']


class RED:
  """
  Road Edge Detection Controller.

  Provides safety guardrail for Laneless mode by detecting and
  avoiding physical road boundaries using multi-sensor fusion.
  """

  PARAM_REFRESH_S = 2.0

  def __init__(self):
    self.params = Params()
    self.enabled = False
    self._last_param_t = 0.0

    # State
    self.state = REDState.INACTIVE
    self.detected_edges: list[RoadEdge] = []
    self.filtered_edges: list[RoadEdge] = []
    self.prev_edges: list[RoadEdge] = []

    # Safety outputs
    self.lateral_cost = 0.0
    self.path_override_active = False
    self.closest_distance = float('inf')

    # Temporal filtering
    self.edge_history: list[list[RoadEdge]] = []
    self.history_length = 3  # Require edge to persist for 3 frames

  def detect_road_edges(self, model_v2, yolo_detections, stereo_data) -> list[RoadEdge]:
    """
    Multi-sensor road edge detection.

    Args:
      model_v2: modelV2 cereal message with roadEdges
      yolo_detections: List of YOLO detection objects
      stereo_data: Stereo depth data from gridd (optional)

    Returns:
      List of RoadEdge objects with fused confidence
    """
    edges = []

    # 1. Vision-based detection from modelV2.roadEdges
    if model_v2 is None or not hasattr(model_v2, 'roadEdges'):
      return edges

    for i, edge in enumerate(model_v2.roadEdges):
      # Check confidence threshold
      if not hasattr(edge, 'prob') or edge.prob < MIN_VISION_CONF:
        continue

      side = 'left' if i == 0 else 'right'

      # Extract edge points
      if hasattr(edge, 'x') and hasattr(edge, 'y'):
        points = list(zip(edge.x, edge.y))
      else:
        continue

      if len(points) < 2:
        continue

      # 2. YOLO validation - check for barrier objects near edge
      yolo_conf = self._validate_with_yolo(side, points, yolo_detections)

      # 3. Stereo depth verification
      stereo_conf = self._verify_with_stereo(points, stereo_data)

      # 4. Classify edge type
      edge_type = self._classify_edge_type(edge, yolo_detections, points)

      # Create fused edge
      road_edge = RoadEdge(
        side=side,
        points=points,
        edge_type=edge_type,
        vision_confidence=edge.prob,
        yolo_confidence=yolo_conf,
        stereo_confidence=stereo_conf,
      )

      # Only keep edges with sufficient fused confidence
      if road_edge.fused_confidence >= MIN_FUSED_CONF:
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

  def _temporal_filter(self, new_edges: list[RoadEdge]) -> list[RoadEdge]:
    """
    Apply temporal filtering - require edge to persist for multiple frames.

    Reduces false positives from momentary detections.
    """
    # Add current edges to history
    self.edge_history.append(new_edges)
    if len(self.edge_history) > self.history_length:
      self.edge_history.pop(0)

    # If not enough history, return empty (be conservative)
    if len(self.edge_history) < self.history_length:
      return []

    # Find edges that persist across all frames
    filtered = []
    for edge in new_edges:
      persist_count = 0
      for historical in self.edge_history[:-1]:
        for hist_edge in historical:
          if edge.side == hist_edge.side:
            # Check if edge position is similar
            if len(edge.points) > 0 and len(hist_edge.points) > 0:
              dist = abs(edge.points[0][1] - hist_edge.points[0][1])
              if dist < 0.3:  # Within 30cm
                persist_count += 1
                break

      if persist_count >= self.history_length - 1:
        filtered.append(edge)

    return filtered

  def _lateral_distance(self, position: tuple[float, float], edge: RoadEdge) -> float:
    """Calculate lateral distance from position to edge."""
    if len(edge.points) == 0:
      return float('inf')

    # Use average Y of first few edge points
    edge_y = np.mean([p[1] for p in edge.points[:5]])
    return abs(position[1] - edge_y)

  def calculate_repulsive_cost(self, vehicle_y: float, road_edge: RoadEdge,
                               v_ego: float) -> float:
    """
    Calculate cost to steer away from road edge.

    Uses exponential cost function that increases as vehicle
    approaches the edge. Higher speeds get more aggressive avoidance.

    Args:
      vehicle_y: Current lateral position (m from center)
      road_edge: RoadEdge object
      v_ego: Current speed (m/s)

    Returns:
      Cost value (0.0 to 2.0+)
    """
    if len(road_edge.points) == 0:
      return 0.0

    # Distance to edge
    edge_y = np.mean([p[1] for p in road_edge.points[:5]])
    distance = abs(edge_y - vehicle_y)

    if distance > WARNING_DISTANCE:
      return 0.0

    # Exponential cost function
    proximity = (WARNING_DISTANCE - distance) / WARNING_DISTANCE
    base_cost = 0.5 * (proximity ** 2)

    # Speed factor (1.0 at 0 m/s, 2.0 at 30 m/s)
    speed_mult = 1.0 + (v_ego / 30.0)

    # Edge type danger factor
    type_mult = TYPE_MULTIPLIERS.get(road_edge.edge_type, 1.0)

    return base_cost * speed_mult * type_mult

  def apply_path_safety_override(self, planned_path: list[tuple[float, float]],
                                 road_edges: list[RoadEdge]) -> tuple[list[tuple[float, float]], bool]:
    """
    Modify planned path to avoid crossing road edges.

    This is the ultimate safety layer - prevents path from
    crossing physical road boundaries.

    Args:
      planned_path: List of path points (x, y)
      road_edges: List of RoadEdge objects

    Returns:
      (modified_path, override_active)
    """
    override_active = False
    modified_path = list(planned_path)  # Copy

    for edge in road_edges:
      if len(edge.points) == 0:
        continue

      edge_y = np.mean([p[1] for p in edge.points[:5]])

      for i, path_point in enumerate(modified_path):
        distance_to_edge = abs(path_point[1] - edge_y)

        if distance_to_edge < MIN_EDGE_DISTANCE:
          # CRITICAL: Path would cross or get too close to edge
          safe_offset = MIN_EDGE_DISTANCE - distance_to_edge + 0.1  # Extra margin

          # Direction: push away from edge
          direction = 1 if edge.side == 'left' else -1

          # Apply offset to this and all future path points
          for j in range(i, len(modified_path)):
            modified_path[j] = (
              modified_path[j][0],
              modified_path[j][1] + safe_offset * direction
            )

          override_active = True

    return modified_path, override_active

  def update(self, model_v2, yolo_detections, stereo_data,
             vehicle_position: tuple[float, float], v_ego: float,
             planned_path: list[tuple[float, float]],
             is_laneless: bool) -> dict[str, Any]:
    """
    Main update method - called each control cycle.

    Args:
      model_v2: modelV2 cereal message
      yolo_detections: YOLO barrier detections from gridd
      stereo_data: Stereo depth data from gridd (optional)
      vehicle_position: (x, y) relative to path center
      v_ego: Current speed (m/s)
      planned_path: Current planned path points
      is_laneless: Whether operating in Laneless mode

    Returns:
      Dict with lateral_cost, path_override, state, edges info
    """
    now = time.monotonic()
    if now - self._last_param_t >= self.PARAM_REFRESH_S:
      self._last_param_t = now
      self.enabled = self.params.get_bool("EOPRedControllerEnabled")

    # RED only active when enabled AND in Laneless mode
    if not self.enabled or not is_laneless:
      self.state = REDState.INACTIVE
      self.lateral_cost = 0.0
      self.path_override_active = False
      self.detected_edges = []
      self.filtered_edges = []
      return {
        'lateral_cost': 0.0,
        'path_override': False,
        'modified_path': planned_path,
        'state': REDState.INACTIVE.name,
        'edges_detected': 0,
        'closest_distance': float('inf'),
      }

    # Detect edges with multi-sensor fusion
    self.detected_edges = self.detect_road_edges(model_v2, yolo_detections, stereo_data)

    # Apply temporal filtering
    self.filtered_edges = self._temporal_filter(self.detected_edges)

    # Update state based on closest edge
    if not self.filtered_edges:
      self.state = REDState.INACTIVE
      self.closest_distance = float('inf')
    else:
      # Find closest edge distance
      self.closest_distance = min(
        self._lateral_distance(vehicle_position, edge)
        for edge in self.filtered_edges
      )

      if self.closest_distance < CRITICAL_DISTANCE:
        self.state = REDState.CRITICAL
      elif self.closest_distance < WARNING_DISTANCE:
        self.state = REDState.WARNING
      else:
        self.state = REDState.MONITORING

    # Calculate repulsive costs for all edges
    total_cost = 0.0
    for edge in self.filtered_edges:
      cost = self.calculate_repulsive_cost(vehicle_position[1], edge, v_ego)
      total_cost += cost

    self.lateral_cost = min(total_cost, 2.0)  # Cap at 2.0

    # Apply path override if in CRITICAL state
    modified_path = planned_path
    if self.state == REDState.CRITICAL:
      modified_path, self.path_override_active = self.apply_path_safety_override(
        list(planned_path), self.filtered_edges
      )
    else:
      self.path_override_active = False

    # Determine which side the closest edge is on (positive y = left in vehicle frame).
    # edge_side: -1 = left edge (push right), +1 = right edge (push left), 0 = unknown.
    closest_edge_side = 0
    if self.filtered_edges:
      closest_edge = min(
        self.filtered_edges,
        key=lambda e: abs(float(np.mean([p[1] for p in e.points[:5]])) - vehicle_position[1])
      )
      edge_y = float(np.mean([p[1] for p in closest_edge.points[:5]]))
      closest_edge_side = -1 if edge_y > vehicle_position[1] else 1

    return {
      'lateral_cost': self.lateral_cost,
      'path_override': self.path_override_active,
      'modified_path': modified_path,
      'state': self.state.name,
      'edges_detected': len(self.filtered_edges),
      'closest_distance': self.closest_distance,
      'edge_side': closest_edge_side,
    }

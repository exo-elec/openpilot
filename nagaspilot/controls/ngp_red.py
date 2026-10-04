"""RED — Road Edge Detection: keep the car away from physical road edges (curbs, grass, guardrails, walls).

Pure policy, comma-3 inputs only. The basic version below works from `modelV2` alone: its road edges
(`roadEdges` x/y) with confidence taken from `roadEdgeStds`. It keeps a short temporal filter, a state machine
(monitoring / warning / critical by lateral distance) and an exponential repulsive cost. ExoPilot extends it by
corroborating edges with YOLO barrier detections and stereo depth, and by classifying the edge type.

Frames: modelV2 is calibrated-frame, y **right** positive (a left edge has negative y). `edge_side` follows the
curvature convention instead: -1 = edge on the left (push right), +1 = edge on the right (push left).
"""
from dataclasses import dataclass
from enum import Enum
from typing import Any

import numpy as np


class REDState(Enum):
  INACTIVE = 0    # no edges, or disabled
  MONITORING = 1  # edges seen, far enough
  WARNING = 2     # edge within 1.0 m: repulsive cost added
  CRITICAL = 3    # edge within 0.5 m: path override


class RoadEdgeType(Enum):
  CURB = 0
  GRASS = 1
  GUARDRAIL = 2
  WALL = 3
  UNKNOWN = 4


WARNING_DISTANCE = 1.0  # m
CRITICAL_DISTANCE = 0.5  # m
MIN_EDGE_DISTANCE = 0.3  # m, absolute minimum
MIN_VISION_CONF = 0.5
MIN_FUSED_CONF = 0.6
HISTORY_LENGTH = 3  # an edge must persist for this many frames
TYPE_MULTIPLIERS = {
  RoadEdgeType.CURB: 1.5, RoadEdgeType.GUARDRAIL: 1.3, RoadEdgeType.WALL: 1.4,
  RoadEdgeType.GRASS: 1.0, RoadEdgeType.UNKNOWN: 1.2,
}


@dataclass
class RoadEdge:
  side: str  # 'left' or 'right'
  points: list[tuple[float, float]]  # [(x, y), ...] modelV2 frame
  edge_type: RoadEdgeType
  vision_confidence: float
  yolo_confidence: float = 0.0
  stereo_confidence: float = 0.0

  @property
  def fused_confidence(self) -> float:
    """Vision 0.5, YOLO 0.3, stereo 0.2."""
    return 0.5 * self.vision_confidence + 0.3 * self.yolo_confidence + 0.2 * self.stereo_confidence


def vision_edges(model_v2, min_conf: float = MIN_FUSED_CONF) -> list[RoadEdge]:
  """Road edges from modelV2 alone. Confidence is 1 - roadEdgeStd (the std the model reports per edge)."""
  edges: list[RoadEdge] = []
  if model_v2 is None:
    return edges
  stds = list(getattr(model_v2, 'roadEdgeStds', ()) or ())
  for i, edge in enumerate(getattr(model_v2, 'roadEdges', ()) or ()):
    if i >= 2 or i >= len(stds):
      break
    conf = max(0.0, min(1.0, 1.0 - float(stds[i])))
    if conf < min_conf or len(edge.x) < 2 or len(edge.x) != len(edge.y):
      continue
    edges.append(RoadEdge('left' if i == 0 else 'right', list(zip(edge.x, edge.y, strict=False)), RoadEdgeType.UNKNOWN, conf))
  return edges


def _edge_y(edge: RoadEdge) -> float:
  return float(np.mean([p[1] for p in edge.points[:5]]))


class NGPRED:
  def __init__(self):
    self.state = REDState.INACTIVE
    self.filtered_edges: list[RoadEdge] = []
    self.lateral_cost = 0.0
    self.path_override_active = False
    self.closest_distance = float('inf')
    self.edge_history: list[list[RoadEdge]] = []
    self.history_length = HISTORY_LENGTH

  def reset(self):
    self.state = REDState.INACTIVE
    self.filtered_edges = []
    self.lateral_cost = 0.0
    self.path_override_active = False
    self.closest_distance = float('inf')

  def inactive_output(self, planned_path) -> dict[str, Any]:
    self.reset()
    return {'lateral_cost': 0.0, 'path_override': False, 'modified_path': planned_path, 'state': REDState.INACTIVE.name,
            'edges_detected': 0, 'closest_distance': float('inf')}

  def temporal_filter(self, new_edges: list[RoadEdge]) -> list[RoadEdge]:
    """Require an edge to persist for several frames; empty until the history is full (conservative)."""
    self.edge_history.append(new_edges)
    if len(self.edge_history) > self.history_length:
      self.edge_history.pop(0)
    if len(self.edge_history) < self.history_length:
      return []
    filtered = []
    for edge in new_edges:
      persist = 0
      for past in self.edge_history[:-1]:
        for past_edge in past:
          if edge.side == past_edge.side and edge.points and past_edge.points and abs(edge.points[0][1] - past_edge.points[0][1]) < 0.3:
            persist += 1
            break
      if persist >= self.history_length - 1:
        filtered.append(edge)
    return filtered

  @staticmethod
  def lateral_distance(position: tuple[float, float], edge: RoadEdge) -> float:
    if not edge.points:
      return float('inf')
    return abs(position[1] - _edge_y(edge))

  @staticmethod
  def repulsive_cost(vehicle_y: float, edge: RoadEdge, v_ego: float) -> float:
    """Exponential cost that grows as the car nears the edge (more at speed and for harder edges)."""
    if not edge.points:
      return 0.0
    distance = abs(_edge_y(edge) - vehicle_y)
    if distance > WARNING_DISTANCE:
      return 0.0
    proximity = (WARNING_DISTANCE - distance) / WARNING_DISTANCE
    return 0.5 * proximity ** 2 * (1.0 + v_ego / 30.0) * TYPE_MULTIPLIERS.get(edge.edge_type, 1.0)

  @staticmethod
  def path_override(planned_path, edges: list[RoadEdge]):
    """Shift a planned path that would cross an edge. modelV2 y is right-positive: push a left edge's path +y."""
    override = False
    path = list(planned_path)
    for edge in edges:
      if not edge.points:
        continue
      edge_y = _edge_y(edge)
      for i, point in enumerate(path):
        if abs(point[1] - edge_y) < MIN_EDGE_DISTANCE:
          offset = MIN_EDGE_DISTANCE - abs(point[1] - edge_y) + 0.1
          direction = 1 if edge.side == 'left' else -1
          for j in range(i, len(path)):
            path[j] = (path[j][0], path[j][1] + offset * direction)
          override = True
    return path, override

  def evaluate(self, edges: list[RoadEdge], vehicle_position: tuple[float, float], v_ego: float, planned_path) -> dict[str, Any]:
    self.filtered_edges = self.temporal_filter(edges)
    if not self.filtered_edges:
      self.state, self.closest_distance = REDState.INACTIVE, float('inf')
    else:
      self.closest_distance = min(self.lateral_distance(vehicle_position, e) for e in self.filtered_edges)
      self.state = (REDState.CRITICAL if self.closest_distance < CRITICAL_DISTANCE
                    else REDState.WARNING if self.closest_distance < WARNING_DISTANCE else REDState.MONITORING)

    self.lateral_cost = min(sum(self.repulsive_cost(vehicle_position[1], e, v_ego) for e in self.filtered_edges), 2.0)

    modified_path = planned_path
    if self.state == REDState.CRITICAL:
      modified_path, self.path_override_active = self.path_override(list(planned_path), self.filtered_edges)
    else:
      self.path_override_active = False

    edge_side = 0
    if self.filtered_edges:
      closest = min(self.filtered_edges, key=lambda e: abs(_edge_y(e) - vehicle_position[1]))
      # modelV2 y is right-positive: an edge at larger y is on the RIGHT, so push left (+1)
      edge_side = 1 if _edge_y(closest) > vehicle_position[1] else -1

    return {'lateral_cost': self.lateral_cost, 'path_override': self.path_override_active, 'modified_path': modified_path,
            'state': self.state.name, 'edges_detected': len(self.filtered_edges), 'closest_distance': self.closest_distance,
            'edge_side': edge_side}

  def update(self, model_v2, vehicle_position: tuple[float, float], v_ego: float, planned_path, is_laneless: bool) -> dict[str, Any]:
    """Basic (vision-only) update: active only in laneless mode."""
    if not is_laneless:
      return self.inactive_output(planned_path)
    return self.evaluate(vision_edges(model_v2), vehicle_position, v_ego, planned_path)


def curvature_nudge(red_output: dict[str, Any]) -> float:
  """Small curvature delta (1/m) away from a close edge; positive = left turn."""
  if red_output['lateral_cost'] <= 0 or red_output.get('edges_detected', 0) <= 0 or red_output.get('closest_distance', float('inf')) >= 1.0:
    return 0.0
  return (1.0 if red_output.get('edge_side', 0) > 0 else -1.0) * abs(red_output['lateral_cost'] * 0.001)



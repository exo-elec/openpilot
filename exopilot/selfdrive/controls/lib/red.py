"""RED — Road Edge Detection: keep the car away from physical road edges (curbs, grass, guardrails, walls).

One implementation for every branch. Edges come from `modelV2` (`roadEdges` x/y, confidence 1 - `roadEdgeStds`);
where a branch has them (EOP10), YOLO barrier detections and stereo depth corroborate each edge and give its type.
Without them (NGP10, comma 3) the result is the vision-only one: an edge needs vision confidence >= MIN_FUSED_CONF.
A short temporal filter, a state machine (monitoring / warning / critical by lateral distance) and an exponential
repulsive cost follow. Switch: optional `get_bool` (EOP10 passes Params().get_bool, key RedControllerEnabled,
re-read every 2 s); without it RED is always on (NGP10's controlsd builds it only when the param is set).

Frames: modelV2 is calibrated-frame, y **right** positive (a left edge has negative y). `edge_side` follows the
curvature convention instead: -1 = edge on the left (push right), +1 = edge on the right (push left).
"""
import time
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
PARAM_REFRESH_S = 2.0
BARRIER_CLASSES = ['guardrail', 'wall', 'barrier', 'fence', 'curb']  # YOLO labels that confirm a physical edge
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


def _label(det) -> str:
  return str(getattr(det, 'class_label', getattr(det, 'label', ''))).lower()


def _det_y(det) -> float:
  return getattr(det, 'y', getattr(det, 'center_y', 0))


def yolo_confidence(points: list[tuple[float, float]], detections) -> float:
  """0.8 when a YOLO barrier lies within 0.5 m laterally of the edge, else 0."""
  if detections is None or not points:
    return 0.0
  edge_y = np.mean([p[1] for p in points[:5]])
  for det in detections:
    if _label(det) in BARRIER_CLASSES and abs(_det_y(det) - edge_y) < 0.5:
      return 0.8
  return 0.0


def _depth_step(point: tuple[float, float], stereo_data) -> bool:
  """A depth change of more than 30 cm across the point marks a physical edge."""
  depth_map = getattr(stereo_data, 'depth_map', None)
  if depth_map is None:
    return False
  try:
    x, y = int(point[0]), int(point[1])
    depth_in = depth_map[y, x - 5] if x >= 5 else 0
    depth_out = depth_map[y, x + 5] if x + 5 < depth_map.shape[1] else 0
    return abs(depth_in - depth_out) > 0.3
  except (IndexError, AttributeError):
    return False


def stereo_confidence(points: list[tuple[float, float]], stereo_data) -> float:
  """Share of the first five edge points that sit on a stereo depth step."""
  if stereo_data is None or not points:
    return 0.0
  check = points[:5]
  return sum(_depth_step(p, stereo_data) for p in check) / len(check)


def classify_edge_type(points: list[tuple[float, float]], detections) -> RoadEdgeType:
  """Edge type from the YOLO label within 1 m of the edge; UNKNOWN without detections."""
  if detections is None:
    return RoadEdgeType.UNKNOWN
  edge_y = np.mean([p[1] for p in points[:5]]) if points else 0
  for det in detections:
    label = _label(det)
    if abs(_det_y(det) - edge_y) < 1.0:
      if 'guardrail' in label:
        return RoadEdgeType.GUARDRAIL
      if 'wall' in label or 'barrier' in label:
        return RoadEdgeType.WALL
      if 'curb' in label:
        return RoadEdgeType.CURB
      if 'grass' in label or 'dirt' in label:
        return RoadEdgeType.GRASS
  return RoadEdgeType.UNKNOWN


def detect_edges(model_v2, yolo_detections=None, stereo_data=None) -> list[RoadEdge]:
  """Road edges from modelV2, corroborated by YOLO and stereo when given.

  Vision confidence is 1 - roadEdgeStd (or the edge's `prob`, if a message carries one). An edge is kept when the fused
  confidence reaches MIN_FUSED_CONF, or when vision alone does, so the extra inputs can only add edges, never drop one.
  """
  edges: list[RoadEdge] = []
  if model_v2 is None:
    return edges
  stds = list(getattr(model_v2, 'roadEdgeStds', ()) or ())
  for i, edge in enumerate(getattr(model_v2, 'roadEdges', ()) or ()):
    if i >= 2:
      break
    if hasattr(edge, 'prob'):
      conf = float(edge.prob)
    elif i < len(stds):
      conf = max(0.0, min(1.0, 1.0 - float(stds[i])))
    else:
      continue
    if conf < MIN_VISION_CONF or len(edge.x) < 2 or len(edge.x) != len(edge.y):
      continue
    points = list(zip(edge.x, edge.y, strict=True))
    road_edge = RoadEdge('left' if i == 0 else 'right', points, classify_edge_type(points, yolo_detections), conf,
                         yolo_confidence(points, yolo_detections), stereo_confidence(points, stereo_data))
    if road_edge.fused_confidence >= MIN_FUSED_CONF or conf >= MIN_FUSED_CONF:
      edges.append(road_edge)
  return edges


def vision_edges(model_v2) -> list[RoadEdge]:
  """Road edges from modelV2 alone."""
  return detect_edges(model_v2)


def _edge_y(edge: RoadEdge) -> float:
  return float(np.mean([p[1] for p in edge.points[:5]]))


class RED:
  def __init__(self, get_bool=None, enabled_key: str = "RedControllerEnabled", clock=time.monotonic):
    self._get_bool, self._key, self._clock = get_bool, enabled_key, clock
    self._param_t = -1e9
    self.enabled = get_bool is None
    self.detected_edges: list[RoadEdge] = []
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

  def update(self, model_v2, vehicle_position: tuple[float, float], v_ego: float, planned_path, is_laneless: bool,
             yolo_detections=None, stereo_data=None) -> dict[str, Any]:
    """One control cycle. Active only when enabled and in laneless mode."""
    if self._get_bool is not None:
      now = self._clock()
      if now - self._param_t >= PARAM_REFRESH_S:
        self._param_t, self.enabled = now, bool(self._get_bool(self._key))
    if not self.enabled or not is_laneless:
      self.detected_edges = []
      return self.inactive_output(planned_path)
    self.detected_edges = detect_edges(model_v2, yolo_detections, stereo_data)
    return self.evaluate(self.detected_edges, vehicle_position, v_ego, planned_path)


def curvature_nudge(red_output: dict[str, Any]) -> float:
  """Small curvature delta (1/m) away from a close edge; positive = left turn."""
  if red_output['lateral_cost'] <= 0 or red_output.get('edges_detected', 0) <= 0 or red_output.get('closest_distance', float('inf')) >= 1.0:
    return 0.0
  return (1.0 if red_output.get('edge_side', 0) > 0 else -1.0) * abs(red_output['lateral_cost'] * 0.001)



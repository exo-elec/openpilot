"""Pure modelV2 road-edge gate used by NGP10 lane-change proposals."""

from dataclasses import dataclass


@dataclass(frozen=True)
class RoadEdgeResult:
  left_blocked: bool
  right_blocked: bool
  left_confidence: float
  right_confidence: float
  valid: bool


def evaluate_road_edges(road_edge_stds, lane_line_probs) -> RoadEdgeResult:
  stds = tuple(float(value) for value in (road_edge_stds or ()))
  probs = tuple(float(value) for value in (lane_line_probs or ()))
  if len(stds) < 2 or len(probs) < 4:
    return RoadEdgeResult(False, False, 0.0, 0.0, False)
  left_confidence = max(0.0, min(1.0, 1.0 - stds[0]))
  right_confidence = max(0.0, min(1.0, 1.0 - stds[1]))
  left_blocked = left_confidence > 0.35 and probs[0] < 0.2 and probs[3] >= probs[0]
  right_blocked = right_confidence > 0.35 and probs[3] < 0.2 and probs[0] >= probs[3]
  return RoadEdgeResult(left_blocked, right_blocked, left_confidence, right_confidence, True)

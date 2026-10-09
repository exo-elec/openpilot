"""Stateless lane-change gates for EOP: road edge, adjacent gap, lane width, blind spot.

The gap and lane-width checks are the shared policy in ngp_lane_change.py (also on NGP10);
the road-edge blinker guard and blind-spot priority stay here (EOP radar/BSM inputs).
No Params, messaging or daemon imports.
"""
from enum import Enum, auto

from cereal import log
from nagaspilot.controls.ngp_lane_change import MIN_LANE_WIDTH, evaluate_gap as _evaluate_gap, validate_lane_width as _validate_lane_width

LaneChangeDirection = log.LaneChangeDirection

RADAR_TO_CAMERA = 1.52  # same offset radard applies to vision leads


class Dir(Enum):
  LEFT = auto()
  RIGHT = auto()


def _clip01(x: float) -> float:
  return min(max(x, 0.0), 1.0)


def is_road_edge_blinker(md, right_blinker: bool, left_blinker: bool) -> bool:
  """Block ALC when the blinker points toward a detected road edge.

  Uses modelV2 road-edge standard deviations and nearside lane-line
  probabilities. A high-confidence road edge on the blinker side with a
  low-probability nearside lane line means there is no real adjacent lane.
  Driver confirmation is still required; this is a safety guard, not a
  replacement for attention.
  """
  if md is None:
    return False

  if not hasattr(md, 'roadEdgeStds') or not hasattr(md, 'laneLineProbs'):
    return False

  edge_threshold = 0.475
  try:
    if len(md.roadEdgeStds) < 2 or len(md.laneLineProbs) < 4:
      return False
    left_edge_prob = _clip01(1.0 - md.roadEdgeStds[0])
    right_edge_prob = _clip01(1.0 - md.roadEdgeStds[1])
    left_nearside_prob = md.laneLineProbs[0]
    right_nearside_prob = md.laneLineProbs[3]
  except Exception:
    return False

  if (right_edge_prob > edge_threshold and right_nearside_prob < 0.2 and
      left_nearside_prob >= right_nearside_prob):
    road_edge_stat = Dir.RIGHT
  elif (left_edge_prob > edge_threshold and left_nearside_prob < 0.2 and
        right_nearside_prob >= left_nearside_prob):
    road_edge_stat = Dir.LEFT
  else:
    return False

  return (right_blinker and road_edge_stat == Dir.RIGHT) or (left_blinker and road_edge_stat == Dir.LEFT)


def evaluate_gap(radar_state, model_v2, direction: str, v_ego: float) -> tuple[bool, float]:
  """EOP signature (radar first) over the shared policy in ngp_lane_change.py."""
  return _evaluate_gap(model_v2, direction, v_ego, radar_state=radar_state, radar_to_camera=RADAR_TO_CAMERA)


def validate_lane_width(model_v2, direction: str, min_lane_width: float = MIN_LANE_WIDTH) -> bool:
  """Shared target-lane width check (ngp_lane_change.py)."""
  return _validate_lane_width(model_v2, direction, min_lane_width)


def blindspot_blocked(carstate, blind_spot_alert, direction) -> bool:
  """Check if a lane change is blocked for the given direction.

  Priority order:
    1. Vehicle-native BSM (CAN hardware — always authoritative)
    2. Immediate BSD zone (±15m, radar4d/2d/camera → alert + chime)
    3. Wide LCA gate (up to 100m, radar3d TTC-based — no alert/chime)
  """
  if direction == LaneChangeDirection.left and carstate.leftBlindspot:
    return True
  if direction == LaneChangeDirection.right and carstate.rightBlindspot:
    return True
  if blind_spot_alert is None:
    return False
  if direction == LaneChangeDirection.left:
    return (blind_spot_alert.leftDetected
            or blind_spot_alert.leftAlertLevel >= 1
            or blind_spot_alert.lcaBlockedLeft)
  if direction == LaneChangeDirection.right:
    return (blind_spot_alert.rightDetected
            or blind_spot_alert.rightAlertLevel >= 1
            or blind_spot_alert.lcaBlockedRight)
  return False

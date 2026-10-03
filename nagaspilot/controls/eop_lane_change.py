"""Stateless lane-change gates for EOP: road edge, adjacent gap, lane width, blind spot.

Moved verbatim from selfdrive/controls/lib/desire_helper.py so the upstream file keeps only
the state machine and thin calls into this module. No Params, messaging or daemon imports.
"""
from enum import Enum, auto

import numpy as np
from cereal import log

LaneChangeDirection = log.LaneChangeDirection

MIN_TIME_GAP = 2.0  # seconds - minimum TTC for safe gap
MIN_LANE_WIDTH = 2.5  # meters - minimum lane width for safe change
FAST_APPROACH_THRESHOLD = -10.0  # m/s (36 km/h faster)
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
  """Evaluate whether the adjacent-lane gap is safe for a lane change using TTC.

  Pure-camera style: uses radar (if available) and modelV2 vision leads.

  Args:
    radar_state: Radar data with lead information (may be None on camera-only platforms)
    model_v2: ModelV2 message with vision leads (leadsV3)
    direction: 'left' or 'right'
    v_ego: Current ego speed (m/s)

  Returns:
    (is_safe, confidence) tuple
  """
  adjacent_leads = []

  # 1. Radar leads (if available)
  # radarState yRel: car frame, left positive (log.capnp LeadData)
  if radar_state:
    for lead in [radar_state.leadOne, radar_state.leadTwo]:
      if not lead.status:
        continue
      if direction == 'left' and lead.yRel > 1.5:
        adjacent_leads.append((lead.dRel, lead.vRel))
      elif direction == 'right' and lead.yRel < -1.5:
        adjacent_leads.append((lead.dRel, lead.vRel))

  # 2. Vision leads from modelV2 (pure-camera fallback / augmentation)
  if model_v2 and len(model_v2.leadsV3) > 0:
    for lead in model_v2.leadsV3:
      if lead.prob < 0.5:
        continue
      # modelV2 lead.y[0] is calibrated frame, right positive; flip to
      # yRel (left positive) as radard does. Adjacent lane is |y| > 1.5m.
      y_rel = -lead.y[0]
      if direction == 'left' and y_rel > 1.5:
        d_rel = lead.x[0] - RADAR_TO_CAMERA
        v_rel = lead.v[0] - v_ego
        adjacent_leads.append((d_rel, v_rel))
      elif direction == 'right' and y_rel < -1.5:
        d_rel = lead.x[0] - RADAR_TO_CAMERA
        v_rel = lead.v[0] - v_ego
        adjacent_leads.append((d_rel, v_rel))

  # No adjacent leads detected
  if not adjacent_leads:
    return True, 1.0

  # Evaluate each adjacent lead
  for d_rel, v_rel in adjacent_leads:
    # Check for fast-approaching vehicle
    if v_rel < FAST_APPROACH_THRESHOLD:
      return False, 0.0

    # Calculate time to collision (TTC)
    if v_rel < 0:  # Lead is approaching
      time_to_collision = d_rel / abs(v_rel) if abs(v_rel) > 0.1 else float('inf')
      if time_to_collision < MIN_TIME_GAP:
        return False, 0.0
    else:
      # Lead moving away or same speed - check minimum distance
      if d_rel < 10.0:  # 10m minimum
        return False, 0.3

  return True, 1.0


def validate_lane_width(model_v2, direction: str, min_lane_width: float = MIN_LANE_WIDTH) -> bool:
  """Validate target lane is physically wide enough and is a real lane (not shoulder).

  Interpolates at multiple forward distances and takes median — robust to close-range
  artifacts where lane lines may be parallel or crossing. np.minimum() on outer_dist
  and edge_dist means the road edge can REDUCE effective lane width, correctly
  rejecting shoulders (edge closer than far lane line → apparent width collapses).
  """
  if not model_v2:
    return True
  try:
    SAMPLE_X = [5.0, 10.0, 20.0, 30.0, 40.0]

    if direction == 'left':
      inner_ll = model_v2.laneLines[1] if len(model_v2.laneLines) > 1 else None
      outer_ll = model_v2.laneLines[0] if len(model_v2.laneLines) > 0 else None
      edge_ll = model_v2.roadEdges[0] if len(model_v2.roadEdges) > 0 else None
    else:
      inner_ll = model_v2.laneLines[2] if len(model_v2.laneLines) > 2 else None
      outer_ll = model_v2.laneLines[3] if len(model_v2.laneLines) > 3 else None
      edge_ll = model_v2.roadEdges[1] if len(model_v2.roadEdges) > 1 else None

    if inner_ll is None or not len(inner_ll.x):
      return True

    ix = list(inner_ll.x)
    iy = list(inner_ll.y)
    x_max = ix[-1]
    valid_x = [x for x in SAMPLE_X if x <= x_max]
    if not valid_x:
      return True

    inner_y = np.array([np.interp(x, ix, iy) for x in valid_x])
    inner_dist = np.abs(inner_y)

    if outer_ll is not None and len(outer_ll.x):
      outer_y = np.array([np.interp(x, list(outer_ll.x), list(outer_ll.y)) for x in valid_x])
      outer_dist = np.abs(outer_y)
    else:
      outer_dist = np.full(len(valid_x), np.inf)

    if edge_ll is not None and len(edge_ll.x):
      edge_y = np.array([np.interp(x, list(edge_ll.x), list(edge_ll.y)) for x in valid_x])
      edge_dist = np.abs(edge_y)
    else:
      edge_dist = np.full(len(valid_x), np.inf)

    # Road edge caps the effective outer boundary — shoulder detection via np.minimum
    effective_dist = np.minimum(outer_dist, edge_dist)
    widths = effective_dist - inner_dist
    lane_width = float(np.median(widths))
    return lane_width >= min_lane_width

  except (IndexError, AttributeError, TypeError, ValueError):
    return True


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

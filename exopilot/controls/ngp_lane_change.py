"""Stateless lane-change gates from modelV2: adjacent-lane gap and target-lane width.

Pure policy, like ngp_road_edge / ngp_lc_lead_handoff: no Params, messaging or cereal imports.
Works from comma 3 inputs only (modelV2 leadsV3, laneLines, roadEdges). Enable-gating is the
caller's job (see DesireHelper). Missing or malformed data never blocks: a guard that cannot
see the lane stays out of the way and the driver's own checks apply.

Frames: modelV2 y is calibrated-frame, right positive; radarState yRel is left positive. Model
leads are flipped once, here, as radard does. `radar_state` is optional and not wired in
modeld: radarState.leadOne/leadTwo are radard's radar-refined copies of modelV2.leadsV3[0/1],
published one frame after modelV2, so they would double-count the same vehicle.
"""
import numpy as np

MIN_TIME_GAP = 2.0  # s, minimum TTC for a safe gap
MIN_LANE_WIDTH = 2.5  # m, minimum target-lane width
FAST_APPROACH_THRESHOLD = -10.0  # m/s closing speed that always blocks
MIN_FOLLOW_DISTANCE = 10.0  # m, lead pulling away or level with us
ADJACENT_LANE_Y_MIN = 1.5  # m, |y| beyond which a lead is in the neighbouring lane
MIN_LEAD_PROB = 0.5
DEFAULT_RADAR_TO_CAMERA = 1.52  # same offset radard applies to vision leads
SAMPLE_X = (5.0, 10.0, 20.0, 30.0, 40.0)


def evaluate_gap(model_v2, direction: str, v_ego: float, radar_state=None,
                 radar_to_camera: float = DEFAULT_RADAR_TO_CAMERA) -> tuple[bool, float]:
  """Is the adjacent-lane gap on `direction` ('left'/'right') safe? Returns (is_safe, confidence)."""
  adjacent = []

  if radar_state:
    for lead in (radar_state.leadOne, radar_state.leadTwo):
      if not lead.status:
        continue
      if (direction == 'left' and lead.yRel > ADJACENT_LANE_Y_MIN) or (direction == 'right' and lead.yRel < -ADJACENT_LANE_Y_MIN):
        adjacent.append((lead.dRel, lead.vRel))

  if model_v2 is not None:
    for lead in model_v2.leadsV3:
      if lead.prob < MIN_LEAD_PROB:
        continue
      y_rel = -lead.y[0]
      if (direction == 'left' and y_rel > ADJACENT_LANE_Y_MIN) or (direction == 'right' and y_rel < -ADJACENT_LANE_Y_MIN):
        adjacent.append((lead.x[0] - radar_to_camera, lead.v[0] - v_ego))

  for d_rel, v_rel in adjacent:
    if v_rel < FAST_APPROACH_THRESHOLD:
      return False, 0.0
    if v_rel < 0:
      if abs(v_rel) > 0.1 and d_rel / abs(v_rel) < MIN_TIME_GAP:
        return False, 0.0
    elif d_rel < MIN_FOLLOW_DISTANCE:
      return False, 0.3
  return True, 1.0


def validate_lane_width(model_v2, direction: str, min_lane_width: float = MIN_LANE_WIDTH) -> bool:
  """Is the target lane wide enough and a real lane (not shoulder)? Median over several look-aheads;
  the road edge caps the outer boundary, so a close edge collapses the width and rejects it."""
  if model_v2 is None:
    return True
  try:
    inner, outer, edge_idx = (1, 0, 0) if direction == 'left' else (2, 3, 1)
    lines = model_v2.laneLines
    edges = model_v2.roadEdges
    inner_ll = lines[inner] if len(lines) > inner else None
    outer_ll = lines[outer] if len(lines) > outer else None
    edge_ll = edges[edge_idx] if len(edges) > edge_idx else None

    if inner_ll is None or not len(inner_ll.x):
      return True
    valid_x = [x for x in SAMPLE_X if x <= inner_ll.x[-1]]
    if not valid_x:
      return True

    def dist(ll):
      if ll is None or not len(ll.x):
        return np.full(len(valid_x), np.inf)
      return np.abs([np.interp(x, list(ll.x), list(ll.y)) for x in valid_x])

    widths = np.minimum(dist(outer_ll), dist(edge_ll)) - dist(inner_ll)
    return float(np.median(widths)) >= min_lane_width
  except (IndexError, AttributeError, TypeError, ValueError):
    return True

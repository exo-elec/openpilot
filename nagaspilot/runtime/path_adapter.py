"""monoDetections / modelV2 -> inputs for the path selector (adapter; policy in nagaspilot/controls/ngp_path_selector.py)."""
from nagaspilot.controls.ngp_path_selector import PObj

EGO_HALF_WIDTH_M = 0.95
LINE_MARGIN_M = 0.15       # keep the tyre this far inside the lane line
MIN_LINE_PROB = 0.5
LOOKAHEAD_IDX = 5          # modelV2 lane line point used for the room (a few metres ahead)


def pobjects(sm) -> tuple[list[PObj], bool]:
  """Return (objects, fresh). Coasting tracks arrive with confidence 0 and are dropped by the selector."""
  fresh = bool(sm.alive.get('monoDetections', False) and sm.valid.get('monoDetections', False))
  if not fresh:
    return [], False
  return [PObj(int(d.trackId), str(d.className), float(d.x), float(d.y), float(d.vx), float(d.vy), float(d.confidence))
          for d in sm['monoDetections'].detections], True


def lane_room(model) -> tuple[float, float]:
  """(room_left, room_right) in metres we may shift inside the lane, from modelV2 lane lines.

  The model is y-RIGHT: the left inner line has negative y, the right inner line positive y.
  Unknown (low probability) lines give zero room on that side: never nudge toward an unseen line.
  """
  try:
    lines, probs = model.laneLines, model.laneLineProbs
    left, right = lines[1].y[LOOKAHEAD_IDX], lines[2].y[LOOKAHEAD_IDX]
  except (AttributeError, IndexError):
    return 0.0, 0.0
  room_left = max(0.0, -left - EGO_HALF_WIDTH_M - LINE_MARGIN_M) if probs[1] >= MIN_LINE_PROB and left < 0 else 0.0
  room_right = max(0.0, right - EGO_HALF_WIDTH_M - LINE_MARGIN_M) if probs[2] >= MIN_LINE_PROB and right > 0 else 0.0
  return room_left, room_right

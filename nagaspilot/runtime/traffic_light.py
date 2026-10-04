"""Traffic-light lamp colour and range from a detector box (sensing; moved from EOP10's monod classifier, RGB frames).

HSV analysis of the box crop, no model: red / yellow / green by the dominant colour fraction (>= 6 %). Range from the head size: a
three-lamp head is about 1 m along its long side, so range = 1.0 * focal / max(box_w, box_h). Not a road user: lights are never
tracked and never ranged on the road plane. Needs cv2 (on the device and in the dev venv).
"""
import numpy as np

STATE = {'unknown': 0, 'red': 1, 'yellow': 2, 'green': 3}
HEAD_LONG_SIDE_M = 1.0
MIN_FRAC = 0.06
MIN_RANGE_M, MAX_RANGE_M = 3.0, 120.0

_RED_L1, _RED_U1 = np.array([0, 80, 80], np.uint8), np.array([10, 255, 255], np.uint8)
_RED_L2, _RED_U2 = np.array([160, 80, 80], np.uint8), np.array([180, 255, 255], np.uint8)
_YEL_L, _YEL_U = np.array([15, 80, 80], np.uint8), np.array([35, 255, 255], np.uint8)
_GRN_L, _GRN_U = np.array([45, 60, 60], np.uint8), np.array([85, 255, 255], np.uint8)


def classify_rgb(frame_rgb: np.ndarray, bbox: tuple[float, float, float, float]) -> tuple[int, float]:
  """(state 0 unknown / 1 red / 2 yellow / 3 green, matching-pixel fraction) for a box (x1, y1, x2, y2) in frame pixels."""
  import cv2
  x1, y1, x2, y2 = (int(v) for v in bbox)
  fh, fw = frame_rgb.shape[:2]
  x1, y1, x2, y2 = max(0, x1), max(0, y1), min(fw - 1, x2), min(fh - 1, y2)
  crop = frame_rgb[y1:y2, x1:x2]
  if crop.size == 0:
    return 0, 0.0
  hsv = cv2.cvtColor(crop, cv2.COLOR_RGB2HSV)
  total = max(crop.shape[0] * crop.shape[1], 1)
  red = float(np.count_nonzero(cv2.inRange(hsv, _RED_L1, _RED_U1) | cv2.inRange(hsv, _RED_L2, _RED_U2))) / total
  yel = float(np.count_nonzero(cv2.inRange(hsv, _YEL_L, _YEL_U))) / total
  grn = float(np.count_nonzero(cv2.inRange(hsv, _GRN_L, _GRN_U))) / total
  best = max(red, yel, grn)
  if best < MIN_FRAC:
    return 0, 0.0
  if red == best:
    return STATE['red'], red
  if yel == best:
    return STATE['yellow'], yel
  return STATE['green'], grn


def head_position(box, focal: float, cx: float) -> tuple[float, float] | None:
  """(forward range, lateral left+) of a light head from its box and the camera focal length / principal column; None out of range."""
  extent = max(box.w, box.h)
  if extent <= 1.0:
    return None
  rng = HEAD_LONG_SIDE_M * focal / extent
  if not (MIN_RANGE_M <= rng <= MAX_RANGE_M):
    return None
  return rng, -(box.cx - cx) * rng / focal

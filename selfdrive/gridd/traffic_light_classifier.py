"""Traffic light color classifier using HSV crop analysis.

Uses the YOLO bounding box crop from the road camera frame to determine
whether a detected traffic light is showing Red, Yellow, or Green.

Reference: proven HSV-range approach used in budget embedded systems.
No additional NPU model required — ~1ms CPU on A76.

Returns:
  (state_int, confidence):
    0 = unknown / cannot classify
    1 = red
    2 = yellow
    3 = green   (currently unused by TLSC but available)
"""
from __future__ import annotations

import numpy as np
import cv2

# HSV ranges for traffic light lamp colors
# Covers both LED and incandescent lamps, daytime and night
_RED_L1 = np.array([  0,  80,  80], dtype=np.uint8)
_RED_U1 = np.array([ 10, 255, 255], dtype=np.uint8)
_RED_L2 = np.array([160,  80,  80], dtype=np.uint8)   # hue wraps at 180
_RED_U2 = np.array([180, 255, 255], dtype=np.uint8)
_YEL_L  = np.array([ 15,  80,  80], dtype=np.uint8)
_YEL_U  = np.array([ 35, 255, 255], dtype=np.uint8)
_GRN_L  = np.array([ 45,  60,  60], dtype=np.uint8)
_GRN_U  = np.array([ 85, 255, 255], dtype=np.uint8)

# Minimum fraction of bbox pixels matching a color for classification
_MIN_FRAC = 0.06


def classify(frame_bgr: np.ndarray,
             bbox: tuple[float, float, float, float]) -> tuple[int, float]:
    """Classify traffic light state from a bbox crop.

    Args:
        frame_bgr: Full camera frame in BGR.
        bbox: (x1, y1, x2, y2) bounding box in frame pixels.

    Returns:
        (state, confidence)  state: 0=unknown 1=red 2=yellow 3=green
        confidence is the matching color pixel fraction (0.0–1.0).
    """
    x1, y1, x2, y2 = map(int, bbox)
    fh, fw = frame_bgr.shape[:2]
    x1 = max(0, x1)
    y1 = max(0, y1)
    x2 = min(fw - 1, x2)
    y2 = min(fh - 1, y2)
    crop = frame_bgr[y1:y2, x1:x2]
    if crop.size == 0:
        return 0, 0.0

    hsv = cv2.cvtColor(crop, cv2.COLOR_BGR2HSV)
    total = max(crop.shape[0] * crop.shape[1], 1)

    red_mask = cv2.inRange(hsv, _RED_L1, _RED_U1) | cv2.inRange(hsv, _RED_L2, _RED_U2)
    yel_mask = cv2.inRange(hsv, _YEL_L, _YEL_U)
    grn_mask = cv2.inRange(hsv, _GRN_L, _GRN_U)

    red_frac = float(np.count_nonzero(red_mask)) / total
    yel_frac = float(np.count_nonzero(yel_mask)) / total
    grn_frac = float(np.count_nonzero(grn_mask)) / total

    # Pick the dominant color above threshold
    best = max(red_frac, yel_frac, grn_frac)
    if best < _MIN_FRAC:
        return 0, 0.0
    if red_frac == best:
        return 1, red_frac
    if yel_frac == best:
        return 2, yel_frac
    return 3, grn_frac

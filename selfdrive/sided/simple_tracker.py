#!/usr/bin/env python3
"""
Lightweight IoU-based multi-object tracker for side cameras.

Inspired by:
  - openpilot selfdrive/controls/radard.py (RadarD track management)
  - SORT tracker (Bewley et al., 2016) — simplified IoU matching only

Algorithm per frame:
1. Compute IoU between every (existing track, new detection) pair.
2. Greedy 1-to-1 matching: assign highest IoU pairs first (threshold = 0.3).
3. Unmatched detections → new tracks with fresh unique UIDs.
4. Unmatched tracks → age += 1; evict if age > MAX_AGE.
5. Matched tracks → update position, compute velocity via EMA.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Dict, List, Tuple

import numpy as np

# ──────────────────────────────────────────────────────────────────────────────
# Constants
# ──────────────────────────────────────────────────────────────────────────────
MAX_AGE: int = 3          # Frames before unmatched track is evicted (150 ms @ 20 Hz)
IOU_THRESHOLD: float = 0.30
VELOCITY_ALPHA: float = 0.4   # EMA weight for velocity smoothing


# ──────────────────────────────────────────────────────────────────────────────
# Detection object (minimal, no external dependencies)
# ──────────────────────────────────────────────────────────────────────────────
@dataclass
class SideObject:
  """A detected object from side camera inference."""
  uid: int = -1
  label: str = 'unknown'
  confidence: float = 0.0
  distance_m: float = 0.0       # longitudinal in vehicle frame
  lateral_m: float = 0.0        # lateral in vehicle frame
  height_m: float = 0.0
  velocity_mps: float = 0.0
  bbox_2d: Tuple[float, float, float, float] = (0.0, 0.0, 0.0, 0.0)
  width_m: float = 1.8
  length_m: float = 4.5


# ──────────────────────────────────────────────────────────────────────────────
# Internal track representation
# ──────────────────────────────────────────────────────────────────────────────
@dataclass
class _Track:
  uid: int
  obj: SideObject
  age: int = 0
  velocity_ema: float = 0.0


# ──────────────────────────────────────────────────────────────────────────────
# IoU helpers
# ──────────────────────────────────────────────────────────────────────────────
def _iou(a: Tuple[float, ...], b: Tuple[float, ...]) -> float:
  """Compute 2-D IoU between two (x1,y1,x2,y2) bounding boxes."""
  ax1, ay1, ax2, ay2 = a
  bx1, by1, bx2, by2 = b

  ix1, iy1 = max(ax1, bx1), max(ay1, by1)
  ix2, iy2 = min(ax2, bx2), min(ay2, by2)
  inter = max(0.0, ix2 - ix1) * max(0.0, iy2 - iy1)
  if inter == 0.0:
    return 0.0

  area_a = (ax2 - ax1) * (ay2 - ay1)
  area_b = (bx2 - bx1) * (by2 - by1)
  union = area_a + area_b - inter
  return inter / union if union > 0.0 else 0.0


# ──────────────────────────────────────────────────────────────────────────────
# Public tracker
# ──────────────────────────────────────────────────────────────────────────────
class SimpleTracker:
  """Maintains persistent UIDs for detected objects across frames."""

  def __init__(self) -> None:
    self._tracks: Dict[int, _Track] = {}
    self._next_uid: int = 1

  def update(self, detections: List[SideObject]) -> List[SideObject]:
    """Match detections to existing tracks and return annotated list."""
    if not detections:
      self._age_tracks(set())
      return []

    track_ids = list(self._tracks.keys())
    n_trk = len(track_ids)
    n_det = len(detections)

    # Build IoU matrix [tracks × detections]
    iou_mat = np.zeros((n_trk, n_det), dtype=np.float32)
    for ti, tid in enumerate(track_ids):
      trk_bbox = self._tracks[tid].obj.bbox_2d
      for di, det in enumerate(detections):
        iou_mat[ti, di] = _iou(trk_bbox, det.bbox_2d)

    # Greedy matching
    matched_tracks: set[int] = set()
    matched_dets: set[int] = set()

    sorted_pairs = sorted(
      [(ti, di) for ti in range(n_trk) for di in range(n_det)],
      key=lambda p: iou_mat[p[0], p[1]],
      reverse=True,
    )
    for ti, di in sorted_pairs:
      if iou_mat[ti, di] < IOU_THRESHOLD:
        break
      if ti in matched_tracks or di in matched_dets:
        continue
      matched_tracks.add(ti)
      matched_dets.add(di)
      tid = track_ids[ti]
      track = self._tracks[tid]
      det = detections[di]

      # Velocity EMA (negative delta because distance_m is typically behind ego)
      delta_dist = det.distance_m - track.obj.distance_m
      track.velocity_ema = (
        VELOCITY_ALPHA * (-delta_dist)
        + (1.0 - VELOCITY_ALPHA) * track.velocity_ema
      )

      det.uid = tid
      det.velocity_mps = track.velocity_ema
      track.obj = det
      track.age = 0

    # New tracks for unmatched detections
    for di, det in enumerate(detections):
      if di not in matched_dets:
        uid = self._next_uid
        self._next_uid += 1
        det.uid = uid
        det.velocity_mps = 0.0
        self._tracks[uid] = _Track(uid=uid, obj=det)

    # Age out unmatched tracks
    matched_uids = {track_ids[ti] for ti in matched_tracks}
    self._age_tracks(matched_uids)

    return detections

  def reset(self) -> None:
    """Clear all tracks (e.g. on sensor reset)."""
    self._tracks.clear()
    self._next_uid = 1

  def _age_tracks(self, matched_uids: set[int]) -> None:
    evict = []
    for uid, track in self._tracks.items():
      if uid not in matched_uids:
        track.age += 1
        if track.age > MAX_AGE:
          evict.append(uid)
    for uid in evict:
      del self._tracks[uid]

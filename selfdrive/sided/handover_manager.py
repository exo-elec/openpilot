#!/usr/bin/env python3
"""
Cross-camera track handover for side cameras.

Problem: an object detected by side_left may momentarily disappear from
that camera's FOV while still being relevant (e.g., entering the rear-wide
camera view, or transitioning to side_right on a curved road).  We need to:

  1. Maintain a *global* UID space across all side cameras.
  2. When a track disappears from one camera, keep it alive for a coast
     period so it can be re-matched if it re-appears in another camera.
  3. Merge tracks that represent the same physical object.

This module is intentionally simple (no graph optimisation).  It uses
spatial proximity in the vehicle frame for cross-camera matching.
"""

from __future__ import annotations

from dataclasses import dataclass

from openpilot.selfdrive.sided.simple_tracker import SideObject

# ──────────────────────────────────────────────────────────────────────────────
# Tuning constants
# ──────────────────────────────────────────────────────────────────────────────
_CROSS_CAMERA_MATCH_RADIUS_M = 3.0
_COAST_MAX_AGE = 5
_MIN_CONFIDENCE = 0.3


@dataclass
class _GlobalTrack:
  """Internal state for a globally-tracked object."""
  uid: int
  camera: str
  obj: SideObject
  age: int = 0
  matched: bool = False


class HandoverManager:
  """Manage persistent global UIDs across multiple side-camera trackers.

  Usage::

    manager = HandoverManager()

    # Each frame, feed in tracked objects from each camera
    left_tracks  = tracker_left.update(detections_left)
    right_tracks = tracker_right.update(detections_right)

    all_global = manager.update({
      'side_left':  left_tracks,
      'side_right': right_tracks,
    })

    # all_global is a flat list of SideObject with stable global UIDs
  """

  def __init__(
    self,
    match_radius_m: float = _CROSS_CAMERA_MATCH_RADIUS_M,
    coast_max_age: int = _COAST_MAX_AGE,
  ) -> None:
    self.match_radius_m = match_radius_m
    self.coast_max_age = coast_max_age
    self._next_uid = 1
    self._tracks: dict[int, _GlobalTrack] = {}

  def update(
    self,
    camera_tracks: dict[str, list[SideObject]],
  ) -> list[SideObject]:
    """Merge per-camera tracks into a globally-consistent track list.

    Args:
      camera_tracks: mapping camera_name → list of SideObject
                     (each list already has per-camera UIDs from its
                     SimpleTracker, but we ignore those and assign
                     global UIDs here).

    Returns:
      Flat list of SideObject with **global** UIDs.
    """
    # 1. Age all existing tracks
    for gt in self._tracks.values():
      gt.age += 1
      gt.matched = False

    # 2. Match incoming detections to existing global tracks
    for camera_name, objects in camera_tracks.items():
      for obj in objects:
        if obj.confidence < _MIN_CONFIDENCE:
          continue

        best_uid: int | None = None
        best_dist = float('inf')

        for uid, gt in self._tracks.items():
          if gt.matched:
            continue
          dist = self._distance(gt.obj, obj)
          if dist < self.match_radius_m and dist < best_dist:
            best_dist = dist
            best_uid = uid

        if best_uid is not None:
          gt = self._tracks[best_uid]
          gt.obj = obj
          gt.age = 0
          gt.matched = True
          gt.camera = camera_name
        else:
          new_uid = self._next_uid
          self._next_uid += 1
          self._tracks[new_uid] = _GlobalTrack(
            uid=new_uid,
            camera=camera_name,
            obj=obj,
            age=0,
            matched=True,
          )

    # 3. Prune old tracks
    dead = [uid for uid, gt in self._tracks.items() if gt.age > self.coast_max_age]
    for uid in dead:
      del self._tracks[uid]

    # 4. Return all active tracks with global UIDs
    result: list[SideObject] = []
    for uid, gt in self._tracks.items():
      obj = SideObject(
        uid=uid,
        label=gt.obj.label,
        confidence=gt.obj.confidence,
        distance_m=gt.obj.distance_m,
        lateral_m=gt.obj.lateral_m,
        height_m=gt.obj.height_m,
        velocity_mps=gt.obj.velocity_mps,
        bbox_2d=gt.obj.bbox_2d,
        width_m=gt.obj.width_m,
        length_m=gt.obj.length_m,
      )
      result.append(obj)

    return result

  @staticmethod
  def _distance(a: SideObject, b: SideObject) -> float:
    """Euclidean distance in vehicle frame (x, y, z)."""
    dx = a.distance_m - b.distance_m
    dy = a.lateral_m - b.lateral_m
    dz = a.height_m - b.height_m
    return (dx * dx + dy * dy + dz * dz) ** 0.5

  def reset(self) -> None:
    """Clear all tracks (e.g., after a disengagement)."""
    self._tracks.clear()
    self._next_uid = 1

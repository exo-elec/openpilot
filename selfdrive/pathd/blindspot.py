from __future__ import annotations

import math
from dataclasses import dataclass
from collections.abc import Iterable, Sequence


def _gap_thresholds(ego_velocity: float) -> tuple[float, float]:
  """Rear/front safety gaps as a function of ego speed."""
  return max(10.0, ego_velocity * 1.5), max(15.0, ego_velocity * 2.0)


@dataclass
class BlindspotCheckResult:
  safe: bool
  min_gap: float
  objects_considered: int


def check_lane_change_safety(tracked_objects: Iterable,
                             predicted_objects: Sequence | None,
                             direction: str,
                             ego_velocity: float) -> BlindspotCheckResult:
  """
  Shared blind-spot safety check used by convoy and NOO.

  Args:
    tracked_objects: live track list from internal tracker.
    predicted_objects: optional predicted trajectory list.
    direction: 'left' or 'right'.
    ego_velocity: current car speed (m/s).
  """
  min_rear_gap, min_front_gap = _gap_thresholds(ego_velocity)
  if direction == 'left':
    target_lateral_min, target_lateral_max = 2.5, 4.0
    side_cameras = {'leftFront', 'leftRear'}
  else:
    target_lateral_min, target_lateral_max = -4.0, -2.5
    side_cameras = {'rightFront', 'rightRear'}

  min_gap = float('inf')
  considered = 0
  predictions = {getattr(p, 'trackId', None): p for p in (predicted_objects or [])}

  for obj in tracked_objects or []:
    try:
      prob = float(obj.prob)
      d_rel = float(obj.dRel)
      y_rel = float(obj.yRel)
    except (AttributeError, ValueError, TypeError):
      continue
    if prob < 0.5 or not (math.isfinite(d_rel) and math.isfinite(y_rel)):
      continue

    sources = getattr(obj, 'cameraSources', []) or []
    has_side = any(src in side_cameras for src in sources)
    has_stereo = 'stereo' in sources
    if d_rel <= 5.0 and not has_side:
      continue
    if d_rel > 5.0 and not (has_side or has_stereo):
      continue

    in_lane_now = target_lateral_min <= y_rel <= target_lateral_max
    in_lane_future = False
    pred = predictions.get(getattr(obj, 'trackId', None))
    if pred and getattr(pred, 'y', None):
      try:
        for t_val, y_val in zip(pred.t, pred.y, strict=False):
          if not math.isfinite(y_val) or not math.isfinite(t_val):
            continue
          if t_val > 1.5:
            break
          if target_lateral_min <= y_val <= target_lateral_max:
            in_lane_future = True
            break
      except Exception:
        pass

    if not (in_lane_now or in_lane_future):
      continue

    considered += 1

    if d_rel >= 0:
      gap = d_rel
      blocking = gap < min_front_gap
    else:
      gap = abs(d_rel)
      blocking = gap < min_rear_gap

    min_gap = min(min_gap, gap)
    if blocking:
      return BlindspotCheckResult(False, min_gap, considered)

  final_gap = min_gap if min_gap != float('inf') else 100.0
  return BlindspotCheckResult(True, final_gap, considered)


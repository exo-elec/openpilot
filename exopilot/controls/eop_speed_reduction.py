"""Trajectory-intersection speed reduction for objects in our path (EOP10's `compute_speed_reduction`, made shared).

Moved from EOP10's pathd with its constants and numbers unchanged, plus two things the move needed:
  - `max_depth_m` is a parameter (EOP10: `HARDWARE.get_max_reliable_depth_m()`), and the BEV-grid hit is passed in as a
    precomputed `grid_hit = (distance, lateral, score)` instead of an `OccupancyGridView` object;
  - **the distance-scale lookup is corrected**. EOP10's table is "highest ratio first" with lower bounds
    (> 1.0: none, > 0.6: 0.3, > 0.3: 0.6, > 0.1: 1.0, > 0.0: 1.5) but the loop tests `ratio <= threshold`, so the first
    entry (`inf`) always matches and the scale is always 0: **the reduction never applied** (found by running it,
    2026-10-04). `legacy_scale_bug=True` reproduces the old always-zero behaviour exactly (the golden test pins it) so
    a branch can adopt the fix deliberately.

Frames: object `yRel` is left positive, `dRel` forward, `vRel` relative (negative = closing). The corridor is the fixed
`PATH_WIDTH` around the car's centre line (EOP10's original; `ngp_cutin_speed` is the path-relative, direction-aware one).
Pure: no cereal, no Params. Returns inf for "no threat" and a negative m/s delta otherwise.
"""
import math

import numpy as np

PATH_WIDTH = 1.8
SAFE_TTC_THRESHOLD = 3.0
COLLISION_BUFFER = 0.5
MAX_SPEED_REDUCTION = 5.0
# (lower bound of the distance ratio, scale): highest ratio first
DISTANCE_SCALE_THRESHOLDS = [(float('inf'), 0.0), (1.0, 0.0), (0.6, 0.3), (0.3, 0.6), (0.1, 1.0), (0.0, 1.5)]
VELOCITY_SCALE_BP = [0.0, 12.0, 24.0, 36.0]
VELOCITY_SCALE_V = [0.4, 0.7, 1.0, 1.2]


def _scale(distance_ratio: float, legacy_scale_bug: bool) -> float:
  for threshold, scale_factor in DISTANCE_SCALE_THRESHOLDS:
    if (distance_ratio <= threshold) if legacy_scale_bug else (distance_ratio > threshold):
      return scale_factor
  return 0.0


def compute_speed_reduction(tracked_objects, predicted_objects, ego_velocity, max_depth_m: float, grid_hit=None,
                            legacy_scale_bug: bool = False) -> float:
  tracked_objects = tracked_objects or []
  predicted_objects = predicted_objects or []
  if not math.isfinite(ego_velocity):
    ego_velocity = 0.0

  predictions_by_id = {obj.trackId: obj for obj in predicted_objects} if predicted_objects else {}
  threats = []

  for obj in tracked_objects:
    if obj.prob < 0.5:
      if not (hasattr(obj, 'occluded') and obj.occluded and obj.prob > 0.3):
        continue
    if obj.dRel <= 0:
      continue
    if not (math.isfinite(obj.dRel) and math.isfinite(obj.yRel) and math.isfinite(obj.vRel)):
      continue

    current_lateral = obj.yRel
    pred_obj = predictions_by_id.get(obj.trackId)
    future_lateral = current_lateral
    if pred_obj and getattr(pred_obj, 'y', None):
      for t_val, y_val in zip(pred_obj.t, pred_obj.y, strict=False):
        if t_val >= 0.0 and t_val <= SAFE_TTC_THRESHOLD and math.isfinite(y_val):
          future_lateral = y_val
          break

    in_path_now = abs(current_lateral) <= PATH_WIDTH
    in_path_future = abs(future_lateral) <= PATH_WIDTH

    is_from_side_camera = False
    is_from_stereo = False
    if hasattr(obj, 'cameraSources') and obj.cameraSources:
      side_cameras = ['leftFront', 'leftRear', 'rightFront', 'rightRear']
      is_from_side_camera = any(cam in obj.cameraSources for cam in side_cameras)
      is_from_stereo = 'stereo' in obj.cameraSources or 'modelV2' in obj.cameraSources
      if is_from_side_camera and not is_from_stereo:
        if not (in_path_now or in_path_future):
          continue

    if not (in_path_now or in_path_future):
      continue

    closing_speed = -obj.vRel
    if pred_obj and getattr(pred_obj, 'v', None):
      closing_speed = 0.0
      for t_val, v_val in zip(pred_obj.t, pred_obj.v, strict=False):
        if t_val >= 0.0:
          closing_speed = max(closing_speed, -v_val)
          break
      if closing_speed <= 0.0:
        closing_speed = -obj.vRel
    if closing_speed < 0.5:
      continue

    predicted_ttc = None
    if pred_obj and getattr(pred_obj, 'x', None):
      for t_val, x_val in zip(pred_obj.t, pred_obj.x, strict=False):
        if not math.isfinite(t_val) or not math.isfinite(x_val):
          continue
        if t_val < 0.0:
          continue
        if x_val <= COLLISION_BUFFER:
          predicted_ttc = t_val
          break

    depth_confidence = 1.0
    if hasattr(obj, 'cameraSources') and obj.cameraSources:
      if 'stereo' in obj.cameraSources:
        depth_confidence = 1.2
        if hasattr(obj, 'depthSource'):
          try:
            if obj.depthSource == 1:
              depth_confidence = 1.5
          except (AttributeError, TypeError):
            pass
    if hasattr(obj, 'occluded') and obj.occluded:
      depth_confidence *= 1.1

    threats.append({'distance': obj.dRel, 'closing_speed': closing_speed, 'lateral': current_lateral, 'future_lateral': future_lateral,
                    'prob': obj.prob * depth_confidence, 'predicted_ttc': predicted_ttc, 'occluded': hasattr(obj, 'occluded') and obj.occluded})

  if grid_hit is not None:
    dist, lateral, score = grid_hit
    closing_speed = max(ego_velocity, 0.1) + 1.0
    threats.append({'distance': max(0.1, dist), 'closing_speed': closing_speed, 'lateral': lateral, 'future_lateral': lateral,
                    'prob': score, 'predicted_ttc': dist / max(closing_speed, 0.1), 'occluded': False})

  if not threats:
    return float('inf')

  most_critical = None
  min_ttc = float('inf')
  for threat in threats:
    ttc = threat['distance'] / max(threat['closing_speed'], 0.1)
    if threat['predicted_ttc'] is not None:
      ttc = min(ttc, threat['predicted_ttc'])
    if ttc < min_ttc:
      min_ttc = ttc
      most_critical = threat
  if most_critical is None:
    return float('inf')

  distance = most_critical['distance']
  closing_speed = most_critical['closing_speed']
  scale = _scale(distance / max_depth_m, legacy_scale_bug)

  speed_reduction = -closing_speed * scale
  speed_reduction *= float(np.interp(ego_velocity, VELOCITY_SCALE_BP, VELOCITY_SCALE_V))
  speed_reduction = max(speed_reduction, -MAX_SPEED_REDUCTION)
  if ego_velocity > 1.0:
    speed_reduction = max(speed_reduction, -ego_velocity * 0.5)
  if not math.isfinite(speed_reduction):
    return 0.0
  return speed_reduction

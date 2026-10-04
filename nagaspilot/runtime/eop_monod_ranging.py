"""EOP10 / 01M / 02M monod: replace the class-height-prior range of road-camera boxes with NGP10's ground-plane ranging.

EOP10's `objects_to_road_frame` ranges a box as `class_height * focal / box_height`, with no calibration and no
anchor: a wrong class height or a clipped box gives a wrong range. This adapter ranges the box BOTTOM on the road
plane (horizon and vanishing column from `liveCalibration`, camera height from the calibration), falls back to
the class height only for clipped/at-horizon boxes, and learns a per-class scale from real radar leads. It is
behind `ngp_monod_ranger` (default off) until `python3 -m nagaspilot.tools.validate_ranging` passes on an EOP10
stereo route. Only the narrow road lens is handled (pinhole); the 1.7 mm wide fisheye keeps the prior.
Pure apart from the lazy transformation import in `set_calibration`.
"""
import math

import numpy as np

from nagaspilot.controls.ngp_ranging import LeadAnchoredRanger, RoadCamera
from nagaspilot.runtime.monod import vanishing_point

KNOWN = {'car', 'truck', 'bus', 'motorcycle', 'bicycle', 'person'}


class GroundRanging:
  def __init__(self, focal_px: float, frame_hw: tuple[int, int], min_range_m: float, max_range_m: float):
    h, w = frame_hw
    self.frame_hw = frame_hw
    self.cam = RoadCamera(focal=float(focal_px), cx=w / 2.0, cy=h / 2.0)
    self.ranger = LeadAnchoredRanger(self.cam)
    self.K = np.array([[focal_px, 0.0, w / 2.0], [0.0, focal_px, h / 2.0], [0.0, 0.0, 1.0]])
    self.view_from_calib: np.ndarray | None = None
    self.min_range_m, self.max_range_m = min_range_m, max_range_m

  def set_calibration(self, rpy, height=None, view_from_calib_fn=None) -> None:
    if view_from_calib_fn is None:
      from openpilot.common.transformations.camera import get_view_frame_from_calib_frame

      def view_from_calib_fn(r, p, y):
        return get_view_frame_from_calib_frame(r, p, y, 0.0)[:, :3]
    rpy = [float(v) for v in rpy]
    if len(rpy) == 3 and all(math.isfinite(v) for v in rpy):
      self.view_from_calib = np.asarray(view_from_calib_fn(*rpy), dtype=float)
    if height is not None and math.isfinite(float(height)) and float(height) > 0.5:
      self.ranger.cam = RoadCamera(self.cam.focal, self.cam.cx, self.cam.cy, float(height))

  def apply(self, road_dets: list[dict], radar_leads: list[tuple[float, float]] | None = None) -> int:
    """Re-range the dicts in place (`distance_m`, `lateral_m`); keep the prior in `prior_distance_m`. Returns how many changed."""
    if self.view_from_calib is None:
      return 0
    fh, fw = self.frame_hw
    vp_u, horizon_v = vanishing_point(self.view_from_calib, self.K)
    ranged = []
    for d in road_dets:
      name, box = d.get('class'), d.get('bbox')
      if name not in KNOWN or box is None:
        continue
      x1, y1, x2, y2 = (float(v) for v in box)
      est = self.ranger.range_box(name, 0.5 * (x1 + x2), y2, y2 - y1, horizon_v, vp_u, clipped_bottom=y2 >= fh - 2)
      if est is None:
        continue
      ranged.append((d, name, est))
    # learn the scale from independent leads on this frame, then apply it to the same frame
    if radar_leads:
      self.ranger.observe_leads([(n, e) for _, n, e in ranged], [(dd, y) for dd, y in radar_leads])
    changed = 0
    for d, name, est in ranged:
      k = self.ranger.scale(name)
      x = est.raw_x * k
      y = est.y * (x / est.x) if est.x else est.y
      if not self.min_range_m <= x <= self.max_range_m or not (math.isfinite(x) and math.isfinite(y)):
        continue
      d['prior_distance_m'] = d.get('distance_m')
      d['distance_m'], d['lateral_m'], d['range_source'] = float(x), float(y), est.source
      changed += 1
    return changed


def radar_leads(sm) -> list[tuple[float, float]]:
  """Independent anchors: leads measured by a real radar (`.radar`), never vision leads."""
  if not sm.valid.get('radarState', False):
    return []
  rs = sm['radarState']
  return [(float(ld.dRel), float(ld.yRel)) for ld in (rs.leadOne, rs.leadTwo) if ld.status and getattr(ld, 'radar', False)]

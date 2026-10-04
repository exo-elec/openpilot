"""Range for detector boxes: flat-ground geometry, anchored on openpilot's own leads.

The road camera is the same 8 mm sensor on comma 3/3X and on our devices
(`common/transformations/camera.py`: 1928x1208, f = 2648 px), so its pinhole
model is known. A box's bottom edge is a point on the road; its row below the
horizon gives the range: x = camera_height * f / (v_bottom - horizon_v). Boxes
clipped at the image bottom or too close to the horizon fall back to the class
height prior. The geometry is then corrected by a per-class scale learned
from the boxes that match a `radarState` lead (bearing + range gate), and
applied to boxes openpilot dropped. The wide camera (1.7 mm, fisheye) is NOT
handled: it needs a distortion model, not this pinhole.

Frames: input leads and output `y` are the car frame, left positive (`yRel`).
Callers holding `modelV2` leads (y-right) flip once before calling.
Pure: no cereal, no Params.
"""
import math
from dataclasses import dataclass

CLASS_HEIGHT_M: dict[str, float] = {
  'person': 1.7, 'bicycle': 1.0, 'motorcycle': 1.2, 'car': 1.5, 'bus': 2.8, 'truck': 2.5,
}

MIN_PIXELS_BELOW_HORIZON = 4.0
MAX_RANGE_M = 150.0
BEARING_GATE_RAD = 0.04
RANGE_RATIO_GATE = (0.5, 2.0)
SCALE_LIMITS = (0.5, 2.0)
SCALE_ALPHA = 0.1
MIN_CLASS_OBS = 3


@dataclass(frozen=True)
class RoadCamera:
  focal: float = 2648.0
  cx: float = 964.0
  cy: float = 604.0
  height_m: float = 1.22   # lens above the road; tune per vehicle


@dataclass(frozen=True)
class Ranged:
  x: float           # forward, m
  y: float           # lateral, m, left positive
  source: str        # 'ground' | 'height'
  raw_x: float       # before the lead-anchored scale


class LeadAnchoredRanger:
  def __init__(self, cam: RoadCamera | None = None):
    self.cam = cam or RoadCamera()
    self.k_all = 1.0
    self.k_cls: dict[str, float] = {}
    self.n_cls: dict[str, int] = {}

  def scale(self, name: str) -> float:
    if self.n_cls.get(name, 0) >= MIN_CLASS_OBS:
      return self.k_cls[name]
    return self.k_all

  def range_box(self, name: str, cx_px: float, bottom_px: float, height_px: float,
                horizon_v: float, vanish_u: float, clipped_bottom: bool = False) -> Ranged | None:
    """Range one box. `horizon_v` / `vanish_u` come from liveCalibration (the adapter projects them)."""
    cam = self.cam
    below = bottom_px - horizon_v
    if not clipped_bottom and below >= MIN_PIXELS_BELOW_HORIZON:
      raw, source = cam.height_m * cam.focal / below, 'ground'
    elif name in CLASS_HEIGHT_M and height_px > MIN_PIXELS_BELOW_HORIZON:
      raw, source = CLASS_HEIGHT_M[name] * cam.focal / height_px, 'height'
    else:
      return None
    if not math.isfinite(raw) or raw <= 0 or raw * self.scale(name) > MAX_RANGE_M:
      return None
    x = raw * self.scale(name)
    y_left = -(cx_px - vanish_u) * x / cam.focal
    return Ranged(x, y_left, source, raw)

  def observe_leads(self, boxes: list[tuple[str, Ranged]], leads: list[tuple[float, float]]) -> int:
    """Update scales from boxes that match a lead. `leads` = [(dRel, yRel_left)]. Returns matches."""
    pairs = []
    for bi, (_, r) in enumerate(boxes):
      if r.source != 'ground':
        continue  # only the ground-plane geometry is corrected by a lead range
      for li, (d, y) in enumerate(leads):
        if d <= 1.0:
          continue
        bearing_err = abs(math.atan2(r.y, r.x) - math.atan2(y, d))
        ratio = d / r.raw_x
        if bearing_err <= BEARING_GATE_RAD and RANGE_RATIO_GATE[0] <= ratio <= RANGE_RATIO_GATE[1]:
          pairs.append((bearing_err, bi, li, ratio))
    used_b: set[int] = set()
    used_l: set[int] = set()
    n = 0
    for _, bi, li, ratio in sorted(pairs):
      if bi in used_b or li in used_l:
        continue
      used_b.add(bi)
      used_l.add(li)
      name = boxes[bi][0]
      ratio = min(max(ratio, SCALE_LIMITS[0]), SCALE_LIMITS[1])
      self.k_all = _ewma(self.k_all, ratio)
      self.k_cls[name] = _ewma(self.k_cls.get(name, self.k_all), ratio)
      self.n_cls[name] = self.n_cls.get(name, 0) + 1
      n += 1
    return n


def _ewma(old: float, new: float) -> float:
  v = (1 - SCALE_ALPHA) * old + SCALE_ALPHA * new
  return min(max(v, SCALE_LIMITS[0]), SCALE_LIMITS[1])

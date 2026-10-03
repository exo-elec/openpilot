"""
ObjectDrivableAreaFilter: an advisory check that a detected object is where a
vehicle can be.

After Autoware's detected_object_validation (object_lanelet_filter and
occupancy_grid_based_validator), with gridd's fused drivable layer standing in
for the lanelet map. For each object: the mean drivable probability under its
footprint; if fresh cells confidently say "not road" (a guardrail, a wall, a
car parked on the verge, a person on the pavement) its confidence is scaled
down. That is all:

- Never deletes an object, only lowers its confidence.
- Unknown or decayed cells (probability near 0.5), a stale layer, or an object
  outside the grid mean no filtering.
- Advisory only. Not used by AEB, which is fed by the built-in radar alone
  (radar_zones' other alerts keep their own confidence gates).

Frame: x forward, y LEFT (every yRel). The layer is gridObjects' "drivable"
layer (uint8 probability x 255), rows forward from originY, cols left positive
from originX -- read from the message, like pathd's OccupancyGridView.
"""
from __future__ import annotations

import math

import numpy as np

MEAN_THRESHOLD = 0.3      # mean drivable probability below this = not road
KNOWN_MARGIN = 0.15       # |p - 0.5| at least this: a cell with evidence
MIN_KNOWN_FRACTION = 0.6  # of the footprint must have evidence
CONFIDENCE_FACTOR = 0.5   # scale for an object judged off the road
MAX_AGE_S = 0.5           # older layer: no filtering

# (length, width) m by camera class; unknown objects are treated as a car
FOOTPRINT_M: dict[str, tuple[float, float]] = {
  'person': (0.5, 0.5), 'bicycle': (1.7, 0.5), 'motorcycle': (2.0, 0.7),
  'car': (4.5, 1.8), 'van': (5.0, 2.0), 'bus': (12.0, 2.5), 'truck': (10.0, 2.5),
}
DEFAULT_FOOTPRINT_M = FOOTPRINT_M['car']


class ObjectDrivableAreaFilter:
  def __init__(self) -> None:
    self._p: np.ndarray | None = None
    self._resolution = 0.5
    self._origin_forward = 0.0
    self._origin_left = 0.0
    self._t = -1e9

  def update_grid(self, grid_msg, t: float) -> None:
    """Take the drivable layer from a gridObjects message (none: no filtering)."""
    self._p = None
    width, height = int(grid_msg.width), int(grid_msg.height)
    for layer in grid_msg.layers:
      if layer.name == 'drivable' and len(layer.data) == width * height > 0:
        scale = layer.scale if layer.scale != 0.0 else 1.0 / 255.0
        self._p = np.frombuffer(layer.data, dtype=np.uint8).reshape(height, width).astype(np.float32) * scale
        self._resolution = float(grid_msg.resolution) or 0.5
        self._origin_forward = float(grid_msg.originY)
        self._origin_left = float(grid_msg.originX)
        self._t = t
        return

  def _footprint(self, obj: dict) -> tuple[float, float]:
    length, width = obj.get('length', 0.0), obj.get('width', 0.0)
    if 0.1 < length < 20.0 and 0.1 < width < 5.0:
      return float(length), float(width)
    return FOOTPRINT_M.get(str(obj.get('className', '')).lower(), DEFAULT_FOOTPRINT_M)

  def off_road(self, obj: dict) -> bool:
    """Whether fresh, known cells under the object say it is not on the road."""
    if self._p is None:
      return False
    d, y = obj.get('dRel'), obj.get('yRel')
    if d is None or y is None or not (math.isfinite(d) and math.isfinite(y)):
      return False
    length, width = self._footprint(obj)
    res = self._resolution
    r0 = int(math.floor((d - length / 2.0 - self._origin_forward) / res))
    r1 = int(math.floor((d + length / 2.0 - self._origin_forward) / res))
    c0 = int(math.floor((y - width / 2.0 - self._origin_left) / res))
    c1 = int(math.floor((y + width / 2.0 - self._origin_left) / res))
    rows, cols = self._p.shape
    if r1 < 0 or c1 < 0 or r0 >= rows or c0 >= cols:
      return False                                   # outside the grid
    block = self._p[max(r0, 0):min(r1, rows - 1) + 1, max(c0, 0):min(c1, cols - 1) + 1]
    cells_in_footprint = (r1 - r0 + 1) * (c1 - c0 + 1)
    known = block[np.abs(block - 0.5) >= KNOWN_MARGIN]
    if known.size < MIN_KNOWN_FRACTION * cells_in_footprint:
      return False                                   # not enough evidence
    return float(known.mean()) < MEAN_THRESHOLD

  def apply(self, objects: list[dict], t: float) -> list[dict]:
    """The same objects, an off-road one with its confidence scaled down."""
    if self._p is None or t - self._t > MAX_AGE_S:
      return objects
    for obj in objects:
      if self.off_road(obj):
        for key in ('confidence', 'prob'):
          if key in obj:
            obj[key] = float(obj[key]) * CONFIDENCE_FACTOR
        obj['offRoad'] = True
    return objects

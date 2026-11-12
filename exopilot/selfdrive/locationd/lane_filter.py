"""Which lane the car is in, and where it sits in it, from what the camera sees and what the map says.

Camera model inputs (per update): the distance to the lane line on each side of the ego lane, and to the road edge on each side, each with a visibility flag; a completed lane
change when the planner reports one. Map inputs: the number of lanes (from `lanes=` in OSM, when mapped). The state is a belief over lane indices, counted from the carriageway's
left edge (0 is the leftmost lane): a small Bayes filter.

- the lateral offset from the lane centre and the lane width come straight from the two lane lines;
- road edges say how many lanes lie beyond the ego lane on each side (distance to the edge minus the distance to the line, divided by the lane width);
- a completed lane change moves the belief one lane;
- with no information the belief spreads out and the filter says 'unknown' rather than guess.

Advisory only: nothing here feeds the controls. Standard library only.
"""
from __future__ import annotations

import math
from dataclasses import dataclass

MIN_LANE_W, MAX_LANE_W = 2.2, 5.0       # metres: a lane width outside this is not a lane
SIGMA_LANES = 0.6                       # how sharp the edge-based lane count is, in lanes
LEAK = 0.04                             # belief that leaks to a uniform spread each update (the world changes under us)
REPORT_AT = 0.7                         # confidence needed to name a lane
WIDTH_ALPHA = 0.1                       # smoothing of the lane width


@dataclass(frozen=True)
class Observation:
  left_line_m: float | None = None      # distance from the car's centre to the left line of the ego lane
  right_line_m: float | None = None
  left_edge_m: float | None = None      # distance to the left road edge (beyond the lines)
  right_edge_m: float | None = None
  lane_change: int = 0                  # +1 completed a change to the left, -1 to the right, 0 none
  lane_count: int | None = None         # from the map, when mapped


@dataclass(frozen=True)
class Estimate:
  lane_index: int | None                # None = unknown
  lane_count: int | None
  offset_m: float | None                # positive: right of the lane centre
  lane_width_m: float | None
  confidence: float


class LaneFilter:
  def __init__(self):
    self._belief: list[float] = []
    self._width: float | None = None
    self._offset: float | None = None

  def _resize(self, n: int) -> None:
    if len(self._belief) != n:
      self._belief = [1.0 / n] * n

  def update(self, o: Observation) -> Estimate:
    # lane width and offset from the two lines
    if o.left_line_m is not None and o.right_line_m is not None:
      w = o.left_line_m + o.right_line_m
      if MIN_LANE_W <= w <= MAX_LANE_W:
        self._width = w if self._width is None else self._width + WIDTH_ALPHA * (w - self._width)
        self._offset = (o.left_line_m - o.right_line_m) / 2.0     # nearer the right line -> right of centre
      else:
        self._offset = None
    else:
      self._offset = None

    n = o.lane_count
    if n is None and self._width and o.left_edge_m is not None and o.right_edge_m is not None and o.left_line_m is not None and o.right_line_m is not None:
      n = max(1, round(self._belief_width_lanes(o)))      # unmapped: the camera's own count of the carriageway
    if n is None or n < 1:
      return Estimate(None, None, self._offset, self._width, 0.0)
    self._resize(n)

    # a completed lane change moves the belief; the edges stop at the carriageway
    if o.lane_change:
      shifted = [0.0] * n
      for i, b in enumerate(self._belief):
        shifted[min(max(i - o.lane_change, 0), n - 1)] += b
      self._belief = shifted

    # the edges say how many lanes are beyond the ego lane on each side
    w = self._width
    if w and o.left_edge_m is not None and o.left_line_m is not None:
      est_left = (o.left_edge_m - o.left_line_m) / w                     # lanes to the left of the ego lane
      self._multiply(lambda i: math.exp(-0.5 * ((i - est_left) / SIGMA_LANES) ** 2))
    if w and o.right_edge_m is not None and o.right_line_m is not None:
      est_right = (o.right_edge_m - o.right_line_m) / w                  # lanes to the right
      self._multiply(lambda i: math.exp(-0.5 * (((n - 1 - i) - est_right) / SIGMA_LANES) ** 2))

    # leak a little toward uniform so a wrong belief can recover
    self._belief = [(1 - LEAK) * b + LEAK / n for b in self._belief]
    total = sum(self._belief)
    self._belief = [b / total for b in self._belief]
    best = max(range(n), key=lambda i: self._belief[i])
    conf = self._belief[best]
    return Estimate(best if conf >= REPORT_AT else None, n, self._offset, self._width, conf)

  def _belief_width_lanes(self, o: Observation) -> float:
    carriageway = o.left_edge_m + o.right_edge_m       # edge to edge across the car
    return carriageway / self._width

  def _multiply(self, likelihood) -> None:
    mixed = [b * likelihood(i) for i, b in enumerate(self._belief)]
    total = sum(mixed)
    if total > 1e-12:
      self._belief = [m / total for m in mixed]

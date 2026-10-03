#!/usr/bin/env python3
"""
FusionCostmap: gridd's cost layer, published as gridObjects layer "cost".

Layers combined by max, after Autoware's costmap_generator:
  - drivable: card segmentation / surfaced, never above COST_UNKNOWN, so
    segmentation alone never reaches pathd's obstacle thresholds;
  - obstacles: stereo occupancy and measured radar footprints, COST_OBSTACLE.

Encoding: gridObjects `cost` = uint16 (log.capnp OccupancyGridEncoding),
values below; pathd scores cost / COST_OBSTACLE. Layout: BEV_GRID, rows
forward, cols left positive (CLAUDE.md "Frame conventions").

CPU numpy: a few thousand stereo points and a handful of radar boxes per
frame. The GPU `occupancy_grid` op this used to call never existed in
inferenced, so every call raised.
"""
from __future__ import annotations

import numpy as np

from openpilot.selfdrive.gridd.lazy_bev import BEV_GRID, BEVGridSpec

COST_LANE = 0         # lane marking on the road
COST_ROAD = 10        # confident drivable road
COST_UNCERTAIN = 25   # weak drivable evidence / rough surface
COST_UNKNOWN = 50     # no evidence
COST_OBSTACLE = 100   # stereo or radar obstacle


class FusionCostmap:
  """Per-frame cost layer on BEV_GRID."""

  def __init__(self, spec: BEVGridSpec = BEV_GRID):
    self.spec = spec
    self.costmap = np.full((spec.rows, spec.cols), COST_UNKNOWN, dtype=np.uint16)

  def reset(self) -> None:
    """Start a frame: everything unknown."""
    self.costmap.fill(COST_UNKNOWN)

  def apply_drivable(self, cost: np.ndarray) -> None:
    """Drivable-layer cost (same shape), known cells only; capped at COST_UNKNOWN."""
    known = cost < COST_UNKNOWN
    self.costmap[known] = np.minimum(self.costmap[known], cost[known])

  def mark_occupied(self, mask: np.ndarray) -> None:
    """Cells an occupancy source calls occupied (same shape bool mask)."""
    self.costmap[mask] = COST_OBSTACLE

  def add_obstacle(self, forward: float, left: float, length: float, width: float,
                   cost: int = COST_OBSTACLE) -> None:
    """Axis-aligned footprint centred at (forward, left) metres, left positive."""
    res = self.spec.resolution
    r0, c0, _ = self.spec.cells(forward - length / 2.0, left - width / 2.0)
    r1, c1, _ = self.spec.cells(forward + length / 2.0, left + width / 2.0)
    r0, r1 = max(int(r0), 0), min(int(r1), self.spec.rows - 1)
    c0, c1 = max(int(c0), 0), min(int(c1), self.spec.cols - 1)
    if r0 > r1 or c0 > c1 or res <= 0:
      return
    block = self.costmap[r0:r1 + 1, c0:c1 + 1]
    np.maximum(block, cost, out=block)

  def get_cost_at(self, forward: float, left: float) -> int:
    row, col, inside = self.spec.cells(forward, left)
    return int(self.costmap[int(row), int(col)]) if inside else COST_UNKNOWN

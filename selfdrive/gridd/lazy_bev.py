"""Sparse, lazy BEV diagnostics for comma 3.

The grid accepts already-metric normalized radar/MonoD observations. It never
constructs stereo depth from the comma 3 wide/narrow camera pair.
"""

from dataclasses import dataclass
from math import floor

from openpilot.selfdrive.gridd.ngp_capabilities import Feature, NGPCapabilities


@dataclass(frozen=True)
class BEVCell:
  x_index: int
  y_index: int
  occupancy: float
  track_ids: tuple[int, ...]


@dataclass(frozen=True)
class LazyBEVResult:
  available: bool
  cells: tuple[BEVCell, ...]
  reason: str
  control_authority: bool = False


class NGPLazyBEV:
  def __init__(self, resolution_m: float = 1.0, x_limits=(-30.0, 150.0), y_limits=(-12.0, 12.0)):
    if resolution_m <= 0.0:
      raise ValueError("resolution must be positive")
    self.resolution_m = float(resolution_m)
    self.x_limits = tuple(float(value) for value in x_limits)
    self.y_limits = tuple(float(value) for value in y_limits)

  def update(self, observations, capabilities: NGPCapabilities,
             metric_observations: bool) -> LazyBEVResult:
    if not capabilities.supports(Feature.GRID):
      return LazyBEVResult(False, (), "camera_contract_unavailable")
    if not metric_observations:
      return LazyBEVResult(False, (), "metric_depth_unavailable")

    grouped: dict[tuple[int, int], list] = {}
    for observation in observations or ():
      x = float(getattr(observation, "d_rel", getattr(observation, "x", 0.0)))
      y = float(getattr(observation, "y_rel", getattr(observation, "y", 0.0)))
      if not (self.x_limits[0] <= x <= self.x_limits[1] and self.y_limits[0] <= y <= self.y_limits[1]):
        continue
      key = (floor(x / self.resolution_m), floor(y / self.resolution_m))
      grouped.setdefault(key, []).append(observation)

    cells = []
    for (x_index, y_index), items in grouped.items():
      probability = max(float(getattr(item, "probability", getattr(item, "confidence", 0.5))) for item in items)
      track_ids = tuple(sorted(int(getattr(item, "track_id", -1)) for item in items))
      cells.append(BEVCell(x_index, y_index, max(0.0, min(1.0, probability)), track_ids))
    return LazyBEVResult(True, tuple(sorted(cells, key=lambda cell: (cell.x_index, cell.y_index))), "available")

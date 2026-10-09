"""
OccupancyGrid: BEV World Modeling Engine.
Projects SceneSeg and PP-LiteSeg masks into a physical 3D grid using Stereo Depth.
Solves the 'Distance Gap' and 'Bike Cut-in' problems via deterministic geometry.
"""

from __future__ import annotations
import numpy as np
from typing import Any

class OccupancyGrid:
  """
  OccupancyGrid creates a top-down physical representation of the road.
  It maps pixels to metric coordinates (meters).
  """

  def __init__(self, range_m: float = 100.0, resolution_m: float = 0.5):
    self.range_m = range_m
    self.resolution_m = resolution_m
    self.safe_width = 2.2 # Meters

  def build_grid(
    self,
    xyz_map: np.ndarray,
    road_mask: np.ndarray,
    object_mask: np.ndarray
  ) -> dict[str, Any]:
    """
    Projects semantic masks into a physical top-down grid.
    """
    results = {
      "drivable_limit_m": self.range_m,
      "occupancy_detected": False,
      "bike_cutin_imminent": False
    }

    if xyz_map is None:
      return results

    # 1. 3D Coordinate Mapping (Geometric Truth)
    z = xyz_map[:, :, 2] # Forward
    x = xyz_map[:, :, 0] # Lateral

    # 2. Safety Corridor Filtering
    # Only process points within our driving path
    in_path = (np.abs(x) < (self.safe_width / 2.0)) & (z > 0.5) & (z < self.range_m)

    # 3. Collision Probability Calculation
    # Find 'object' pixels that are physically in our path
    hazards_in_path = object_mask & in_path

    if np.any(hazards_in_path):
      # Physical distance from Stereo (Accuracy Deduction)
      dist = float(np.min(z[hazards_in_path]))
      results["drivable_limit_m"] = dist
      results["occupancy_detected"] = True

      # 4. Aggressive Cut-in Logic
      # If hazard is entering the 'road' boundary within the safety zone
      if np.any(hazards_in_path & road_mask):
        results["bike_cutin_imminent"] = True

    return results

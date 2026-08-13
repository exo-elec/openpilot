#!/usr/bin/env python3
"""
Fusion costmap generation using centralized HAL.

All GPU compute goes through InferenceClient - no direct pyopencl access.
"""

import numpy as np
from dataclasses import dataclass
from typing import cast

# Centralized HAL - ONLY way to access GPU compute
from openpilot.system.inferenced import InferenceClient


@dataclass
class CostmapConfig:
  """Costmap configuration."""
  width: int = 200  # Cells
  height: int = 200
  resolution: float = 0.2  # Meters per cell
  origin_x: float = -20.0  # Meters
  origin_y: float = -20.0
  max_height: float = 2.0  # Max obstacle height
  min_height: float = -0.5  # Min ground height


class FusionCostmap:
  """Generates fused costmap from multiple sensor sources via HAL."""

  def __init__(self, config: CostmapConfig | None = None):
    """Initialize costmap generator via HAL.

    Args:
        config: Costmap configuration
    """
    self.config = config or CostmapConfig()

    # Create HAL client - ONLY way to access GPU
    self.client = InferenceClient("gridd")

    # Occupancy grid fusion is ASSIGNED to GPU. No CPU fallback.
    self.gpu = self.client.acl()

    # Initialize costmap
    self.costmap = np.zeros(
      (self.config.height, self.config.width),
      dtype=np.float32
    )
    self.heightmap = np.zeros(
      (self.config.height, self.config.width),
      dtype=np.float32
    )

    # Precompute world-to-grid transform
    self._init_transforms()

  def is_gpu_available(self) -> bool:
    """Return whether a GPU compute backend is available."""
    return self.gpu is not None

  def _init_transforms(self):
    """Precompute coordinate transforms."""
    # Grid to world
    self.grid_x = np.arange(self.config.width, dtype=np.float32)
    self.grid_y = np.arange(self.config.height, dtype=np.float32)
    self.grid_x, self.grid_y = np.meshgrid(self.grid_x, self.grid_y)

    self.world_x = self.config.origin_x + self.grid_x * self.config.resolution
    self.world_y = self.config.origin_y + self.grid_y * self.config.resolution

  def world_to_grid(self, x: float, y: float) -> tuple[int, int]:
    """Convert world coordinates to grid indices.

    Args:
        x, y: World coordinates in meters

    Returns:
        (grid_x, grid_y) indices
    """
    gx = int((x - self.config.origin_x) / self.config.resolution)
    gy = int((y - self.config.origin_y) / self.config.resolution)
    return gx, gy

  def grid_to_world(self, gx: int, gy: int) -> tuple[float, float]:
    """Convert grid indices to world coordinates.

    Args:
        gx, gy: Grid indices

    Returns:
        (x, y) world coordinates in meters
    """
    x = self.config.origin_x + gx * self.config.resolution
    y = self.config.origin_y + gy * self.config.resolution
    return x, y

  def fuse_pointcloud(self, points: np.ndarray,
                      intensities: np.ndarray | None = None) -> np.ndarray:
    """Fuse point cloud into costmap via GPU OpenCL.

    Args:
        points: Nx3 array of 3D points
        intensities: Optional N array of point intensities

    Returns:
        Updated costmap
    """
    return self._fuse_gpu(points, intensities)

  def _fuse_gpu(self, points: np.ndarray,
                intensities: np.ndarray | None) -> np.ndarray:
    """GPU point cloud fusion via HAL."""
    # Create labels: 1=occupied (all points are obstacles)
    labels = np.ones(len(points), dtype=np.uint8)

    result = self.gpu.infer(
      model_name='occupancy_grid',
      inputs={
        'xyz': points.astype(np.float32),
        'labels': labels,
        'grid_shape': (self.config.height, self.config.width),
        'grid_resolution': self.config.resolution,
        'grid_origin': (self.config.origin_x, self.config.origin_y),
      }
    )

    if not result.success:
      raise RuntimeError(f"GPU occupancy grid failed: {result.error_message}")

    self.costmap = result.outputs['grid']
    return cast(np.ndarray, self.costmap.copy())

  def fuse_depth(self, depth_map: np.ndarray,
                 camera_pose: np.ndarray,
                 intrinsics: np.ndarray) -> np.ndarray:
    """Fuse depth map into costmap.

    Args:
        depth_map: HxW depth image
        camera_pose: 4x4 camera-to-world transform
        intrinsics: 3x3 camera intrinsics

    Returns:
        Updated costmap
    """
    # First reproject depth to points
    points = self._depth_to_points(depth_map, camera_pose, intrinsics)

    # Then fuse
    return self.fuse_pointcloud(points)

  def _depth_to_points(self, depth: np.ndarray,
                       pose: np.ndarray,
                       K: np.ndarray) -> np.ndarray:
    """Convert depth map to 3D points."""
    h, w = depth.shape

    # Create pixel grid
    u = np.arange(w, dtype=np.float32)
    v = np.arange(h, dtype=np.float32)
    u, v = np.meshgrid(u, v)

    # Backproject
    z = depth
    x = (u - K[0, 2]) * z / K[0, 0]
    y = (v - K[1, 2]) * z / K[1, 1]

    # Stack to points
    points_cam = np.stack([x, y, z, np.ones_like(z)], axis=-1).reshape(-1, 4)

    # Transform to world
    points_world = (pose @ points_cam.T).T
    points_world = points_world[:, :3] / points_world[:, 3:4]

    # Filter valid depth
    valid = depth.flatten() > 0.1
    return cast(np.ndarray, points_world[valid])

  def add_obstacle(self, x: float, y: float, radius: float, cost: float = 1.0):
    """Add circular obstacle to costmap.

    Args:
        x, y: Center position in world coordinates
        radius: Radius in meters
        cost: Cost value (0-1)
    """
    cx, cy = self.world_to_grid(x, y)
    r_cells = int(radius / self.config.resolution)

    for dy in range(-r_cells, r_cells + 1):
      for dx in range(-r_cells, r_cells + 1):
        gx, gy = cx + dx, cy + dy
        if 0 <= gx < self.config.width and 0 <= gy < self.config.height:
          dist = np.sqrt(dx**2 + dy**2) * self.config.resolution
          if dist <= radius:
            self.costmap[gy, gx] = max(self.costmap[gy, gx], cost)

  def inflate_obstacles(self, inflation_radius: float = 0.5) -> np.ndarray:
    """Inflate obstacles for path planning safety.

    Args:
        inflation_radius: Inflation radius in meters

    Returns:
        Inflated costmap
    """
    from scipy.ndimage import maximum_filter

    r_cells = int(inflation_radius / self.config.resolution)
    inflated = maximum_filter(self.costmap, size=2*r_cells+1)

    return cast(np.ndarray, inflated)

  def clear(self):
    """Clear the costmap."""
    self.costmap.fill(0)
    self.heightmap.fill(0)

  def get_cost_at(self, x: float, y: float) -> float:
    """Get cost at world position.

    Args:
        x, y: World coordinates

    Returns:
        Cost value (0-1)
    """
    gx, gy = self.world_to_grid(x, y)
    if 0 <= gx < self.config.width and 0 <= gy < self.config.height:
      return cast(float, self.costmap[gy, gx])
    return 1.0  # Unknown is costly

  def release(self):
    """Release resources."""
    self.client.release()

  def __del__(self):
    self.release()


# Compatibility aliases for imports from gridd.py
FusionCostmapGenerator = FusionCostmap
FusionCostmapConfig = CostmapConfig

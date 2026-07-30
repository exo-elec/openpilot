#!/usr/bin/env python3
"""
Point cloud reprojection using ACL backend.

3D reprojection uses ACL (GPU preferred, CPU fallback) via InferenceClient.
"""

import numpy as np

from openpilot.common.swaglog import cloudlog

from openpilot.system.inferenced import InferenceClient, BackendType


class PointReprojector:
  """Reprojects depth maps to 3D point clouds using GPU OpenCL."""

  def __init__(self, width: int = 1920, height: int = 1080, 
               fx: float = 1000.0, fy: float = 1000.0,
               cx: float = 960.0, cy: float = 540.0):
    """Initialize reprojector via HAL.
    
    Args:
        width: Image width
        height: Image height
        fx, fy: Focal lengths
        cx, cy: Principal point
    """
    self.width = width
    self.height = height
    self.fx = fx
    self.fy = fy
    self.cx = cx
    self.cy = cy
    
    # Create HAL client - ONLY way to access GPU
    self.client = InferenceClient("pointcloudd")
    
    # 3D reprojection is ASSIGNED to GPU. No CPU fallback.
    self.gpu = self.client.acl()
    
    # Precompute pixel coordinates
    self._init_coordinates()

  def _init_coordinates(self):
    """Precompute coordinate grids."""
    u = np.arange(self.width, dtype=np.float32)
    v = np.arange(self.height, dtype=np.float32)
    self.u_grid, self.v_grid = np.meshgrid(u, v)
    
    # Precompute normalized coordinates
    self.x_norm = (self.u_grid - self.cx) / self.fx
    self.y_norm = (self.v_grid - self.cy) / self.fy

  def reproject(self, depth_map: np.ndarray, 
                mask: np.ndarray | None = None) -> np.ndarray:
    """Reproject depth map to 3D point cloud.
    
    Args:
        depth_map: HxW depth values in meters
        mask: Optional HxW mask of valid pixels
        
    Returns:
        Nx3 array of 3D points
    """
    if depth_map.shape != (self.height, self.width):
      raise ValueError(f"Depth map shape {depth_map.shape} doesn't match expected ({self.height}, {self.width})")
    
    # Ensure float32
    depth = depth_map.astype(np.float32)
    
    # Use GPU via HAL
    result = self.gpu.infer(
      model_name='reproject',
      inputs={
        'depth': depth,
        'x_norm': self.x_norm,
        'y_norm': self.y_norm,
      }
    )

    if not result.success:
      raise RuntimeError(f"GPU reprojection failed: {result.error_message}")

    xyz = result.outputs['xyz']
    
    # Filter invalid (zero) points from GPU output
    valid = xyz[:, :, 2] > 0.1
    
    # Apply mask if provided
    if mask is not None:
      valid = valid & mask
    
    points = xyz[valid].reshape(-1, 3)
    return points

  def reproject_with_colors(self, depth_map: np.ndarray,
                           image: np.ndarray,
                           mask: np.ndarray | None = None) -> tuple[np.ndarray, np.ndarray]:
    """Reproject with RGB colors.
    
    Args:
        depth_map: HxW depth values
        image: HxWx3 RGB image
        mask: Optional mask
        
    Returns:
        (Nx3 points, Nx3 colors)
    """
    points = self.reproject(depth_map, mask)
    
    # Sample colors at valid pixel locations
    if mask is not None:
      valid_indices = np.where(mask.flatten())[0]
    else:
      valid_indices = np.where(depth_map.flatten() > 0.1)[0]
    
    # Convert flat indices to 2D
    v_coords = valid_indices // self.width
    u_coords = valid_indices % self.width
    
    colors = image[v_coords, u_coords].astype(np.float32) / 255.0
    
    return points, colors

  def transform_points(self, points: np.ndarray, 
                       transform: np.ndarray) -> np.ndarray:
    """Apply 4x4 transformation matrix to points.
    
    Args:
        points: Nx3 array
        transform: 4x4 transformation matrix
        
    Returns:
        Nx3 transformed points
    """
    result = self.gpu.infer(
      model_name='transform_points',
      inputs={'points': points.astype(np.float32), 'transform': transform.astype(np.float32)}
    )
    if not result.success:
      raise RuntimeError(f"GPU transform failed: {result.error_message}")
    return result.outputs['points']

  def filter_by_distance(self, points: np.ndarray,
                         min_dist: float = 0.5,
                         max_dist: float = 100.0) -> np.ndarray:
    """Filter points by distance from origin."""
    distances = np.linalg.norm(points, axis=1)
    mask = (distances >= min_dist) & (distances <= max_dist)
    return points[mask]

  def downsample(self, points: np.ndarray, 
                 voxel_size: float = 0.1) -> np.ndarray:
    """Voxel grid downsampling."""
    voxel_indices = np.floor(points / voxel_size).astype(np.int32)
    unique_voxels, inverse = np.unique(voxel_indices, axis=0, return_inverse=True)
    downsampled = np.zeros((len(unique_voxels), 3), dtype=np.float32)
    counts = np.zeros(len(unique_voxels), dtype=np.int32)
    np.add.at(downsampled, inverse, points)
    np.add.at(counts, inverse, 1)
    downsampled /= counts[:, np.newaxis]
    return downsampled

  def release(self):
    """Release resources."""
    self.client.release()

  def __del__(self):
    self.release()

#!/usr/bin/env python3
"""
Adaptive Voxel Downsampling - Variable density based on semantics and geometry.

Dense for features (poles, curbs), sparse for flat areas (road).
"""

import numpy as np
from dataclasses import dataclass


@dataclass
class VoxelConfig:
    """Configuration for adaptive voxel sizes."""
    # Default voxel sizes by semantic class (meters)
    class_voxel_sizes: dict[str, float] = None
    
    # Distance-based adjustment
    near_range: tuple[float, float] = (0, 10)      # 0-10m: detailed
    mid_range: tuple[float, float] = (10, 50)      # 10-50m: medium
    far_range: tuple[float, float] = (50, 200)     # 50-200m: coarse
    
    # Multipliers for distance
    near_voxel_mult: float = 1.0    # Base size
    mid_voxel_mult: float = 2.0     # 2x larger
    far_voxel_mult: float = 4.0     # 4x larger
    
    # Geometry-based adjustment
    flat_mult: float = 2.0          # Larger voxels for flat areas
    edge_mult: float = 0.5          # Smaller voxels for edges
    
    def __post_init__(self):
        if self.class_voxel_sizes is None:
            # Default: importance weighting by semantic class
            self.class_voxel_sizes = {
                'road': 0.15,           # Flat, uniform - large voxels
                'sidewalk': 0.10,       # Medium importance
                'building': 0.05,       # Vertical features - small
                'wall': 0.05,           # Vertical
                'fence': 0.05,          # Thin features - small
                'pole': 0.02,           # Critical landmarks - very small
                'traffic_light': 0.02,  # Landmarks
                'traffic_sign': 0.02,   # Landmarks
                'terrain': 0.15,        # Similar to road
                'curb': 0.02,           # Critical for localization
            }


class AdaptiveVoxelFilter:
    """
    Adaptive voxel downsampling with variable density.
    
    Variable density: dense for features, sparse for flat areas.
    Considers:
    1. Semantic class (road vs pole)
    2. Distance from sensor (near vs far)
    3. Local geometry (flat vs edge)
    """
    
    def __init__(self, config: VoxelConfig | None = None):
        self.config = config or VoxelConfig()
        self.class_to_id = self._build_class_mapping()
    
    def _build_class_mapping(self) -> dict[str, int]:
        """Build class name to ID mapping."""
        return {
            'road': 0, 'sidewalk': 1, 'building': 2, 'wall': 3,
            'fence': 4, 'pole': 5, 'traffic_light': 6, 'traffic_sign': 7,
            'vegetation': 8, 'terrain': 9, 'sky': 10, 'person': 11,
            'rider': 12, 'car': 13, 'truck': 14, 'bus': 15,
            'train': 16, 'motorcycle': 17, 'bicycle': 18
        }
    
    def downsample(self,
                   points: np.ndarray,
                   semantic_labels: np.ndarray | None = None,
                   normals: np.ndarray | None = None) -> tuple[np.ndarray, np.ndarray]:
        """
        Adaptive voxel downsampling.
        
        Args:
            points: Nx3 array of XYZ coordinates
            semantic_labels: Optional N array of class indices
            normals: Optional Nx3 array of surface normals
        
        Returns:
            downsampled_points: Mx3 array (M <= N)
            downsampled_labels: M array of labels (if input provided)
        """
        if len(points) == 0:
            return points, semantic_labels if semantic_labels is not None else np.array([])
        
        # Calculate per-point voxel sizes
        voxel_sizes = self._calculate_voxel_sizes(points, semantic_labels, normals)
        
        # Assign points to adaptive voxels
        voxel_indices = np.floor(points / voxel_sizes[:, None]).astype(np.int64)
        
        # Find unique voxels
        # Use structured array for efficient unique operation
        voxel_struct = np.core.records.fromarrays(
            voxel_indices.T, 
            names='x,y,z',
            formats='i8,i8,i8'
        )
        
        unique_voxels, inverse_indices = np.unique(voxel_struct, return_inverse=True)
        
        # Average points within each voxel
        n_voxels = len(unique_voxels)
        downsampled_points = np.zeros((n_voxels, 3))
        
        if semantic_labels is not None:
            downsampled_labels = np.zeros(n_voxels, dtype=semantic_labels.dtype)
        else:
            downsampled_labels = None
        
        for i in range(n_voxels):
            mask = inverse_indices == i
            downsampled_points[i] = points[mask].mean(axis=0)
            
            if semantic_labels is not None:
                # Use most common label in voxel
                labels_in_voxel = semantic_labels[mask]
                downsampled_labels[i] = np.bincount(labels_in_voxel).argmax()
        
        return downsampled_points, downsampled_labels
    
    def _calculate_voxel_sizes(self,
                              points: np.ndarray,
                              semantic_labels: np.ndarray | None,
                              normals: np.ndarray | None) -> np.ndarray:
        """Calculate per-point voxel sizes."""
        n_points = len(points)
        voxel_sizes = np.ones(n_points) * 0.10  # Default 10cm
        
        # 1. Semantic-based voxel size
        if semantic_labels is not None:
            for i, label in enumerate(semantic_labels):
                class_name = self._get_class_name(label)
                base_size = self.config.class_voxel_sizes.get(class_name, 0.10)
                voxel_sizes[i] = base_size
        
        # 2. Distance-based adjustment
        distances = np.linalg.norm(points, axis=1)
        
        near_mask = (distances >= self.config.near_range[0]) & (distances < self.config.near_range[1])
        mid_mask = (distances >= self.config.mid_range[0]) & (distances < self.config.mid_range[1])
        far_mask = (distances >= self.config.far_range[0]) & (distances < self.config.far_range[1])
        
        voxel_sizes[near_mask] *= self.config.near_voxel_mult
        voxel_sizes[mid_mask] *= self.config.mid_voxel_mult
        voxel_sizes[far_mask] *= self.config.far_voxel_mult
        
        # 3. Geometry-based adjustment (if normals available)
        if normals is not None:
            # Flatness = how much normal points up (z-component)
            flatness = np.abs(normals[:, 2])
            
            is_flat = flatness > 0.9
            is_edge = flatness < 0.5
            
            voxel_sizes[is_flat] *= self.config.flat_mult
            voxel_sizes[is_edge] *= self.config.edge_mult
        
        # Clamp to reasonable range
        voxel_sizes = np.clip(voxel_sizes, 0.01, 0.50)  # 1cm to 50cm
        
        return voxel_sizes
    
    def _get_class_name(self, label_id: int) -> str:
        """Get class name from label ID."""
        id_to_class = {v: k for k, v in self.class_to_id.items()}
        return id_to_class.get(label_id, 'unknown')
    
    def estimate_memory_savings(self,
                               points: np.ndarray,
                               semantic_labels: np.ndarray | None = None) -> dict:
        """
        Estimate memory savings vs uniform voxel grid.
        
        Returns:
            stats: dict with compression statistics
        """
        n_original = len(points)
        
        # Adaptive downsampling
        downsampled, _ = self.downsample(points, semantic_labels)
        n_adaptive = len(downsampled)
        
        # Uniform downsampling (5cm)
        uniform_voxel = 0.05
        voxel_idx_uniform = np.floor(points / uniform_voxel).astype(np.int64)
        voxel_struct = np.core.records.fromarrays(
            voxel_idx_uniform.T, names='x,y,z', formats='i8,i8,i8'
        )
        n_uniform = len(np.unique(voxel_struct))
        
        return {
            'original_count': n_original,
            'adaptive_count': n_adaptive,
            'uniform_count': n_uniform,
            'adaptive_ratio': n_adaptive / n_original if n_original > 0 else 0,
            'uniform_ratio': n_uniform / n_original if n_original > 0 else 0,
            'memory_savings_vs_uniform': 1.0 - (n_adaptive / n_uniform) if n_uniform > 0 else 0,
            'estimated_mb_per_km': (n_adaptive * 12) / (1024 * 1024)  # 12 bytes per point
        }


class HierarchicalVoxelFilter:
    """
    Multi-resolution voxel filtering for different use cases.
    
    Creates multiple resolutions:
    - Fine: For detailed features (curbs, poles)
    - Medium: For structures (buildings)
    - Coarse: For terrain (road, ground)
    """
    
    def __init__(self):
        self.fine_filter = AdaptiveVoxelFilter(VoxelConfig(
            class_voxel_sizes={'default': 0.02}  # 2cm
        ))
        self.medium_filter = AdaptiveVoxelFilter(VoxelConfig(
            class_voxel_sizes={'default': 0.05}  # 5cm
        ))
        self.coarse_filter = AdaptiveVoxelFilter(VoxelConfig(
            class_voxel_sizes={'default': 0.15}  # 15cm
        ))
    
    def create_multi_resolution(self,
                               points: np.ndarray,
                               semantic_labels: np.ndarray) -> dict[str, np.ndarray]:
        """
        Create multi-resolution point clouds.
        
        Returns:
            {
                'fine': high_detail_points,      # Curbs, poles
                'medium': medium_detail_points,  # Buildings
                'coarse': low_detail_points      # Road, terrain
            }
        """
        # Separate by importance
        fine_classes = {5, 6, 7}  # pole, traffic_light, traffic_sign
        medium_classes = {2, 3, 4}  # building, wall, fence
        coarse_classes = {0, 1, 9}  # road, sidewalk, terrain
        
        fine_mask = np.isin(semantic_labels, list(fine_classes))
        medium_mask = np.isin(semantic_labels, list(medium_classes))
        coarse_mask = np.isin(semantic_labels, list(coarse_classes))
        
        result = {}
        
        if np.any(fine_mask):
            fine_points, _ = self.fine_filter.downsample(points[fine_mask])
            result['fine'] = fine_points
        
        if np.any(medium_mask):
            medium_points, _ = self.medium_filter.downsample(points[medium_mask])
            result['medium'] = medium_points
        
        if np.any(coarse_mask):
            coarse_points, _ = self.coarse_filter.downsample(points[coarse_mask])
            result['coarse'] = coarse_points
        
        return result


# Convenience functions

def adaptive_downsample(points: np.ndarray,
                       semantic_labels: np.ndarray | None = None,
                       normals: np.ndarray | None = None) -> np.ndarray:
    """
    Convenience function for adaptive voxel downsampling.
    
    Usage:
        downsampled = adaptive_downsample(points, labels)
        print(f"Reduced from {len(points)} to {len(downsampled)} points")
    """
    filter_obj = AdaptiveVoxelFilter()
    downsampled, _ = filter_obj.downsample(points, semantic_labels, normals)
    return downsampled


def estimate_savings(points: np.ndarray, semantic_labels: np.ndarray | None = None) -> dict:
    """Estimate memory savings."""
    filter_obj = AdaptiveVoxelFilter()
    return filter_obj.estimate_memory_savings(points, semantic_labels)

#!/usr/bin/env python3
"""
PCD Matcher - Point Cloud Registration for Long Horizon Fusion

Aligns historical PCD with current sensor data using ICP (Iterative Closest Point)
similar to VisionPilot and Autoware Universe implementation.

Key features:
- Fast ICP with voxel grid downsampling
- Initial guess from GPS/heading
- Robust outlier rejection
- Multi-scale registration (coarse to fine)
"""

from __future__ import annotations

import numpy as np
from typing import Any
from dataclasses import dataclass
from enum import Enum
from openpilot.common.swaglog import cloudlog

class RegistrationStatus(Enum):
    """ICP registration status."""
    SUCCESS = "success"
    NOT_ENOUGH_POINTS = "not_enough_points"
    MAX_ITERATIONS = "max_iterations"
    DIVERGED = "diverged"
    LOW_OVERLAP = "low_overlap"


@dataclass
class RegistrationResult:
    """Result of point cloud registration."""
    success: bool
    transform: np.ndarray  # 4x4 transformation matrix
    fitness_score: float   # Lower is better (avg distance)
    inlier_ratio: float    # Ratio of inlier points
    iterations: int
    status: RegistrationStatus
    
    @property
    def translation(self) -> np.ndarray:
        """Extract translation (x, y, z)."""
        return self.transform[:3, 3]
    
    @property
    def rotation_matrix(self) -> np.ndarray:
        """Extract 3x3 rotation matrix."""
        return self.transform[:3, :3]
    
    @property
    def yaw_deg(self) -> float:
        """Extract yaw rotation in degrees."""
        R = self.rotation_matrix
        return np.degrees(np.arctan2(R[1, 0], R[0, 0]))


class VoxelGrid:
    """Fast voxel grid downsampling."""
    
    def __init__(self, leaf_size: float = 0.5):
        self.leaf_size = leaf_size
    
    def downsample(self, points: np.ndarray) -> np.ndarray:
        """Downsample point cloud using voxel grid."""
        if len(points) == 0:
            return points
        
        # Compute voxel indices
        voxel_indices = np.floor(points / self.leaf_size).astype(np.int32)
        
        # Find unique voxels
        unique_voxels, inverse = np.unique(
            voxel_indices, axis=0, return_inverse=True
        )
        
        # Compute centroids for each voxel
        downsampled = np.zeros((len(unique_voxels), 3), dtype=np.float32)
        for i in range(len(unique_voxels)):
            mask = inverse == i
            downsampled[i] = np.mean(points[mask], axis=0)
        
        return downsampled


class ICPMatcher:
    """
    Iterative Closest Point registration.
    
    Aligns source point cloud to target point cloud.
    Uses point-to-point ICP with robust outlier rejection.
    """
    
    def __init__(
        self,
        max_iterations: int = 50,
        tolerance: float = 1e-6,
        max_correspondence_distance: float = 2.0,
        min_points: int = 100,
        voxel_sizes: tuple[float, ...] = (1.0, 0.5, 0.25)
    ):
        self.max_iterations = max_iterations
        self.tolerance = tolerance
        self.max_correspondence_distance = max_correspondence_distance
        self.min_points = min_points
        self.voxel_sizes = voxel_sizes
        
        # Statistics
        self._last_fitness = 0.0
        self._last_iterations = 0
        
        # Lazy-init HAL client for ACL GEMM
        self._hal_client = None
        self._hal_gpu = None
    
    def _get_hal_gpu(self):
        """Lazy-init HAL GPU backend to avoid creating client per-call."""
        if self._hal_gpu is None:
            try:
                from openpilot.system.inferenced.client import InferenceClient
                self._hal_client = InferenceClient("surfaced")
                self._hal_gpu = self._hal_client.acl()
            except Exception:
                self._hal_gpu = None
        return self._hal_gpu
    
    def release(self):
        """Release HAL resources."""
        if self._hal_client is not None:
            self._hal_client.release()
            self._hal_client = None
        self._hal_gpu = None
    
    def _find_correspondences(
        self,
        source: np.ndarray,
        target: np.ndarray,
        max_distance: float
    ) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
        """
        Find nearest neighbor correspondences.
        
        Returns:
            source_indices: Indices of source points with matches
            target_indices: Indices of corresponding target points
            distances: Distance for each correspondence
        """
        # Simple brute-force nearest neighbor (fast enough for small clouds)
        # For larger clouds, use KD-tree
        source_indices = []
        target_indices = []
        distances = []
        
        for i, src_pt in enumerate(source):
            # Find nearest target point
            diff = target - src_pt
            dists = np.sum(diff ** 2, axis=1)
            min_idx = np.argmin(dists)
            min_dist = np.sqrt(dists[min_idx])
            
            if min_dist < max_distance:
                source_indices.append(i)
                target_indices.append(min_idx)
                distances.append(min_dist)
        
        return (
            np.array(source_indices),
            np.array(target_indices),
            np.array(distances)
        )
    
    def _compute_transform(
        self,
        source: np.ndarray,
        target: np.ndarray
    ) -> np.ndarray:
        """
        Compute optimal rigid transformation (SVD-based).
        
        Uses Umeyama's method for least-squares registration.
        """
        # Compute centroids
        source_centroid = np.mean(source, axis=0)
        target_centroid = np.mean(target, axis=0)
        
        # Center the points
        source_centered = source - source_centroid
        target_centered = target - target_centroid
        
        # Compute cross-covariance matrix
        H = source_centered.T @ target_centered
        
        # SVD
        U, S, Vt = np.linalg.svd(H)
        
        # Compute rotation
        R = Vt.T @ U.T
        
        # Handle reflection case
        if np.linalg.det(R) < 0:
            Vt[-1, :] *= -1
            R = Vt.T @ U.T
        
        # Compute translation
        t = target_centroid - R @ source_centroid
        
        # Build 4x4 transformation matrix
        transform = np.eye(4)
        transform[:3, :3] = R
        transform[:3, 3] = t
        
        return transform
    
    def register(
        self,
        source: np.ndarray,
        target: np.ndarray,
        initial_guess: np.ndarray | None = None
    ) -> RegistrationResult:
        """
        Register source point cloud to target.
        
        Args:
            source: Source points (N, 3) - historical PCD
            target: Target points (M, 3) - current sensor data
            initial_guess: Initial 4x4 transformation (optional)
            
        Returns:
            RegistrationResult with transform and metadata
        """
        # Validate inputs
        if len(source) < self.min_points or len(target) < self.min_points:
            return RegistrationResult(
                success=False,
                transform=np.eye(4),
                fitness_score=float('inf'),
                inlier_ratio=0.0,
                iterations=0,
                status=RegistrationStatus.NOT_ENOUGH_POINTS
            )
        
        # Multi-scale registration (coarse to fine)
        current_transform = initial_guess.copy() if initial_guess is not None else np.eye(4)
        
        for voxel_size in self.voxel_sizes:
            voxel = VoxelGrid(leaf_size=voxel_size)
            source_down = voxel.downsample(source)
            target_down = voxel.downsample(target)
            
            result = self._register_single_scale(
                source_down, target_down, current_transform
            )
            
            if not result.success:
                return result
            
            current_transform = result.transform
        
        return result
    
    def _register_single_scale(
        self,
        source: np.ndarray,
        target: np.ndarray,
        initial_transform: np.ndarray
    ) -> RegistrationResult:
        """ICP at single resolution."""
        
        transform = initial_transform.copy()
        source_transformed = self._transform_points(source, transform)
        
        prev_error = float('inf')
        
        for iteration in range(self.max_iterations):
            # Find correspondences
            src_idx, tgt_idx, distances = self._find_correspondences(
                source_transformed,
                target,
                self.max_correspondence_distance
            )
            
            if len(src_idx) < self.min_points // 2:
                return RegistrationResult(
                    success=False,
                    transform=transform,
                    fitness_score=prev_error,
                    inlier_ratio=len(src_idx) / len(source),
                    iterations=iteration,
                    status=RegistrationStatus.LOW_OVERLAP
                )
            
            # Compute optimal transform for correspondences
            delta_transform = self._compute_transform(
                source_transformed[src_idx],
                target[tgt_idx]
            )
            
            # Update cumulative transform
            transform = delta_transform @ transform
            source_transformed = self._transform_points(source, transform)
            
            # Check convergence
            mean_error = np.mean(distances)
            if abs(prev_error - mean_error) < self.tolerance:
                break
            
            prev_error = mean_error
        
        else:
            # Max iterations reached
            return RegistrationResult(
                success=True,  # Still usable
                transform=transform,
                fitness_score=prev_error,
                inlier_ratio=len(src_idx) / len(source),
                iterations=self.max_iterations,
                status=RegistrationStatus.MAX_ITERATIONS
            )
        
        # Success
        self._last_fitness = prev_error
        self._last_iterations = iteration + 1
        
        return RegistrationResult(
            success=True,
            transform=transform,
            fitness_score=prev_error,
            inlier_ratio=len(src_idx) / len(source),
            iterations=iteration + 1,
            status=RegistrationStatus.SUCCESS
        )
    
    def _transform_points(
        self,
        points: np.ndarray,
        transform: np.ndarray
    ) -> np.ndarray:
        """Apply transformation to points."""
        n_points = len(points)
        
        # Convert to homogeneous coordinates
        ones = np.ones((n_points, 1), dtype=np.float32)
        homogeneous = np.hstack([points, ones])
        
        # Transform: (4x4) @ (4xN) → (4xN)
        # For large point clouds, use ACL GEMM if available
        if n_points > 1000:
            acl = self._get_hal_gpu()
            if acl is not None:
                try:
                    result = acl.infer(
                        model_name='gemm',
                        inputs={'A': transform.astype(np.float32), 'B': homogeneous.T.astype(np.float32)}
                    )
                    if result.success:
                        transformed = result.outputs['C'].T
                        return transformed[:, :3].astype(np.float32)
                except Exception:
                    pass  # Fall back to numpy
        
        # Numpy fallback
        transformed = (transform @ homogeneous.T).T
        
        # Back to 3D
        return transformed[:, :3].astype(np.float32)
    
    def get_stats(self) -> dict[str, Any]:
        """Get matcher statistics."""
        return {
            "max_iterations": self.max_iterations,
            "tolerance": self.tolerance,
            "last_fitness": self._last_fitness,
            "last_iterations": self._last_iterations,
        }


class GPSInitialGuess:
    """
    Compute initial alignment guess from GPS/heading difference.
    
    Converts lat/lon/heading to local transformation.
    """
    
    @staticmethod
    def compute(
        current_lat: float,
        current_lon: float,
        current_heading: float,
        historical_lat: float,
        historical_lon: float,
        historical_heading: float
    ) -> np.ndarray:
        """
        Compute transformation from historical to current frame.
        
        Args:
            current_lat, current_lon: Current position (degrees)
            current_heading: Current heading (degrees, 0=north)
            historical_lat, historical_lon: Historical PCD position
            historical_heading: Historical PCD heading
            
        Returns:
            4x4 transformation matrix
        """
        # Convert lat/lon to local meters (approximate)
        # 1 degree lat ~ 111km, 1 degree lon varies by latitude
        lat_diff = current_lat - historical_lat
        lon_diff = current_lon - historical_lon
        
        # Approximate local coordinates (meters)
        meters_per_deg_lat = 111320.0
        meters_per_deg_lon = 111320.0 * np.cos(np.radians(current_lat))
        
        dx = lon_diff * meters_per_deg_lon  # East-West
        dy = lat_diff * meters_per_deg_lat  # North-South
        
        # Heading difference
        heading_diff = current_heading - historical_heading
        heading_rad = np.radians(heading_diff)
        
        # Build transformation matrix
        cos_h = np.cos(heading_rad)
        sin_h = np.sin(heading_rad)
        
        transform = np.array([
            [cos_h, -sin_h, 0, dx],
            [sin_h, cos_h, 0, dy],
            [0, 0, 1, 0],
            [0, 0, 0, 1]
        ], dtype=np.float32)
        
        return transform


class HistoricalPCDMatcher:
    """
    High-level interface for matching historical PCD to current frame.
    
    Combines:
    1. GPS-based initial guess
    2. ICP refinement
    3. Quality validation
    """
    
    def __init__(
        self,
        fitness_threshold: float = 0.5,
        inlier_threshold: float = 0.3,
        max_yaw_diff_deg: float = 30.0
    ):
        self.icp = ICPMatcher()
        self.gps_guess = GPSInitialGuess()
        
        self.fitness_threshold = fitness_threshold
        self.inlier_threshold = inlier_threshold
        self.max_yaw_diff_deg = max_yaw_diff_deg
        
        # Statistics
        self._matches_attempted = 0
        self._matches_successful = 0
        self._avg_fitness = 0.0
    
    def match(
        self,
        historical_points: np.ndarray,
        current_points: np.ndarray,
        current_lat: float,
        current_lon: float,
        current_heading: float,
        historical_lat: float = 0.0,
        historical_lon: float = 0.0,
        historical_heading: float = 0.0
    ) -> tuple[np.ndarray | None, RegistrationResult]:
        """
        Match historical PCD to current frame.
        
        Args:
            historical_points: Points from saved PCD
            current_points: Points from current sensor
            current_lat, current_lon, current_heading: Current pose
            historical_lat, historical_lon, historical_heading: Historical pose (if known)
            
        Returns:
            (transformed_points, result)
            transformed_points is None if match failed
        """
        self._matches_attempted += 1
        
        # Compute initial guess from GPS if historical pose known
        if historical_lat != 0 and historical_lon != 0:
            initial_guess = self.gps_guess.compute(
                current_lat, current_lon, current_heading,
                historical_lat, historical_lon, historical_heading
            )
        else:
            initial_guess = np.eye(4)
        
        # Run ICP
        result = self.icp.register(historical_points, current_points, initial_guess)
        
        # Validate result
        if not result.success:
            cloudlog.debug(f"ICP failed: {result.status.value}, fitness={result.fitness_score:.3f}")
            return None, result
        
        # Check quality thresholds
        if result.fitness_score > self.fitness_threshold:
            cloudlog.debug(f"ICP fitness too low: {result.fitness_score:.3f} > {self.fitness_threshold}")
            return None, result
        
        if result.inlier_ratio < self.inlier_threshold:
            cloudlog.debug(f"ICP inlier ratio too low: {result.inlier_ratio:.3f} < {self.inlier_threshold}")
            return None, result
        
        if abs(result.yaw_deg) > self.max_yaw_diff_deg:
            cloudlog.debug(f"ICP yaw diff too large: {result.yaw_deg:.1f} > {self.max_yaw_diff_deg}")
            return None, result
        
        # Success - transform historical points
        transformed = self._transform_points(historical_points, result.transform)
        
        self._matches_successful += 1
        
        # Update stats
        alpha = 0.1
        self._avg_fitness = (1 - alpha) * self._avg_fitness + alpha * result.fitness_score
        
        cloudlog.debug(f"PCD match success: fitness={result.fitness_score:.3f}, "
                    f"inliers={result.inlier_ratio:.2%}, yaw={result.yaw_deg:.1f}°")
        
        return transformed, result
    
    def _transform_points(
        self,
        points: np.ndarray,
        transform: np.ndarray
    ) -> np.ndarray:
        """Apply transformation to points."""
        ones = np.ones((len(points), 1), dtype=np.float32)
        homogeneous = np.hstack([points, ones])
        transformed = (transform @ homogeneous.T).T
        return transformed[:, :3].astype(np.float32)
    
    def get_stats(self) -> dict[str, Any]:
        """Get matcher statistics."""
        success_rate = (
            self._matches_successful / self._matches_attempted * 100
            if self._matches_attempted > 0 else 0
        )
        return {
            "matches_attempted": self._matches_attempted,
            "matches_successful": self._matches_successful,
            "success_rate": f"{success_rate:.1f}%",
            "avg_fitness": f"{self._avg_fitness:.3f}",
            "icp_stats": self.icp.get_stats(),
        }

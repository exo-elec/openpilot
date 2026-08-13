#!/usr/bin/env python3
"""
Semantic Point Cloud Filter - Remove noise and dynamic objects using semantic labels.

Uses PP-LiteSeg semantic labels to intelligently filter point clouds for fleet mapping.
"""

import numpy as np

from dataclasses import dataclass


@dataclass
class FilterConfig:
    """Configuration for semantic filtering."""
    # Classes to remove
    remove_noise: bool = True      # vegetation, sky
    remove_dynamic: bool = True    # cars, people, bikes
    remove_unlabeled: bool = True  # points with no semantic label

    # Confidence thresholds
    min_semantic_confidence: float = 0.5

    # Size limits
    max_point_distance: float = 100.0  # Remove points beyond this (meters)

    # Vegetation-specific
    vegetation_distance_threshold: float = 50.0  # Remove vegetation beyond this


class SemanticPointFilter:
    """
    Filter point cloud using semantic labels from PP-LiteSeg.

    Removes:
    - Noise: vegetation (wind movement), sky (invalid depth)
    - Dynamic objects: cars, trucks, people, bikes
    - Distant points: beyond reliable stereo range

    Keeps:
    - Static infrastructure: road, buildings, curbs, poles
    - Terrain: ground, sidewalk
    """

    # Cityscapes class IDs
    CLASSES = {
        0: 'road', 1: 'sidewalk', 2: 'building', 3: 'wall',
        4: 'fence', 5: 'pole', 6: 'traffic_light', 7: 'traffic_sign',
        8: 'vegetation', 9: 'terrain', 10: 'sky', 11: 'person',
        12: 'rider', 13: 'car', 14: 'truck', 15: 'bus',
        16: 'train', 17: 'motorcycle', 18: 'bicycle'
    }

    # Class sets for filtering
    NOISE_CLASSES = {8, 10}  # vegetation, sky
    DYNAMIC_CLASSES = {11, 12, 13, 14, 15, 16, 17, 18}  # person to bicycle
    STATIC_CLASSES = {0, 1, 2, 3, 4, 5, 6, 7, 9}  # road to terrain

    def __init__(self, config: FilterConfig | None = None):
        self.config = config or FilterConfig()

    def filter(self,
               points: np.ndarray,
               semantic_labels: np.ndarray,
               semantic_confidence: np.ndarray | None = None) -> tuple[np.ndarray, np.ndarray, dict]:
        """
        Filter point cloud using semantic labels.

        Args:
            points: Nx3 array of XYZ coordinates
            semantic_labels: N array of class indices (-1 = unlabeled)
            semantic_confidence: Optional N array of confidence scores

        Returns:
            filtered_points: Mx3 array (M <= N)
            filtered_labels: M array of labels
            stats: dict with filtering statistics
        """
        n_original = len(points)
        keep_mask = np.ones(n_original, dtype=bool)

        # Start with all points valid
        removal_reasons = {
            'noise': 0,
            'dynamic': 0,
            'unlabeled': 0,
            'distance': 0,
            'confidence': 0
        }

        # 1. Remove by semantic class
        if self.config.remove_noise:
            noise_mask = np.isin(semantic_labels, list(self.NOISE_CLASSES))
            removal_reasons['noise'] = int(np.sum(noise_mask))
            keep_mask &= ~noise_mask

        if self.config.remove_dynamic:
            dynamic_mask = np.isin(semantic_labels, list(self.DYNAMIC_CLASSES))
            removal_reasons['dynamic'] = int(np.sum(dynamic_mask))
            keep_mask &= ~dynamic_mask

        if self.config.remove_unlabeled:
            unlabeled_mask = semantic_labels < 0
            removal_reasons['unlabeled'] = int(np.sum(unlabeled_mask))
            keep_mask &= ~unlabeled_mask

        # 2. Remove by confidence
        if semantic_confidence is not None and self.config.min_semantic_confidence > 0:
            low_conf_mask = semantic_confidence < self.config.min_semantic_confidence
            removal_reasons['confidence'] = int(np.sum(low_conf_mask & keep_mask))
            keep_mask &= ~low_conf_mask

        # 3. Remove by distance
        distances = np.linalg.norm(points, axis=1)
        far_mask = distances > self.config.max_point_distance
        removal_reasons['distance'] = int(np.sum(far_mask & keep_mask))
        keep_mask &= ~far_mask

        # 4. Special handling: distant vegetation is very noisy
        if self.config.remove_noise:
            vegetation_mask = semantic_labels == 8  # vegetation class
            distant_mask = distances > self.config.vegetation_distance_threshold
            distant_veg_mask = vegetation_mask & distant_mask
            removal_reasons['noise'] += int(np.sum(distant_veg_mask & keep_mask))
            keep_mask &= ~distant_veg_mask

        # Apply filter
        filtered_points = points[keep_mask]
        filtered_labels = semantic_labels[keep_mask]

        # Statistics
        n_filtered = len(filtered_points)
        stats = {
            'original_count': n_original,
            'filtered_count': n_filtered,
            'removal_rate': 1.0 - (n_filtered / n_original) if n_original > 0 else 0,
            'removal_reasons': removal_reasons,
            'kept_classes': self._get_class_distribution(filtered_labels)
        }

        return filtered_points, filtered_labels, stats

    def _get_class_distribution(self, labels: np.ndarray) -> dict[str, int]:
        """Get count of each class in filtered point cloud."""
        distribution = {}
        unique, counts = np.unique(labels, return_counts=True)
        for cls_id, count in zip(unique, counts, strict=False):
            name = self.CLASSES.get(cls_id, f'unknown_{cls_id}')
            distribution[name] = int(count)
        return distribution

    def get_ground_points(self,
                         points: np.ndarray,
                         semantic_labels: np.ndarray) -> np.ndarray:
        """
        Get ground/road points using semantic labels.

        Returns:
            mask: Boolean array (True = ground point)
        """
        ground_classes = {0, 1, 9}  # road, sidewalk, terrain
        return np.isin(semantic_labels, list(ground_classes))

    def get_building_points(self,
                           points: np.ndarray,
                           semantic_labels: np.ndarray) -> np.ndarray:
        """Get building/wall points."""
        building_classes = {2, 3}  # building, wall
        return np.isin(semantic_labels, list(building_classes))

    def get_pole_points(self,
                       points: np.ndarray,
                       semantic_labels: np.ndarray) -> np.ndarray:
        """Get pole/traffic sign points (good for localization)."""
        pole_classes = {5, 6, 7}  # pole, traffic_light, traffic_sign
        return np.isin(semantic_labels, list(pole_classes))


class ConfidenceBasedFilter:
    """
    Additional confidence-based filtering using depth and semantic confidence.
    """

    def __init__(self,
                 min_depth_confidence: float = 0.3,
                 min_semantic_confidence: float = 0.5):
        self.min_depth_confidence = min_depth_confidence
        self.min_semantic_confidence = min_semantic_confidence

    def filter_by_confidence(self,
                            points: np.ndarray,
                            depth_confidence: np.ndarray,
                            semantic_confidence: np.ndarray | None = None) -> np.ndarray:
        """
        Filter points by confidence scores.

        Returns:
            mask: Boolean array (True = keep point)
        """
        # Depth confidence
        mask = depth_confidence >= self.min_depth_confidence

        # Semantic confidence (if available)
        if semantic_confidence is not None:
            mask &= semantic_confidence >= self.min_semantic_confidence

        return mask


# Convenience functions for pointcloudd integration

def filter_pointcloud_semantic(points: np.ndarray,
                               semantic_labels: np.ndarray,
                               semantic_confidence: np.ndarray | None = None,
                               remove_dynamic: bool = True,
                               remove_noise: bool = True) -> tuple[np.ndarray, np.ndarray, dict]:
    """
    Convenience function for semantic filtering.

    Usage:
        filtered_points, filtered_labels, stats = filter_pointcloud_semantic(
            points, labels, confidences
        )
        print(f"Removed {stats['removal_rate']*100:.1f}% of points")
    """
    config = FilterConfig(
        remove_dynamic=remove_dynamic,
        remove_noise=remove_noise
    )

    filter_obj = SemanticPointFilter(config)
    return filter_obj.filter(points, semantic_labels, semantic_confidence)


def quick_filter_for_fleet(points: np.ndarray,
                          semantic_labels: np.ndarray) -> np.ndarray:
    """
    Quick filter optimized for fleet PCD upload.

    Removes: vegetation, sky, dynamic objects, unlabeled
    Keeps: static infrastructure only

    Returns:
        filtered_points: Clean static environment
    """
    config = FilterConfig(
        remove_noise=True,
        remove_dynamic=True,
        remove_unlabeled=True,
        max_point_distance=80.0  # Stereo reliable range
    )

    filter_obj = SemanticPointFilter(config)
    filtered_points, _, stats = filter_obj.filter(points, semantic_labels)

    return filtered_points

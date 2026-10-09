#!/usr/bin/env python3
"""
Semantic Point Cloud Fusion - Fuse PP-LiteSeg 2D labels with 3D stereo points.

Converts 2D segmentation masks to per-point semantic labels for enhanced
point cloud understanding and filtering.
"""

import numpy as np
from dataclasses import dataclass


@dataclass
class SemanticPointCloud:
    """Point cloud with per-point semantic labels."""
    points: np.ndarray  # Nx3 XYZ coordinates
    labels: np.ndarray  # N integer class indices
    confidences: np.ndarray  # N confidence scores (0-1)

    # Optional: per-class timestamps for temporal consistency
    label_sources: np.ndarray | None = None  # Which camera/source


class SemanticFusion:
    """
    Fuse PP-LiteSeg 2D segmentation with 3D stereo point cloud.

    Projects 3D points to 2D image coordinates and samples segmentation
    mask to assign semantic labels to each point.
    """

    # Cityscapes class mapping (PP-LiteSeg output)
    CITYSCAPES_CLASSES = {
        0: 'road',
        1: 'sidewalk',
        2: 'building',
        3: 'wall',
        4: 'fence',
        5: 'pole',
        6: 'traffic_light',
        7: 'traffic_sign',
        8: 'vegetation',
        9: 'terrain',
        10: 'sky',
        11: 'person',
        12: 'rider',
        13: 'car',
        14: 'truck',
        15: 'bus',
        16: 'train',
        17: 'motorcycle',
        18: 'bicycle'
    }

    # Classes that are static (good for mapping)
    STATIC_CLASSES = {
        'road', 'sidewalk', 'building', 'wall', 'fence',
        'pole', 'traffic_light', 'traffic_sign', 'terrain'
    }

    # Classes that are dynamic (should be filtered)
    DYNAMIC_CLASSES = {
        'person', 'rider', 'car', 'truck', 'bus',
        'train', 'motorcycle', 'bicycle'
    }

    # Classes that are noise (should be removed)
    NOISE_CLASSES = {
        'vegetation',  # Wind movement
        'sky'          # Invalid depth
    }

    def __init__(self,
                 image_width: int = 640,
                 image_height: int = 480,
                 focal_length_px: float = 700.0):
        """
        Args:
            image_width: Camera image width
            image_height: Camera image height
            focal_length_px: Focal length in pixels
        """
        self.image_width = image_width
        self.image_height = image_height
        self.focal_length = focal_length_px

        # Principal point (center of image)
        self.cx = image_width / 2.0
        self.cy = image_height / 2.0

    def project_points_to_image(self,
                                points_3d: np.ndarray,
                                camera_pose: np.ndarray | None = None) -> np.ndarray:
        """
        Project 3D camera-frame points to 2D image coordinates.

        Args:
            points_3d: Nx3 array (x=right, y=down, z=forward)
            camera_pose: Optional 4x4 transformation matrix

        Returns:
            points_2d: Nx2 array (u, v) image coordinates
        """
        if camera_pose is not None:
            # Transform points to camera frame
            points_h = np.hstack([points_3d, np.ones((len(points_3d), 1))])
            points_cam = (camera_pose @ points_h.T).T[:, :3]
        else:
            points_cam = points_3d

        # Pinhole camera projection
        # x = X * f / Z, y = Y * f / Z
        z = points_cam[:, 2]

        # Avoid division by zero
        valid_mask = z > 0.1

        points_2d = np.zeros((len(points_3d), 2), dtype=np.int32)

        # Project valid points
        points_2d[valid_mask, 0] = (points_cam[valid_mask, 0] *
                                     self.focal_length / z[valid_mask] + self.cx).astype(np.int32)
        points_2d[valid_mask, 1] = (points_cam[valid_mask, 1] *
                                     self.focal_length / z[valid_mask] + self.cy).astype(np.int32)

        # Clamp to image bounds
        points_2d[:, 0] = np.clip(points_2d[:, 0], 0, self.image_width - 1)
        points_2d[:, 1] = np.clip(points_2d[:, 1], 0, self.image_height - 1)

        return points_2d, valid_mask

    def fuse(self,
             points_3d: np.ndarray,
             seg_mask: np.ndarray,
             seg_confidence: np.ndarray | None = None,
             camera_pose: np.ndarray | None = None) -> SemanticPointCloud:
        """
        Fuse 3D point cloud with 2D segmentation mask.

        Args:
            points_3d: Nx3 array of 3D points (camera frame)
            seg_mask: HxW array of class indices (from PP-LiteSeg)
            seg_confidence: Optional HxW array of confidence scores
            camera_pose: Optional camera extrinsics

        Returns:
            SemanticPointCloud with labels and confidences
        """
        # Project points to image
        points_2d, valid_mask = self.project_points_to_image(points_3d, camera_pose)

        # Sample segmentation mask at projected locations
        labels = seg_mask[points_2d[:, 1], points_2d[:, 0]]

        # Get confidence scores
        if seg_confidence is not None:
            confidences = seg_confidence[points_2d[:, 1], points_2d[:, 0]]
        else:
            # Default confidence for valid projections
            confidences = valid_mask.astype(np.float32)

        # Mark invalid projections (behind camera, etc.)
        labels[~valid_mask] = -1  # Invalid label
        confidences[~valid_mask] = 0.0

        return SemanticPointCloud(
            points=points_3d,
            labels=labels,
            confidences=confidences
        )

    def filter_static_points(self, semantic_pc: SemanticPointCloud) -> np.ndarray:
        """
        Return mask of static (non-dynamic, non-noise) points.

        Returns:
            mask: Boolean array (True = keep point)
        """
        static_label_ids = [
            class_id for class_id, name in self.CITYSCAPES_CLASSES.items()
            if name in self.STATIC_CLASSES
        ]

        return np.isin(semantic_pc.labels, static_label_ids)

    def filter_noise_points(self, semantic_pc: SemanticPointCloud) -> np.ndarray:
        """
        Return mask of non-noise points (remove vegetation, sky).

        Returns:
            mask: Boolean array (True = keep point)
        """
        noise_label_ids = [
            class_id for class_id, name in self.CITYSCAPES_CLASSES.items()
            if name in self.NOISE_CLASSES
        ]

        return ~np.isin(semantic_pc.labels, noise_label_ids)

    def get_ground_points(self, semantic_pc: SemanticPointCloud) -> np.ndarray:
        """
        Return mask of ground/road points.

        Uses semantic labels 'road' and 'sidewalk' for ground detection.
        More reliable than geometric-only RANSAC.

        Returns:
            mask: Boolean array (True = ground point)
        """
        ground_label_ids = [
            class_id for class_id, name in self.CITYSCAPES_CLASSES.items()
            if name in ['road', 'sidewalk', 'terrain']
        ]

        return np.isin(semantic_pc.labels, ground_label_ids)

    def get_class_name(self, label_id: int) -> str:
        """Get class name from label ID."""
        return self.CITYSCAPES_CLASSES.get(label_id, 'unknown')

    def get_label_id(self, class_name: str) -> int:
        """Get label ID from class name."""
        for lid, name in self.CITYSCAPES_CLASSES.items():
            if name == class_name:
                return lid
        return -1


class SemanticGroundExtractor:
    """
    Extract ground plane using semantic labels.

    Uses PP-LiteSeg 'road' and 'sidewalk' labels to identify ground points,
    then fits a plane using least-squares (faster than RANSAC).
    """

    def __init__(self, min_points: int = 100):
        self.min_points = min_points
        self.fusion = SemanticFusion()

    def extract_ground(self, semantic_pc: SemanticPointCloud) -> tuple[np.ndarray, np.ndarray]:
        """
        Extract ground plane from semantic point cloud.

        Args:
            semantic_pc: Point cloud with semantic labels

        Returns:
            ground_mask: Boolean array (True = ground point)
            plane_coeffs: [a, b, c, d] for plane equation ax + by + cz + d = 0
        """
        # Get ground points from semantics
        ground_mask = self.fusion.get_ground_points(semantic_pc)

        if np.sum(ground_mask) < self.min_points:
            # Fall back to geometric RANSAC if not enough labeled ground
            return self._ransac_fallback(semantic_pc.points)

        ground_points = semantic_pc.points[ground_mask]

        # Fit plane using least-squares (fast and accurate with good labels)
        plane_coeffs = self._fit_plane_least_squares(ground_points)

        # Refine mask using distance to fitted plane
        distances = self._point_to_plane_distance(semantic_pc.points, plane_coeffs)
        refined_mask = np.abs(distances) < 0.1  # 10cm threshold

        return refined_mask, plane_coeffs

    def _fit_plane_least_squares(self, points: np.ndarray) -> np.ndarray:
        """
        Fit plane to points using least-squares.

        Plane equation: ax + by + cz + d = 0
        Solve: [x y z 1] * [a b c d]^T = 0
        """
        # Build matrix A = [x y z 1]
        A = np.hstack([points, np.ones((len(points), 1))])

        # Solve using SVD (Ax = 0)
        _, _, Vt = np.linalg.svd(A)
        plane_coeffs = Vt[-1, :]  # Last row is solution

        # Normalize
        norm = np.linalg.norm(plane_coeffs[:3])
        if norm > 0:
            plane_coeffs = plane_coeffs / norm

        return plane_coeffs

    def _point_to_plane_distance(self, points: np.ndarray, plane: np.ndarray) -> np.ndarray:
        """Calculate signed distance from points to plane."""
        a, b, c, d = plane
        return (a * points[:, 0] + b * points[:, 1] + c * points[:, 2] + d)

    def _ransac_fallback(self, points: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
        """Fallback to simple geometric ground detection."""
        # Simple heuristic: lowest 30% of points by Z are ground
        z_threshold = np.percentile(points[:, 2], 30)
        ground_mask = points[:, 2] < z_threshold

        plane_coeffs = self._fit_plane_least_squares(points[ground_mask])
        return ground_mask, plane_coeffs


# Convenience function for stereod integration
def fuse_semantic_to_pointcloud(points_3d: np.ndarray,
                                 seg_mask: np.ndarray,
                                 image_width: int = 640,
                                 image_height: int = 480) -> SemanticPointCloud:
    """
    Convenience function to fuse PP-LiteSeg segmentation with point cloud.

    Usage:
        semantic_pc = fuse_semantic_to_pointcloud(xyz_points, ppliteseg_mask)
        ground_mask = semantic_fusion.get_ground_points(semantic_pc)
        static_mask = semantic_fusion.filter_static_points(semantic_pc)
    """
    fusion = SemanticFusion(image_width, image_height)
    return fusion.fuse(points_3d, seg_mask)

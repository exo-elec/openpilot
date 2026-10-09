#!/usr/bin/env python3
"""
Semantic Feature Extractor - Extract landmarks for ICP registration.

Extracts stable features (poles, signs, building corners) for point cloud matching.
Much faster and more robust than point-to-point ICP.
"""

import numpy as np
from dataclasses import dataclass
from scipy.spatial import cKDTree


@dataclass
class Landmark:
    """Extracted landmark for ICP matching."""
    landmark_type: str  # 'pole', 'sign', 'corner', 'curb_line'
    position: np.ndarray  # 3D position [x, y, z]

    # For poles/signs
    height: float | None = None

    # For corners
    angle: float | None = None  # Corner angle in degrees

    # For curb lines
    direction: np.ndarray | None = None  # Unit vector along line

    # Confidence
    confidence: float = 1.0

    def to_dict(self) -> dict:
        """Convert to dictionary for serialization."""
        return {
            'type': self.landmark_type,
            'position': [float(x) for x in self.position],
            'height': float(self.height) if self.height else None,
            'angle': float(self.angle) if self.angle else None,
            'direction': [float(x) for x in self.direction] if self.direction is not None else None,
            'confidence': float(self.confidence)
        }


@dataclass
class SemanticFeatures:
    """Collection of extracted semantic features."""
    poles: list[Landmark]
    signs: list[Landmark]
    building_corners: list[Landmark]
    curb_lines: list[Landmark]

    def to_dict(self) -> dict:
        return {
            'poles': [p.to_dict() for p in self.poles],
            'signs': [s.to_dict() for s in self.signs],
            'building_corners': [c.to_dict() for c in self.building_corners],
            'curb_lines': [c.to_dict() for c in self.curb_lines]
        }

    def get_all_positions(self) -> np.ndarray:
        """Get all landmark positions as Nx3 array."""
        all_landmarks = self.poles + self.signs + self.building_corners
        if not all_landmarks:
            return np.zeros((0, 3))
        return np.array([lm.position for lm in all_landmarks])


class PoleExtractor:
    """Extract vertical pole landmarks from point cloud."""

    def __init__(self,
                 min_height: float = 2.0,      # Minimum pole height
                 max_radius: float = 0.3,      # Maximum pole radius
                 vertical_threshold: float = 0.9):  # cos(angle) for vertical
        self.min_height = min_height
        self.max_radius = max_radius
        self.vertical_threshold = vertical_threshold

    def extract(self,
               points: np.ndarray,
               semantic_labels: np.ndarray,
               pole_class_ids: list[int] = None) -> list[Landmark]:
        """
        Extract pole landmarks.

        Args:
            points: Nx3 point cloud
            semantic_labels: N array of class labels
            pole_class_ids: Class IDs for poles (default: 5=pole, 6=traffic_light, 7=sign)

        Returns:
            list of pole landmarks
        """
        if pole_class_ids is None:
            pole_class_ids = [5, 6, 7]  # pole, traffic_light, traffic_sign

        # Get pole points
        pole_mask = np.isin(semantic_labels, pole_class_ids)
        pole_points = points[pole_mask]

        if len(pole_points) < 10:
            return []

        # Cluster pole points vertically
        # Group by (x, y) position, find height ranges
        xy_coords = pole_points[:, :2]

        # Use 2D grid for clustering
        grid_resolution = 0.5  # 50cm grid
        grid_indices = np.floor(xy_coords / grid_resolution).astype(np.int32)

        # Find unique grid cells
        unique_cells = np.unique(grid_indices, axis=0)

        poles = []
        for cell in unique_cells:
            # Get points in this cell
            cell_mask = (grid_indices == cell).all(axis=1)
            cell_points = pole_points[cell_mask]

            if len(cell_points) < 5:
                continue

            # Check height range
            z_min, z_max = cell_points[:, 2].min(), cell_points[:, 2].max()
            height = z_max - z_min

            if height < self.min_height:
                continue

            # Calculate center position
            center = cell_points.mean(axis=0)

            # Calculate confidence based on point count and height
            confidence = min(len(cell_points) / 50.0, 1.0) * min(height / 4.0, 1.0)

            poles.append(Landmark(
                landmark_type='pole',
                position=center,
                height=height,
                confidence=confidence
            ))

        return poles


class CornerExtractor:
    """Extract building/wall corner landmarks."""

    def __init__(self,
                 min_angle: float = 60.0,      # Minimum corner angle
                 max_angle: float = 120.0,     # Maximum corner angle
                 search_radius: float = 2.0):  # Radius for neighbor search
        self.min_angle = min_angle
        self.max_angle = max_angle
        self.search_radius = search_radius

    def extract(self,
               points: np.ndarray,
               semantic_labels: np.ndarray,
               building_class_ids: list[int] = None) -> list[Landmark]:
        """
        Extract building corner landmarks.

        Detects corners by finding points where surface normal changes abruptly.
        """
        if building_class_ids is None:
            building_class_ids = [2, 3]  # building, wall

        # Get building points
        building_mask = np.isin(semantic_labels, building_class_ids)
        building_points = points[building_mask]

        if len(building_points) < 20:
            return []

        # Estimate normals using local PCA
        normals = self._estimate_normals(building_points)

        # Find corners: points where normal differs significantly from neighbors
        corners = []
        tree = cKDTree(building_points)

        for _i, point in enumerate(building_points):
            # Find neighbors
            neighbor_indices = tree.query_ball_point(point, r=self.search_radius)

            if len(neighbor_indices) < 5:
                continue

            neighbor_normals = normals[neighbor_indices]

            # Check normal variation
            neighbor_normals.mean(axis=0)
            normal_std = neighbor_normals.std(axis=0)

            # High variation indicates corner
            if np.linalg.norm(normal_std) > 0.3:
                # Calculate corner angle
                angle = self._estimate_corner_angle(neighbor_normals)

                if self.min_angle <= angle <= self.max_angle:
                    corners.append(Landmark(
                        landmark_type='corner',
                        position=point,
                        angle=angle,
                        confidence=min(len(neighbor_indices) / 20.0, 1.0)
                    ))

        # Non-maximum suppression (remove nearby corners)
        corners = self._nms_corners(corners, min_distance=3.0)

        return corners

    def _estimate_normals(self, points: np.ndarray, k: int = 10) -> np.ndarray:
        """Estimate normals using PCA on local neighborhoods."""
        tree = cKDTree(points)
        normals = np.zeros_like(points)

        for i, point in enumerate(points):
            _, indices = tree.query(point, k=k)
            neighbors = points[indices]

            # PCA
            centered = neighbors - neighbors.mean(axis=0)
            _, _, Vt = np.linalg.svd(centered)
            normals[i] = Vt[-1, :]  # Last component is normal

        return normals

    def _estimate_corner_angle(self, normals: np.ndarray) -> float:
        """Estimate corner angle from normal directions."""
        # Find two dominant normal directions
        from sklearn.cluster import KMeans

        if len(normals) < 4:
            return 90.0

        kmeans = KMeans(n_clusters=2, random_state=42, n_init=1)
        kmeans.fit(normals)

        # Angle between two cluster centers
        n1, n2 = kmeans.cluster_centers_
        angle = np.arccos(np.clip(np.abs(np.dot(n1, n2)), -1, 1)) * 180 / np.pi

        return angle

    def _nms_corners(self, corners: list[Landmark], min_distance: float) -> list[Landmark]:
        """Non-maximum suppression for corners."""
        if not corners:
            return []

        # Sort by confidence
        corners = sorted(corners, key=lambda x: x.confidence, reverse=True)

        kept = []
        for corner in corners:
            # Check distance to already kept corners
            too_close = False
            for kept_corner in kept:
                dist = np.linalg.norm(corner.position - kept_corner.position)
                if dist < min_distance:
                    too_close = True
                    break

            if not too_close:
                kept.append(corner)

        return kept


class CurbLineExtractor:
    """Extract curb line features."""

    def __init__(self,
                 step_height_threshold: float = 0.05,  # 5cm step
                 line_fit_threshold: float = 0.10):   # 10cm line fit
        self.step_height_threshold = step_height_threshold
        self.line_fit_threshold = line_fit_threshold

    def extract(self,
               points: np.ndarray,
               semantic_labels: np.ndarray,
               road_class_id: int = 0,
               sidewalk_class_id: int = 1) -> list[Landmark]:
        """
        Extract curb lines between road and sidewalk.

        Returns line segments represented by center point and direction.
        """
        # Get road and sidewalk points
        road_mask = semantic_labels == road_class_id
        sidewalk_mask = semantic_labels == sidewalk_class_id

        road_points = points[road_mask]
        sidewalk_points = points[sidewalk_mask]

        if len(road_points) < 10 or len(sidewalk_points) < 10:
            return []

        # Find boundary points
        # Points near road/sidewalk boundary with height difference
        curb_points = self._find_curb_points(road_points, sidewalk_points)

        if len(curb_points) < 5:
            return []

        # Fit line segments using RANSAC
        lines = self._fit_line_segments(curb_points)

        return lines

    def _find_curb_points(self, road_points: np.ndarray, sidewalk_points: np.ndarray) -> np.ndarray:
        """Find points at curb boundary."""
        # Simple approach: find sidewalk points near road points
        tree = cKDTree(road_points[:, :2])  # 2D tree

        curb_points = []
        for sw_point in sidewalk_points:
            # Find nearest road point
            dist, idx = tree.query(sw_point[:2])

            # Check height difference
            height_diff = sw_point[2] - road_points[idx, 2]

            if height_diff > self.step_height_threshold and dist < 1.0:
                # This sidewalk point is at curb
                curb_points.append(sw_point)

        return np.array(curb_points) if curb_points else np.zeros((0, 3))

    def _fit_line_segments(self, points: np.ndarray) -> list[Landmark]:
        """Fit line segments to curb points using RANSAC."""
        if len(points) < 5:
            return []

        lines = []
        remaining = points.copy()

        while len(remaining) > 5:
            # Fit line to remaining points
            line_point, line_dir, inliers = self._ransac_line(remaining)

            if len(inliers) < 5:
                break

            # Create landmark
            lines.append(Landmark(
                landmark_type='curb_line',
                position=line_point,
                direction=line_dir,
                confidence=len(inliers) / len(points)
            ))

            # Remove inliers
            remaining = remaining[~inliers]

        return lines

    def _ransac_line(self, points: np.ndarray) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
        """RANSAC line fitting."""
        best_inliers = np.array([])
        best_point = points[0]
        best_dir = np.array([1, 0, 0])

        for _ in range(50):  # RANSAC iterations
            if len(points) < 2:
                break

            # Sample 2 points
            idx = np.random.choice(len(points), 2, replace=False)
            p1, p2 = points[idx]

            # Line direction
            direction = p2 - p1
            direction = direction / (np.linalg.norm(direction) + 1e-6)

            # Find inliers
            to_points = points - p1
            projections = np.dot(to_points, direction)
            closest_points = p1 + np.outer(projections, direction)
            distances = np.linalg.norm(points - closest_points, axis=1)

            inliers = distances < self.line_fit_threshold

            if np.sum(inliers) > np.sum(best_inliers):
                best_inliers = inliers
                best_point = (p1 + p2) / 2
                best_dir = direction

        return best_point, best_dir, best_inliers


class SemanticFeatureExtractor:
    """
    Main feature extractor combining all landmark types.

    Extracts stable semantic features for ICP registration.
    """

    def __init__(self):
        self.pole_extractor = PoleExtractor()
        self.corner_extractor = CornerExtractor()
        self.curb_extractor = CurbLineExtractor()

    def extract(self,
               points: np.ndarray,
               semantic_labels: np.ndarray) -> SemanticFeatures:
        """
        Extract all semantic features from point cloud.

        Args:
            points: Nx3 point cloud
            semantic_labels: N array of semantic class labels

        Returns:
            SemanticFeatures with poles, signs, corners, curb lines
        """
        # Extract each feature type
        poles = self.pole_extractor.extract(points, semantic_labels)

        corners = self.corner_extractor.extract(points, semantic_labels)

        curb_lines = self.curb_extractor.extract(points, semantic_labels)

        # Separate traffic signs from poles
        signs = [p for p in poles if p.height and p.height < 3.0]  # Signs are shorter
        poles = [p for p in poles if p.height and p.height >= 3.0]  # Taller = pole

        return SemanticFeatures(
            poles=poles,
            signs=signs,
            building_corners=corners,
            curb_lines=curb_lines
        )


# Convenience function
def extract_landmarks(points: np.ndarray, semantic_labels: np.ndarray) -> SemanticFeatures:
    """
    Convenience function to extract semantic landmarks.

    Usage:
        features = extract_landmarks(points, labels)
        print(f"Found {len(features.poles)} poles, {len(features.signs)} signs")

        # Use for ICP
        landmarks_3d = features.get_all_positions()
    """
    extractor = SemanticFeatureExtractor()
    return extractor.extract(points, semantic_labels)

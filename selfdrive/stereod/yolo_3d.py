#!/usr/bin/env python3
"""
YOLO 3D Estimator - Convert 2D YOLO detections to 3D bounding boxes.

Uses stereo depth to estimate 3D position, size, and heading from 2D detections.
"""

import numpy as np
from dataclasses import dataclass


@dataclass
class Detection3D:
    """3D object detection result."""
    # 2D info
    class_name: str
    confidence: float
    bbox_2d: tuple[int, int, int, int]  # x1, y1, x2, y2
    
    # 3D info
    center_3d: np.ndarray  # [x, y, z] in camera frame
    size_3d: np.ndarray    # [length, width, height] in meters
    yaw: float             # Heading angle (radians)
    
    # Quality metrics
    depth_confidence: float  # How reliable is the depth estimate?
    
    def to_dict(self) -> dict:
        """Convert to dictionary for serialization."""
        return {
            'class': self.class_name,
            'confidence': float(self.confidence),
            'bbox_2d': [int(x) for x in self.bbox_2d],
            'center_3d': [float(x) for x in self.center_3d],
            'size_3d': [float(x) for x in self.size_3d],
            'yaw': float(self.yaw),
            'depth_confidence': float(self.depth_confidence)
        }


class YOLO3DEstimator:
    """
    Estimate 3D bounding boxes from 2D YOLO detections and stereo depth.
    
    Uses class-specific size priors and depth map to estimate:
    - 3D position (center point)
    - 3D dimensions (from class priors + depth)
    - Heading (from box aspect ratio + depth gradient)
    """
    
    # Class-specific 3D size priors (length, width, height) in meters
    # These are average sizes, refined per-detection
    CLASS_SIZE_PRIORS = {
        'car': np.array([4.5, 2.0, 1.8]),
        'truck': np.array([8.0, 2.5, 3.0]),
        'bus': np.array([12.0, 2.5, 3.5]),
        'motorcycle': np.array([2.0, 0.8, 1.5]),
        'bicycle': np.array([1.8, 0.6, 1.2]),
        'person': np.array([0.5, 0.5, 1.7]),
        'rider': np.array([0.8, 0.5, 1.7]),
    }
    
    # Minimum depth confidence for valid 3D estimate
    MIN_DEPTH_CONFIDENCE = 0.3
    
    def __init__(self, 
                 focal_length_px: float = 700.0,
                 image_width: int = 640,
                 image_height: int = 480):
        """
        Args:
            focal_length_px: Camera focal length in pixels
            image_width: Camera image width
            image_height: Camera image height
        """
        self.focal_length = focal_length_px
        self.image_width = image_width
        self.image_height = image_height
        self.cx = image_width / 2.0
        self.cy = image_height / 2.0
    
    def estimate_3d(self,
                   yolo_detections: list[dict],
                   depth_map: np.ndarray,
                   depth_confidence: np.ndarray | None = None) -> list[Detection3D]:
        """
        Convert 2D YOLO detections to 3D bounding boxes.
        
        Args:
            yolo_detections: list of YOLO detection dicts
                Each dict has: {'class': str, 'confidence': float, 
                               'bbox': [x1, y1, x2, y2]}
            depth_map: HxW stereo depth map
            depth_confidence: Optional HxW confidence map
        
        Returns:
            list of Detection3D objects
        """
        detections_3d = []
        
        for det in yolo_detections:
            det_3d = self._estimate_single(det, depth_map, depth_confidence)
            if det_3d is not None:
                detections_3d.append(det_3d)
        
        return detections_3d
    
    def _estimate_single(self,
                        det_2d: dict,
                        depth_map: np.ndarray,
                        depth_confidence: np.ndarray | None) -> Detection3D | None:
        """Estimate 3D box for single 2D detection."""
        
        class_name = det_2d.get('class', '').lower()
        bbox = det_2d.get('bbox', [0, 0, 0, 0])
        conf_2d = det_2d.get('confidence', 0.0)
        
        x1, y1, x2, y2 = [int(c) for c in bbox]
        
        # Clip to image bounds
        x1 = max(0, x1)
        y1 = max(0, y1)
        x2 = min(self.image_width, x2)
        y2 = min(self.image_height, y2)
        
        if x2 <= x1 or y2 <= y1:
            return None
        
        # Get depth at box center
        cx_2d = (x1 + x2) // 2
        cy_2d = (y1 + y2) // 2
        
        # Sample depth in ROI (median for robustness)
        roi_depths = depth_map[y1:y2, x1:x2]
        valid_depths = roi_depths[roi_depths > 0.5]  # Filter invalid
        
        if len(valid_depths) == 0:
            return None
        
        z_depth = np.median(valid_depths)
        
        # Depth confidence
        if depth_confidence is not None:
            roi_conf = depth_confidence[y1:y2, x1:x2]
            depth_conf = float(np.median(roi_conf[roi_conf > 0]))
        else:
            # Estimate from depth variance
            depth_std = np.std(valid_depths)
            depth_conf = 1.0 / (1.0 + depth_std)
        
        if depth_conf < self.MIN_DEPTH_CONFIDENCE:
            return None
        
        # Project to 3D
        # x = (u - cx) * z / f
        # y = (v - cy) * z / f
        # z = depth
        x_3d = (cx_2d - self.cx) * z_depth / self.focal_length
        y_3d = (cy_2d - self.cy) * z_depth / self.focal_length
        
        center_3d = np.array([x_3d, y_3d, z_depth])
        
        # Estimate 3D size
        size_3d = self._estimate_size(class_name, bbox, z_depth)
        
        # Estimate heading (yaw)
        yaw = self._estimate_yaw(bbox, depth_map, x1, y1, x2, y2)
        
        return Detection3D(
            class_name=class_name,
            confidence=conf_2d,
            bbox_2d=(x1, y1, x2, y2),
            center_3d=center_3d,
            size_3d=size_3d,
            yaw=yaw,
            depth_confidence=depth_conf
        )
    
    def _estimate_size(self, 
                      class_name: str, 
                      bbox: tuple[int, ...], 
                      depth: float) -> np.ndarray:
        """
        Estimate 3D size from class prior and 2D box.
        
        The apparent size in 2D relates to actual 3D size through depth.
        """
        # Get class prior
        prior = self.CLASS_SIZE_PRIORS.get(class_name, np.array([2.0, 1.0, 1.5]))
        
        # 2D box dimensions
        box_width_2d = bbox[2] - bbox[0]
        box_height_2d = bbox[3] - bbox[1]
        
        # Estimate actual size from 2D projection
        # width_3d ≈ box_width_2d * depth / focal_length
        estimated_width = box_width_2d * depth / self.focal_length
        estimated_height = box_height_2d * depth / self.focal_length
        
        # Blend prior with estimate
        # Use prior for length (not visible in 2D)
        # Use estimate for width/height (visible in 2D)
        length = prior[0]  # Prior (not observable from front/back)
        
        # Width: blend prior with estimate
        width = 0.7 * prior[1] + 0.3 * estimated_width
        
        # Height: use estimate (reliable from 2D)
        height = 0.5 * prior[2] + 0.5 * estimated_height
        
        # Sanity checks
        width = np.clip(width, prior[1] * 0.5, prior[1] * 1.5)
        height = np.clip(height, prior[2] * 0.5, prior[2] * 1.5)
        
        return np.array([length, width, height])
    
    def _estimate_yaw(self,
                     bbox: tuple[int, ...],
                     depth_map: np.ndarray,
                     x1: int, y1: int, x2: int, y2: int) -> float:
        """
        Estimate heading (yaw) from box aspect ratio and depth gradient.
        
        Simple heuristic: Wide box → facing camera (yaw ≈ 0)
                          Tall box → side view (yaw ≈ ±90°)
        """
        box_width = x2 - x1
        box_height = y2 - y1
        aspect_ratio = box_height / (box_width + 1e-6)
        
        # Estimate yaw from aspect ratio
        # aspect = 1.0 → facing camera (yaw = 0)
        # aspect = 2.0 → side view (yaw = 90°)
        yaw_estimate = np.clip((aspect_ratio - 1.0) * 45.0, -90.0, 90.0)
        
        # Refine with depth gradient if available
        if depth_map is not None and x2 > x1 + 10:
            left_depth = np.median(depth_map[y1:y2, x1:x1+10])
            right_depth = np.median(depth_map[y1:y2, x2-10:x2])
            
            depth_diff = right_depth - left_depth
            yaw_from_depth = np.arctan2(depth_diff, box_width * 0.1) * 180 / np.pi
            
            # Blend estimates
            yaw = 0.6 * yaw_estimate + 0.4 * yaw_from_depth
        else:
            yaw = yaw_estimate
        
        return np.radians(yaw)
    
    def filter_dynamic_objects(self,
                              points: np.ndarray,
                              detections_3d: list[Detection3D],
                              margin: float = 0.5) -> np.ndarray:
        """
        Remove points inside 3D bounding boxes of dynamic objects.
        
        Args:
            points: Nx3 array of point cloud
            detections_3d: 3D object detections
            margin: Extra margin around boxes (meters)
        
        Returns:
            mask: Boolean array (True = keep point)
        """
        keep_mask = np.ones(len(points), dtype=bool)
        
        for det in detections_3d:
            # Skip low-confidence detections
            if det.depth_confidence < 0.5:
                continue
            
            # Check if point is inside 3D box (with margin)
            # Simple axis-aligned box check
            half_size = det.size_3d / 2 + margin
            
            dx = np.abs(points[:, 0] - det.center_3d[0])
            dy = np.abs(points[:, 1] - det.center_3d[1])
            dz = np.abs(points[:, 2] - det.center_3d[2])
            
            in_box = (dx < half_size[0]) & (dy < half_size[1]) & (dz < half_size[2])
            
            keep_mask &= ~in_box
        
        return keep_mask


# Convenience function
def estimate_3d_boxes(yolo_detections: list[dict],
                     depth_map: np.ndarray,
                     depth_confidence: np.ndarray | None = None) -> list[Detection3D]:
    """
    Convenience function to convert YOLO 2D to 3D.
    
    Usage:
        detections_3d = estimate_3d_boxes(yolo_dets, depth_map)
        static_points = filter_points_in_boxes(points, detections_3d)
    """
    estimator = YOLO3DEstimator()
    return estimator.estimate_3d(yolo_detections, depth_map, depth_confidence)

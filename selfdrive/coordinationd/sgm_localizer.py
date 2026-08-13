#!/usr/bin/env python3
"""
sgm_localizer.py - SGM stereo pointcloud matching for coordinationd.

Provides ICP-based pointcloud matching to align live stereo
pointclouds with pre-built SGM map tiles for localization.
"""

from __future__ import annotations

import time
import struct
import hashlib
from pathlib import Path
from dataclasses import dataclass
from collections import deque

import numpy as np

from openpilot.common.params import Params
from openpilot.common.swaglog import cloudlog


@dataclass
class SGMMapTile:
    """SGM map tile containing pre-recorded pointcloud."""
    tile_id: str
    lat: float
    lon: float
    points: np.ndarray
    timestamp: float
    source: str


@dataclass
class PointCloudFrame:
    """Live pointcloud frame from stereo camera."""
    timestamp: float
    points: np.ndarray


@dataclass
class ICPMatchResult:
    """Result of ICP pointcloud matching."""
    dx: float  # X translation in meters
    dy: float  # Y translation in meters
    dz: float  # Z translation in meters
    dyaw: float  # Yaw rotation in radians
    confidence: float  # Match confidence (0-1)
    inliers: int  # Number of inlier points
    iterations: int  # Number of ICP iterations performed


class SGMLocalizerModule:
    """
    SGM stereo pointcloud matching module.

    Performs ICP (Iterative Closest Point) matching between live
    stereo pointclouds and pre-built SGM map tiles for localization.
    """

    # Configuration constants
    MAP_RANGE_M = 100.0  # Map range in meters
    MIN_POINTS_FOR_MATCH = 100  # Minimum points needed for matching
    MAX_POINT_DISTANCE = 50.0  # Maximum point distance to consider
    ICP_MAX_ITERATIONS = 20  # Maximum ICP iterations
    ICP_TOLERANCE = 0.01  # ICP convergence tolerance (meters)
    CONFIDENCE_THRESHOLD = 0.7  # Minimum confidence for valid match
    MAX_ICP_DISTANCE = 0.5  # Maximum distance for point correspondence

    def __init__(self, params: Params | None = None):
        """
        Initialize SGM localizer module.

        Args:
            params: Params object for configuration (creates new if None)
        """
        if params is None:
            params = Params()

        self.enabled = params.get_bool("EOPSGMLocalizerEnabled")
        map_path = params.get("EOPSGMMapPath") or b"/data/maps/sgm"
        self.map_path = Path(map_path.decode())

        # Map tile cache
        self.map_tiles: dict[str, SGMMapTile] = {}
        self.current_tile_id: str | None = None

        # Pointcloud history for temporal consistency
        self.pc_history: deque = deque(maxlen=5)

        # Statistics
        self.frame_count = 0
        self.match_count = 0
        self.reject_count = 0

    def _get_tile_id(self, lat: float, lon: float) -> str:
        """
        Generate tile ID from lat/lon.

        Uses a hash of quantized coordinates for consistent tile IDs.

        Args:
            lat: Latitude
            lon: Longitude

        Returns:
            6-character tile ID string
        """
        lat_key = int((lat + 90) * 100)
        lon_key = int((lon + 180) * 100)
        data = struct.pack('!ii', lat_key, lon_key)
        return hashlib.md5(data).hexdigest()[:6]

    def _load_map_tile(self, tile_id: str) -> SGMMapTile | None:
        """
        Load SGM map tile from disk.

        Args:
            tile_id: Tile ID to load

        Returns:
            SGMMapTile if found, None otherwise
        """
        # Check cache first
        if tile_id in self.map_tiles:
            return self.map_tiles[tile_id]

        # Try to load from disk
        tile_path = self.map_path / f"{tile_id}.npy"
        if tile_path.exists():
            try:
                data = np.load(tile_path, allow_pickle=True).item()
                tile = SGMMapTile(
                    tile_id=tile_id, lat=data['lat'], lon=data['lon'],
                    points=data['points'], timestamp=data['timestamp'], source='map'
                )
                self.map_tiles[tile_id] = tile
                cloudlog.debug(f"SGM: loaded tile {tile_id}")
                return tile
            except Exception as e:
                cloudlog.error(f"SGM: failed to load tile {tile_id}: {e}")

        return None

    def extract_pointcloud(self, pc_msg, sg_msg) -> PointCloudFrame | None:
        """
        Extract pointcloud from cereal messages.

        Args:
            pc_msg: pointcloudProcessed message (or None)
            sg_msg: stereoGround message (or None)

        Returns:
            PointCloudFrame if valid points found, None otherwise
        """
        timestamp = time.monotonic()
        points_list = []

        # Extract from pointcloudProcessed message
        if pc_msg and pc_msg.validPoints > 0:
            try:
                pts = np.array([[p.x, p.y, p.z] for p in pc_msg.points], dtype=np.float32)
                distances = np.linalg.norm(pts[:, :2], axis=1)
                mask = distances < self.MAX_POINT_DISTANCE
                points_list.append(pts[mask])
            except Exception as e:
                cloudlog.debug(f"SGM: failed to decode pointcloud: {e}")

        # Fallback: extract from stereoGround road geometry
        if not points_list and sg_msg and hasattr(sg_msg, 'roadGeometry'):
            rg = sg_msg.roadGeometry
            for edge in rg.roadEdges:
                if hasattr(edge, 'x') and hasattr(edge, 'y'):
                    x, y = np.array(edge.x), np.array(edge.y)
                    z = np.zeros_like(x)
                    points_list.append(np.column_stack([x, y, z]))

        if not points_list:
            return None

        # Combine and downsample if needed
        all_points = np.vstack(points_list)
        if len(all_points) > 1000:
            indices = np.random.choice(len(all_points), 1000, replace=False)
            all_points = all_points[indices]

        return PointCloudFrame(timestamp=timestamp, points=all_points)

    def match_pointcloud(self, live_pc: PointCloudFrame,
                         map_tile: SGMMapTile) -> ICPMatchResult | None:
        """
        Perform ICP matching between live pointcloud and map tile.

        Uses a simple iterative closest point algorithm to find the
        optimal transformation aligning live points to map points.

        Args:
            live_pc: Live pointcloud frame
            map_tile: Map tile to match against

        Returns:
            ICPMatchResult if successful, None otherwise
        """
        self.frame_count += 1

        # Validate inputs
        if len(live_pc.points) < self.MIN_POINTS_FOR_MATCH:
            return None
        if len(map_tile.points) < self.MIN_POINTS_FOR_MATCH:
            return None

        # Initialize with centroid alignment
        live_center = np.mean(live_pc.points, axis=0)
        map_center = np.mean(map_tile.points, axis=0)
        initial_guess = map_center - live_center

        # Build transformation matrix
        transform = np.eye(4)
        transform[:3, 3] = initial_guess

        prev_error = float('inf')
        inliers = 0

        # ICP iterations
        for _ in range(self.ICP_MAX_ITERATIONS):
            # Transform live points
            live_homo = np.column_stack([live_pc.points, np.ones(len(live_pc.points))])
            transformed = (transform @ live_homo.T).T[:, :3]

            # Find nearest neighbors
            distances = np.linalg.norm(
                transformed[:, np.newaxis, :] - map_tile.points[np.newaxis, :, :], axis=2
            )
            min_distances = np.min(distances, axis=1)

            # Identify inliers
            inlier_mask = min_distances < self.MAX_ICP_DISTANCE
            inliers = np.sum(inlier_mask)

            if inliers < self.MIN_POINTS_FOR_MATCH:
                break

            # Compute correspondence
            live_inliers = transformed[inlier_mask]
            map_indices = np.argmin(distances[inlier_mask], axis=1)
            map_inliers = map_tile.points[map_indices]

            # Compute translation (simplified - no rotation for now)
            translation = np.mean(map_inliers - live_inliers, axis=0)
            transform[:3, 3] += translation

            # Check convergence
            error = np.mean(min_distances[inlier_mask])
            if abs(prev_error - error) < self.ICP_TOLERANCE:
                break
            prev_error = error

        # Calculate confidence
        confidence = min(1.0, inliers / len(live_pc.points))

        if confidence < self.CONFIDENCE_THRESHOLD:
            self.reject_count += 1
            return None

        self.match_count += 1

        return ICPMatchResult(
            dx=float(transform[0, 3]), dy=float(transform[1, 3]),
            dz=float(transform[2, 3]), dyaw=0.0, confidence=confidence,
            inliers=inliers, iterations=_ + 1
        )

    def get_stats(self) -> dict:
        """Get module statistics."""
        return {
            'frames': self.frame_count,
            'matches': self.match_count,
            'rejects': self.reject_count,
            'match_rate': self.match_count / max(self.frame_count, 1)
        }

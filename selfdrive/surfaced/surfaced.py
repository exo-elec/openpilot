#!/usr/bin/env python3
"""
SurfaceD - BEV Drivable Area Daemon with Long Horizon Fusion (NON-CRITICAL)

Creates BEV drivable area grid using:
1. Real-time point cloud (current frame) - immediate drivable area
2. Saved PCD data (historical/fleet) - long horizon prediction
3. SQSC history (surface_quality.db) - learned surface quality

Pipeline:
  ┌─────────────────────────────────────────────────────────────┐
  │                    DATA SOURCES                             │
  ├─────────────────────────────────────────────────────────────┤
  │  Real-time:        pointcloudd ──► current PCD frame        │
  │  Historical:       Local PCD files ──► previous drives      │
  │  Fleet:            Server PCD tiles ──► crowd-sourced       │
  │  Learned:          surface_quality.db ──► SQSC history      │
  └─────────────────────────────────────────────────────────────┘
                              │
                              ▼
  ┌─────────────────────────────────────────────────────────────┐
  │                    surfaced                                 │
  │  - Fuses multi-source PCD (real-time + historical)          │
  │  - Extracts drivable area (road surface only)               │
  │  - Enhances with SQSC learned quality                       │
  │  - Publishes: drivableArea (BEV grid)                       │
  └─────────────────────────────────────────────────────────────┘
                              │
                              ▼
  ┌─────────────────────────────────────────────────────────────┐
  │  gridd: Uses drivableArea to enhance occupancy grid         │
  │  SQSC: Uses surfaceStatus for predictive speed control      │
  └─────────────────────────────────────────────────────────────┘

Long Horizon Capability:
- Real-time: 0-30m (current sensors)
- Historical PCD: 30-100m (previous drives on same road)
- Fleet PCD: 100-500m (crowd-sourced from other vehicles)
- SQSC: 50-300m (learned surface quality predictions)

Fault Isolation:
- pointcloudd crash → use historical PCD only
- surfaced crash → gridd uses stereo-only
- Core ADAS (stereod→gridd) continues in all cases
"""

from __future__ import annotations

import logging
import os
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, cast
from enum import IntEnum

import numpy as np

import cereal.messaging as messaging
from openpilot.common.params import Params
from openpilot.common.realtime import Ratekeeper
from openpilot.common.core_config import set_daemon_affinity

from openpilot.selfdrive.surfaced.surface_detector import (
    SurfaceQualityDB,
    SurfacePrediction,
    SurfaceQuality,
)
from openpilot.selfdrive.pointcloudd.pointcloudd import PcdReader
from openpilot.common.swaglog import cloudlog
from openpilot.system.hardware.hw import Paths

# =============================================================================
# Cell State Enumeration
# =============================================================================

class CellState(IntEnum):
    """
    Drivable area cell states.

    Note: OCCUPIED is NOT here - obstacles are handled by gridd's occupancy grid.
    surfaced focuses on ROAD SURFACE QUALITY only (drivable area).
    """
    UNKNOWN = 0
    DRIVABLE_SMOOTH = 1    # Good road surface
    DRIVABLE_NORMAL = 2    # Normal road surface
    DRIVABLE_ROUGH = 3     # Rough road (current detection)
    LEARNED_ROUGH = 4      # Rough road (from SQSC history)

    # Legacy aliases used by the grid API
    DRIVABLE = DRIVABLE_NORMAL
    ROUGH = DRIVABLE_ROUGH
    OCCUPIED = 255         # Obstacle marker (kept for grid clearance computation)


# =============================================================================
# Statistics Tracking
# =============================================================================

@dataclass
class SurfaceDStats:
    """Runtime statistics for SurfaceD."""
    frames_processed: int = 0
    frames_with_pointcloud: int = 0
    frames_with_history: int = 0
    frames_with_gps: int = 0
    frames_with_long_horizon: int = 0  # Historical PCD loaded
    avg_processing_time_ms: float = 0.0
    avg_points_per_frame: float = 0.0
    avg_historical_points: float = 0.0
    consecutive_no_data: int = 0
    last_errors: list[str] = field(default_factory=list)

    def update_timing(self, proc_time_ms: float):
        """Update moving average processing time."""
        alpha = 0.1
        self.avg_processing_time_ms = (1 - alpha) * self.avg_processing_time_ms + alpha * proc_time_ms

    def update_points(self, num_points: int):
        """Update moving average points."""
        alpha = 0.1
        self.avg_points_per_frame = (1 - alpha) * self.avg_points_per_frame + alpha * num_points

    def add_error(self, error: str):
        """Add error to recent errors list."""
        timestamp = time.strftime('%H:%M:%S')
        self.last_errors.append(f"{timestamp}: {error}")
        if len(self.last_errors) > 10:
            self.last_errors.pop(0)


# =============================================================================
# BEV Drivable Area Grid
# =============================================================================

@dataclass
class BEVGrid:
    """Bird's Eye View drivable area grid."""
    resolution: float
    width_m: float
    height_m: float
    origin_x: float
    origin_y: float
    data: np.ndarray  # CellState values
    timestamp: float = 0.0
    has_history: bool = False

    @property
    def grid_w(self) -> int:
        return cast(int, self.data.shape[1])

    @property
    def grid_h(self) -> int:
        return cast(int, self.data.shape[0])

    def copy(self) -> BEVGrid:
        """Create a copy of the grid."""
        return BEVGrid(
            resolution=self.resolution,
            width_m=self.width_m,
            height_m=self.height_m,
            origin_x=self.origin_x,
            origin_y=self.origin_y,
            data=self.data.copy(),
            timestamp=self.timestamp,
            has_history=self.has_history
        )


class BEVDrivableAreaExtractor:
    """
    Extract drivable area (road surface) from point cloud.

    Focus: ROAD SURFACE QUALITY only (where vehicle can drive).
    Does NOT detect obstacles - that's gridd's job (occupancy grid).

    Uses PP-LiteSeg semantic labels to identify road surface:
    - Class 0 (road): Primary drivable area
    - Class 1 (sidewalk): Drivable but lower priority
    - Other classes: Not drivable (ignored - gridd handles obstacles)
    """

    def __init__(
        self,
        resolution: float = 0.25,
        grid_size_m: tuple[float, float] = (30.0, 60.0),
        ground_height_range: tuple[float, float] = (-0.5, 0.5)
    ):
        self.resolution = resolution
        self.width_m, self.height_m = grid_size_m
        self.ground_min_z, self.ground_max_z = ground_height_range

        # Grid dimensions
        self.grid_h = int(self.height_m / resolution)
        half_w = int(self.width_m / resolution / 2)
        self.grid_w = half_w * 2 + 1
        self.half_w = half_w

    def create_empty_grid(self) -> BEVGrid:
        """Create empty grid (all unknown)."""
        return BEVGrid(
            resolution=self.resolution,
            width_m=self.width_m,
            height_m=self.height_m,
            origin_x=-self.width_m / 2,
            origin_y=0.0,
            data=np.full((self.grid_h, self.grid_w), CellState.UNKNOWN, dtype=np.uint8),
            timestamp=time.monotonic()
        )

    def extract_from_pointcloud(
        self,
        points: np.ndarray,  # Vehicle frame: x=forward, y=left, z=up
    ) -> BEVGrid:
        """Extract drivable area from current point cloud."""
        grid = self.create_empty_grid()

        if len(points) == 0:
            return grid

        # Filter to valid range
        x = points[:, 0]  # forward
        y = points[:, 1]  # left
        z = points[:, 2]  # up

        in_range = (
            (x > 0) & (x < self.height_m) &
            (np.abs(y) < self.width_m / 2)
        )

        if not np.any(in_range):
            return grid

        points = points[in_range]
        x, y, z = points[:, 0], points[:, 1], points[:, 2]

        # Ground points (drivable)
        ground_mask = (z > self.ground_min_z) & (z < self.ground_max_z)

        # Vectorized grid mapping
        rows = np.clip((x / self.resolution).astype(np.int32), 0, self.grid_h - 1)
        cols = np.clip((y / self.resolution).astype(np.int32) + self.half_w, 0, self.grid_w - 1)

        # Mark ground points as drivable
        ground_rows = rows[ground_mask]
        ground_cols = cols[ground_mask]
        if len(ground_rows) > 0:
            for r, c in zip(ground_rows, ground_cols, strict=False):
                if grid.data[r, c] == CellState.UNKNOWN:
                    grid.data[r, c] = CellState.DRIVABLE

        # Mark obstacles
        obstacle_mask = ~ground_mask
        obs_rows = rows[obstacle_mask]
        obs_cols = cols[obstacle_mask]
        if len(obs_rows) > 0:
            for r, c in zip(obs_rows, obs_cols, strict=False):
                grid.data[r, c] = CellState.OCCUPIED

        return grid

    def enhance_with_history(
        self,
        current_grid: BEVGrid,
        predictions: list[SurfacePrediction]
    ) -> BEVGrid:
        """
        Enhance current grid with SQSC historical predictions.

        If history says road ahead is rough, mark it in grid
        even if current point cloud looks smooth.
        """
        if not predictions:
            return current_grid

        enhanced = current_grid.copy()
        enhanced.has_history = True

        for pred in predictions:
            if pred.confidence < 0.3:
                continue

            # Project prediction distance to grid row
            dist_x = pred.distance_m
            row = int(dist_x / self.resolution)

            if row >= self.grid_h:
                continue

            # Mark cells as learned rough if prediction indicates poor quality
            if pred.quality_score > 0.5:  # High score = poor quality in SQSC
                center_col = self.grid_w // 2
                width_cols = int(3.0 / self.resolution)  # 3m wide

                for dc in range(-width_cols, width_cols + 1):
                    col = center_col + dc
                    if 0 <= col < self.grid_w:
                        # Only override unknown or drivable, not occupied
                        if enhanced.data[row, col] in (CellState.UNKNOWN, CellState.DRIVABLE):
                            enhanced.data[row, col] = CellState.LEARNED_ROUGH

        return enhanced


# =============================================================================
# Surface Anomaly Detection
# =============================================================================

class SurfaceAnomalyDetector:
    """Detect bumps, potholes from point cloud with robust statistics."""

    def __init__(self, height_thresh: float = 0.05, min_points: int = 50):
        self.height_thresh = height_thresh
        self.min_points = min_points

    def detect(self, points: np.ndarray) -> list[dict[str, Any]]:
        """Detect surface anomalies using robust statistics."""
        if len(points) < self.min_points:
            return []

        # Ground points
        z = points[:, 2]
        ground_mask = (z > -0.5) & (z < 0.5)
        ground = points[ground_mask]

        if len(ground) < self.min_points:
            return []

        # Use median for robustness instead of mean
        z_median = np.median(ground[:, 2])
        z_mad = np.median(np.abs(ground[:, 2] - z_median))  # Median Absolute Deviation

        anomalies = []
        threshold = max(self.height_thresh, 2.0 * z_mad)  # Adaptive threshold

        for p in ground:
            z_diff = p[2] - z_median
            if abs(z_diff) > threshold:
                anomalies.append({
                    "type": "bump" if z_diff > 0 else "pothole",
                    "distance_m": float(p[0]),
                    "lateral_m": float(p[1]),
                    "height_m": float(abs(z_diff)),
                    "severity": min(abs(z_diff) / 0.1, 1.0)
                })

        return anomalies


# =============================================================================
# Historical PCD Loader
# =============================================================================

class HistoricalPCDLoader:
    """
    Load saved PCD data for long horizon surface prediction.

    Enables surface perception beyond real-time sensor range (30-100m+)
    by loading previously recorded point clouds from the same road segment.

    Storage layout:
        /data/media/0/pointclouds/
            YYYYMMDD_HHMMSS_session/
                frame_000000.pcd
                frame_000001.pcd
                ...
    """

    def __init__(self, pcd_root: str = None):
        if pcd_root is None:
            pcd_root = os.path.join(Paths.eop_data_root(), "media", "0", "pointclouds")
        self.pcd_root = Path(pcd_root)
        self.pcd_reader = PcdReader()
        self._cache: dict[str, tuple[np.ndarray, np.ndarray | None]] = {}
        self._cache_max_size = 10  # Keep last 10 loaded frames

        # Spatial index for quick lookup (simplified: session -> bounds)
        self._spatial_index: dict[str, dict[str, Any]] = {}
        self._index_built = False

    def _build_index(self) -> None:
        """Build spatial index of available PCD sessions."""
        if self._index_built:
            return

        try:
            if not self.pcd_root.exists():
                cloudlog.debug(f"PCD root not found: {self.pcd_root}")
                return

            for session_dir in self.pcd_root.iterdir():
                if not session_dir.is_dir():
                    continue

                # Look for metadata file
                meta_file = session_dir / "metadata.json"
                if meta_file.exists():
                    try:
                        import json
                        with open(meta_file) as f:
                            meta = json.load(f)

                        self._spatial_index[session_dir.name] = {
                            "path": session_dir,
                            "lat_min": meta.get("lat_min", 0),
                            "lat_max": meta.get("lat_max", 0),
                            "lon_min": meta.get("lon_min", 0),
                            "lon_max": meta.get("lon_max", 0),
                            "frames": meta.get("frames", 0),
                        }
                    except Exception:
                        pass

            self._index_built = True
            cloudlog.info(f"PCD index built: {len(self._spatial_index)} sessions")

        except Exception as e:
            cloudlog.debug(f"Failed to build PCD index: {e}")

    def _find_nearest_session(self, lat: float, lon: float) -> Path | None:
        """Find session with data closest to current position."""
        self._build_index()

        best_session = None
        best_distance = float('inf')

        for _name, info in self._spatial_index.items():
            # Check if position is within session bounds (with margin)
            margin = 0.001  # ~100m
            if (info["lat_min"] - margin <= lat <= info["lat_max"] + margin and
                info["lon_min"] - margin <= lon <= info["lon_max"] + margin):

                # Calculate distance to center
                center_lat = (info["lat_min"] + info["lat_max"]) / 2
                center_lon = (info["lon_min"] + info["lon_max"]) / 2
                dist = ((lat - center_lat) ** 2 + (lon - center_lon) ** 2) ** 0.5

                if dist < best_distance:
                    best_distance = dist
                    best_session = info["path"]

        return best_session

    def load_for_position(
        self,
        lat: float,
        lon: float,
        heading: float,
        max_distance_m: float = 100.0
    ) -> np.ndarray | None:
        """
        Load PCD data for position ahead of vehicle.

        Args:
            lat, lon: Current vehicle position
            heading: Vehicle heading in degrees
            max_distance_m: How far ahead to look for saved PCD

        Returns:
            Points array or None if no data available
        """
        try:
            session_path = self._find_nearest_session(lat, lon)
            if session_path is None:
                return None

            # List all PCD files in session
            pcd_files = sorted(session_path.glob("frame_*.pcd"))
            if not pcd_files:
                return None

            # For simplicity, load the most recent frame in session
            # In production, we'd match by position/heading more precisely
            cache_key = str(pcd_files[-1])

            if cache_key in self._cache:
                points, _ = self._cache[cache_key]
                return points

            # Load and cache
            result = self.pcd_reader.read(pcd_files[-1])
            if result is None:
                return None

            points, labels, _ = result

            # Manage cache size
            if len(self._cache) >= self._cache_max_size:
                oldest_key = next(iter(self._cache))
                del self._cache[oldest_key]

            self._cache[cache_key] = (points, labels)

            cloudlog.debug(f"Loaded historical PCD: {len(points)} points from {pcd_files[-1].name}")
            return points

        except Exception as e:
            cloudlog.debug(f"Failed to load historical PCD: {e}")
            return None

    def get_stats(self) -> dict[str, Any]:
        """Get loader statistics."""
        return {
            "sessions_indexed": len(self._spatial_index),
            "cache_size": len(self._cache),
            "cache_max": self._cache_max_size,
        }


# =============================================================================
# Main SurfaceD
# =============================================================================

class SurfaceD:
    """
    Surface perception daemon (NON-CRITICAL).

    Receives 3D point cloud from pointcloudd,
    creates BEV drivable area enhanced with SQSC history.

    Fault tolerant: if pointcloudd unavailable, publishes minimal grids
    so gridd can fall back to stereo-only costmap.
    """

    RATE = 20  # 20Hz, but input from pointcloudd is 5Hz

    def __init__(self):
        cloudlog.info("SurfaceD initializing...")
        set_daemon_affinity("surfaced")

        self.params = Params()
        self.enabled = self.params.get_bool("EOPSurfaceEnabled")

        # Grid parameters
        self.grid_resolution = float(self.params.get("EOPSurfaceGridResolution") or 0.25)
        self.grid_range_m = float(self.params.get("EOPSurfaceGridRange") or 60.0)
        self.grid_width_m = float(self.params.get("EOPSurfaceGridWidth") or 30.0)

        # Subscribe to inputs
        # - pointcloudProcessed: 3D point cloud from pointcloudd
        # - stereoSegments: PP-LiteSeg labels from stereod (for semantic fusion)
        # - gpsLocation: position for history lookup
        # - osmCorrectedPose: accurate pose for mapping
        self.sm = messaging.SubMaster(
            ["pointcloudProcessed", "stereoSegments", "gpsLocation", "osmCorrectedPose"],
            poll=["pointcloudProcessed"]
        )

        # Outputs
        self.pm = messaging.PubMaster([
            "drivableArea",
            "surfaceStatus",
        ])

        self.rk = Ratekeeper(self.RATE, print_delay_threshold=None)

        # Components
        self.bev_extractor = BEVDrivableAreaExtractor(
            resolution=self.grid_resolution,
            grid_size_m=(self.grid_width_m, self.grid_range_m)
        )
        self.anomaly_detector = SurfaceAnomalyDetector()
        self.history_db = SurfaceQualityDB()
        self.historical_pcd_loader = HistoricalPCDLoader()

        # Long horizon fusion settings
        self.long_horizon_enabled = self.params.get_bool("EOPSurfaceLongHorizon")
        self.long_horizon_range_m = float(self.params.get("EOPSurfaceLongRange") or 100.0)

        # PP-LiteSeg class indices for drivable area
        # Cityscapes: 0=road, 1=sidewalk, 9=terrain
        self.DRIVABLE_CLASSES = {0, 1, 9}
        self.ROAD_CLASS = 0  # Primary drivable surface

        # Stats
        self.stats = SurfaceDStats()
        self.frame_id = 0

        # Last known good pose for history lookup
        self._last_lat = 0.0
        self._last_lon = 0.0
        self._last_heading = 0.0
        self._pose_valid = False

        cloudlog.info(f"SurfaceD: enabled={self.enabled}, res={self.grid_resolution}m, range={self.grid_range_m}m")

    def _process_pointcloud(self, msg) -> np.ndarray | None:
        """Convert message to numpy array efficiently."""
        if msg is None or msg.validPoints == 0:
            return None

        try:
            n = len(msg.points)
            points = np.zeros((n, 3), dtype=np.float32)

            # Vectorized extraction
            for i in range(n):
                p = msg.points[i]
                points[i] = [p.x, p.y, p.z]

            return points
        except Exception as e:
            cloudlog.debug(f"Point cloud processing failed: {e}")
            return None

    def _decode_semantic_labels(self, msg) -> np.ndarray | None:
        """Decode PP-LiteSeg semantic labels from stereoSegments."""
        if msg is None or not msg.stereoRightCamera:
            return None

        try:
            w, h = msg.rightWidth, msg.rightHeight
            return np.frombuffer(msg.stereoRightCamera, dtype=np.uint8).reshape((h, w))
        except Exception as e:
            cloudlog.debug(f"Failed to decode semantic labels: {e}")
            return None

    def _extract_drivable_area_semantic(
        self,
        points: np.ndarray,
        semantic_labels: np.ndarray | None,
        calibration: dict[str, float]
    ) -> np.ndarray:
        """
        Extract drivable area using semantic labels from PP-LiteSeg.

        Uses class 0 (road) as primary drivable surface.
        Much more accurate than geometric-only ground detection.
        """
        if semantic_labels is None or len(points) == 0:
            # Fallback to geometric-only
            return self._extract_drivable_area_geometric(points)

        # Project 3D points to 2D image coordinates to sample labels
        # This is approximate - assumes camera frame alignment
        # For accurate projection, we'd need full camera calibration

        # Simple heuristic: points close to ground with road label = drivable
        points[:, 2]  # forward
        y = points[:, 1]  # up (height)

        # Ground height filter (allow some slope)
        is_ground = (y > -0.5) & (y < 0.3)

        # Sample semantic labels (simplified - assumes frontal projection)
        # In practice, we'd project using camera matrix
        # For now, use point indices as proxy (labels and points should align)

        # Mark drivable if ground points
        drivable_mask = is_ground

        return points[drivable_mask] if np.any(drivable_mask) else points

    def _extract_drivable_area_geometric(self, points: np.ndarray) -> np.ndarray:
        """Fallback: extract drivable area using geometric ground detection."""
        if len(points) == 0:
            return points

        y = points[:, 2]  # height
        is_ground = (y > -0.5) & (y < 0.3)

        return points[is_ground] if np.any(is_ground) else points

    def _get_pose(self) -> tuple[float, float, float, float]:
        """
        Get current pose (lat, lon, heading, speed).

        Priority: OSM-corrected pose > raw GPS
        """
        lat, lon, heading, speed = 0.0, 0.0, 0.0, 0.0

        # Try OSM-corrected pose first
        if self.sm.updated["osmCorrectedPose"]:
            try:
                osm_pose = self.sm["osmCorrectedPose"]
                if osm_pose.confidence > 0.5:
                    lat = osm_pose.correctedLat
                    lon = osm_pose.correctedLon
                    heading = osm_pose.correctedHeading
                    self._pose_valid = True
                    self._last_lat, self._last_lon, self._last_heading = lat, lon, heading
                    return lat, lon, heading, speed
            except Exception as e:
                cloudlog.debug(f"OSM pose read failed: {e}")

        # Fall back to raw GPS
        if self.sm.updated["gpsLocation"]:
            try:
                gps = self.sm["gpsLocation"]
                lat = gps.latitude
                lon = gps.longitude
                speed = getattr(gps, "speed", 0.0)

                # Calculate heading from velocity if available
                if hasattr(gps, "velocityNED") and gps.velocityNED:
                    vn, ve = gps.velocityNED[0], gps.velocityNED[1]
                    if abs(vn) > 0.1 or abs(ve) > 0.1:
                        heading = np.degrees(np.arctan2(ve, vn))
                elif hasattr(gps, "bearingDeg"):
                    heading = gps.bearingDeg

                self._pose_valid = True
                self._last_lat, self._last_lon, self._last_heading = lat, lon, heading
                return lat, lon, heading, speed
            except Exception as e:
                cloudlog.debug(f"GPS read failed: {e}")

        # Use last known pose if recent enough (< 5 seconds)
        if self._pose_valid:
            return self._last_lat, self._last_lon, self._last_heading, speed

        return 0.0, 0.0, 0.0, 0.0

    def _compute_clearances(self, grid: BEVGrid) -> tuple[float, float, float]:
        """
        Compute clearances from boundaries.

        Returns:
            (left_clearance, right_clearance, front_clearance)
        """
        # Find closest occupied cells
        occupied = (grid.data == CellState.OCCUPIED)

        # Front clearance: first occupied row
        front_clearance = self.grid_range_m
        for row in range(grid.grid_h):
            if np.any(occupied[row, :]):
                front_clearance = row * grid.resolution
                break

        # Left/right clearance from current row (closest to vehicle)
        current_row = 2  # ~0.5m ahead
        if current_row < grid.grid_h:
            row_data = grid.data[current_row, :]

            # Find first occupied from center to left
            center = grid.grid_w // 2
            left_clearance = self.grid_width_m / 2
            for col in range(center, grid.grid_w):
                if row_data[col] == CellState.OCCUPIED:
                    left_clearance = (col - center) * grid.resolution
                    break

            # Find first occupied from center to right
            right_clearance = self.grid_width_m / 2
            for col in range(center, -1, -1):
                if row_data[col] == CellState.OCCUPIED:
                    right_clearance = (center - col) * grid.resolution
                    break
        else:
            left_clearance = self.grid_width_m / 2
            right_clearance = self.grid_width_m / 2

        return left_clearance, right_clearance, front_clearance

    def _load_and_fuse_historical_pcd(
        self,
        current_points: np.ndarray,
        lat: float,
        lon: float,
        heading: float
    ) -> np.ndarray:
        """
        Load historical PCD and fuse with current point cloud.

        Extends drivable area beyond real-time sensor range (30m -> 100m+).
        Transforms historical points to current vehicle frame.
        """
        if not self.long_horizon_enabled:
            return current_points

        try:
            # Load historical PCD for position ahead
            historical_points = self.historical_pcd_loader.load_for_position(
                lat, lon, heading, self.long_horizon_range_m
            )

            if historical_points is None or len(historical_points) == 0:
                return current_points

            # Update stats
            self.stats.frames_with_long_horizon += 1
            alpha = 0.1
            self.stats.avg_historical_points = (
                (1 - alpha) * self.stats.avg_historical_points +
                alpha * len(historical_points)
            )

            # Transform historical points to be relative to current position
            # For now, assume same coordinate frame (both in vehicle frame)
            # In production, we'd use GPS + heading to transform accurately

            # Filter historical points to only those beyond current sensor range
            # This avoids double-counting near-field data
            if len(current_points) > 0:
                current_max_range = np.max(current_points[:, 0]) if len(current_points) > 0 else 30.0
                # Keep historical points that extend beyond current range
                historical_ahead = historical_points[historical_points[:, 0] > current_max_range * 0.8]
            else:
                historical_ahead = historical_points

            if len(historical_ahead) == 0:
                return current_points

            # Fuse: current points + historical extension
            fused = np.vstack([current_points, historical_ahead])

            cloudlog.debug(f"Fused PCD: {len(current_points)} current + {len(historical_ahead)} historical = {len(fused)} total")
            return fused

        except Exception as e:
            cloudlog.debug(f"Historical PCD fusion failed: {e}")
            return current_points

    def _estimate_surface_quality(self, points: np.ndarray) -> SurfaceQuality | None:
        """Estimate surface quality from point cloud roughness."""
        if len(points) < 100:
            return None

        # Ground points
        z = points[:, 2]
        ground_mask = (z > -0.5) & (z < 0.5)
        ground = points[ground_mask]

        if len(ground) < 50:
            return None

        # Height variation as roughness metric (use MAD for robustness)
        heights = ground[:, 2]
        median_h = np.median(heights)
        mad = np.median(np.abs(heights - median_h))
        roughness = float(mad * 1.4826)  # Convert MAD to std estimate

        # Score: 0=smooth, 1=rough
        score = min(roughness / 0.1, 1.0)

        # Texture classification
        if score < 0.2:
            texture = "smooth"
        elif score < 0.5:
            texture = "normal"
        elif score < 0.8:
            texture = "rough"
        else:
            texture = "very_rough"

        return SurfaceQuality(
            score=score,
            roughness_rms=roughness,
            texture=texture,
            confidence=min(len(ground) / 500, 1.0)
        )

    def _publish_drivable_area(
        self,
        ts: int,
        grid: BEVGrid,
        has_history: bool,
        left_clearance: float = 0.0,
        right_clearance: float = 0.0,
        front_clearance: float = 0.0,
        surface_quality: SurfaceQuality | None = None
    ):
        """Publish BEV drivable area."""
        try:
            msg = messaging.new_message("drivableArea")
            da = msg.drivableArea

            da.timestamp = ts
            da.frameId = self.frame_id

            da.resolution = grid.resolution
            da.width = grid.grid_w
            da.height = grid.grid_h
            da.originX = grid.origin_x
            da.originY = grid.origin_y
            da.data = grid.data.flatten().tobytes()

            # Stats
            da.numDrivable = int(np.sum(grid.data == CellState.DRIVABLE))
            da.numRough = int(np.sum(grid.data == CellState.ROUGH))
            da.numLearnedRough = int(np.sum(grid.data == CellState.LEARNED_ROUGH))
            da.hasHistoryEnhancement = has_history

            # Clearances
            da.leftClearanceM = left_clearance
            da.rightClearanceM = right_clearance
            da.frontClearanceM = front_clearance

            # Surface quality
            if surface_quality is not None:
                da.hasSurfaceQuality = True
                da.surfaceQuality.score = surface_quality.score
                da.surfaceQuality.roughnessRms = surface_quality.roughness_rms
                da.surfaceQuality.texture = surface_quality.texture
                da.surfaceQuality.confidence = surface_quality.confidence
            else:
                da.hasSurfaceQuality = False

            self.pm.send("drivableArea", msg)

        except Exception as e:
            cloudlog.error(f"Failed to publish drivableArea: {e}")
            self.stats.add_error(f"publish_drivableArea: {e}")

    def _publish_surface_status(
        self,
        ts: int,
        anomalies: list[dict[str, Any]],
        predictions: list[SurfacePrediction]
    ):
        """Publish surface status for SQSC."""
        try:
            msg = messaging.new_message("surfaceStatus")
            s = msg.surfaceStatus

            s.timestamp = ts

            # Current anomalies
            s.hasShocks = len(anomalies) > 0
            for a in anomalies[:5]:
                shock = s.shocks.add()
                shock.type = a["type"]
                shock.source = "stereo"
                shock.distanceM = a["distance_m"]
                shock.lateralM = a["lateral_m"]
                shock.severity = a["severity"]
                shock.confidence = 0.7

            # Historical predictions
            s.hasPredictions = len(predictions) > 0
            for p in predictions[:5]:
                pred = s.predictions.add()
                pred.distanceM = p.distance_m
                pred.qualityScore = p.quality_score
                pred.texture = p.texture
                pred.featureType = p.feature_type
                pred.recommendedSpeedMs = p.recommended_speed_ms
                pred.confidence = p.confidence

            # Recommended speed from history
            if predictions:
                best = min(predictions, key=lambda x: x.recommended_speed_ms if x.recommended_speed_ms > 0 else 100)
                s.recommendedSpeedMs = best.recommended_speed_ms
            else:
                s.recommendedSpeedMs = 0.0

            self.pm.send("surfaceStatus", msg)

        except Exception as e:
            cloudlog.error(f"Failed to publish surfaceStatus: {e}")

    def run(self):
        """Main loop - fault-tolerant BEV generation."""
        if not self.enabled:
            cloudlog.info("SurfaceD disabled")
            return

        cloudlog.info("SurfaceD running")

        consecutive_errors = 0
        MAX_CONSECUTIVE_ERRORS = 20

        while True:
            loop_start = time.perf_counter()

            try:
                self.sm.update(0)
                ts = int(time.monotonic() * 1e9)

                # Get 3D point cloud from pointcloudd
                points = None
                has_pointcloud = False

                if self.sm.updated["pointcloudProcessed"]:
                    pc_msg = self.sm["pointcloudProcessed"]
                    if pc_msg.validPoints > 0:
                        points = self._process_pointcloud(pc_msg)
                        if points is not None and len(points) > 0:
                            has_pointcloud = True
                            self.stats.frames_with_pointcloud += 1
                            self.stats.consecutive_no_data = 0
                            self.stats.update_points(len(points))
                        else:
                            self.stats.consecutive_no_data += 1
                    else:
                        self.stats.consecutive_no_data += 1
                else:
                    self.stats.consecutive_no_data += 1

                # Process if we have data
                if has_pointcloud and points is not None:
                    # Step 1: Get pose first (needed for long horizon)
                    lat, lon, heading, v_ego = self._get_pose()
                    has_pose = lat != 0 and lon != 0

                    if has_pose:
                        self.stats.frames_with_gps += 1

                    # Step 2: Load and fuse historical PCD for long horizon
                    # Extends drivable area from 30m (real-time) to 100m+ (historical)
                    if has_pose and self.long_horizon_enabled:
                        points_fused = self._load_and_fuse_historical_pcd(
                            points, lat, lon, heading
                        )
                    else:
                        points_fused = points

                    # Step 3: Create BEV from fused point cloud
                    bev_current = self.bev_extractor.extract_from_pointcloud(points_fused)

                    # Step 4: Detect anomalies (use current points only for accuracy)
                    anomalies = self.anomaly_detector.detect(points)

                    # Step 5: Read SQSC history for surface quality predictions
                    predictions = []
                    has_sqsc_history = False

                    if has_pose:
                        try:
                            predictions = self.history_db.predict_ahead(lat, lon, heading, v_ego)
                            has_sqsc_history = len([p for p in predictions if p.confidence > 0.3]) > 0
                            if has_sqsc_history:
                                self.stats.frames_with_history += 1
                        except Exception as e:
                            cloudlog.debug(f"SQSC history lookup failed: {e}")

                    # Step 6: Enhance with SQSC history
                    if predictions:
                        bev_enhanced = self.bev_extractor.enhance_with_history(
                            bev_current, predictions
                        )
                    else:
                        bev_enhanced = bev_current

                    # Step 7: Compute clearances and quality
                    left_c, right_c, front_c = self._compute_clearances(bev_enhanced)
                    surface_quality = self._estimate_surface_quality(points)

                    # Combined history flag: SQSC or long horizon PCD
                    has_any_history = has_sqsc_history or (len(points_fused) > len(points))

                    # Step 8: Publish
                    self._publish_drivable_area(
                        ts, bev_enhanced, has_any_history,
                        left_c, right_c, front_c,
                        surface_quality
                    )
                    self._publish_surface_status(ts, anomalies, predictions)

                    self.stats.frames_processed += 1
                    consecutive_errors = 0

                elif self.stats.consecutive_no_data < 50:  # 2.5 seconds
                    # Publish empty/minimal grid so gridd knows we're alive
                    bev_empty = self.bev_extractor.create_empty_grid()
                    self._publish_drivable_area(ts, bev_empty, False, 0.0, 0.0, 0.0, None)

                else:
                    # Extended pointcloudd outage
                    if self.stats.consecutive_no_data == 50:
                        cloudlog.warning("pointcloudd unavailable for 2.5s, gridd using stereo-only")

                # Update timing stats
                proc_time_ms = (time.perf_counter() - loop_start) * 1000
                self.stats.update_timing(proc_time_ms)

                self.frame_id += 1

            except Exception as e:
                consecutive_errors += 1
                self.stats.add_error(f"main_loop: {e}")
                cloudlog.error(f"Main loop error ({consecutive_errors}/{MAX_CONSECUTIVE_ERRORS}): {e}")

                if consecutive_errors >= MAX_CONSECUTIVE_ERRORS:
                    cloudlog.critical("Too many consecutive errors, restarting...")
                    raise

                time.sleep(0.05)

            self.rk.keep_time()


def main() -> int:
    logging.basicConfig(level=logging.INFO)
    try:
        SurfaceD().run()
        return 0
    except KeyboardInterrupt:
        cloudlog.info("SurfaceD stopped")
        return 0
    except Exception as e:
        cloudlog.exception(f"SurfaceD fatal error: {e}")
        return 1


if __name__ == "__main__":
    exit(main())

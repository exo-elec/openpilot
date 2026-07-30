#!/usr/bin/env python3
"""
surface_detector.py - Surface Detection Components

Components for surfaced daemon:
- IMUBumpDetector: Real-time IMU shock detection
- StereoSurfaceAnalyzer: Real-time stereo depth analysis
- SurfaceQualityAnalyzer: Continuous quality assessment
- SurfaceQualityDB: GPS-based predictive lookup
"""

from __future__ import annotations

import math
import sqlite3
import struct
import time
from collections import deque
from dataclasses import dataclass
from enum import Enum
from pathlib import Path

import numpy as np


class SurfaceFeatureType(Enum):
    """Types of surface features that affect driving."""
    UNKNOWN = "unknown"
    ROUGH_ROAD = "rough_road"
    POTHOLE = "pothole"
    POTHOLE_CLUSTER = "pothole_cluster"
    SPEED_BUMP = "speed_bump"
    SPEED_TABLE = "speed_table"
    RAILROAD_CROSSING = "railroad"
    BRIDGE_JOINT = "bridge_joint"
    MANHOLE_COVER = "manhole"
    DRAINAGE_GRATE = "drainage"
    METAL_PLATE = "metal_plate"
    GRAVEL_TRANSITION = "gravel"
    CONSTRUCTION = "construction"
    FROST_HEAVE = "frost_heave"


@dataclass
class SurfaceShock:
    """Detected surface shock/obstacle."""
    shock_type: str  # "bump", "pothole", "rough_patch"
    source: str  # "imu" or "stereo"
    distance_m: float  # 0 = now, >0 = meters ahead
    lateral_m: float  # offset from center
    severity: float  # 0-1
    confidence: float
    timestamp: float


@dataclass
class SurfaceQuality:
    """Surface quality metrics."""
    score: float  # 0-1 (0=smooth, 1=rough)
    texture: str  # "smooth", "normal", "rough", "very_rough"
    roughness_rms: float  # m/s²
    confidence: float


@dataclass
class SurfacePrediction:
    """Surface quality prediction from history."""
    distance_m: float
    quality_score: float
    texture: str
    feature_type: str
    recommended_speed_ms: float
    confidence: float


# =============================================================================
# IMU Shock Detector
# =============================================================================

class IMUBumpDetector:
    """IMU-based bump detection (reactive)."""
    
    def __init__(self, threshold_g: float = 2.5, cooldown_s: float = 1.0, history_size: int = 10):
        self.threshold_g = threshold_g
        self.cooldown_s = cooldown_s
        self.z_history = deque(maxlen=history_size)
        self.last_shock_time: float | None = None
        self.recent_shocks: list[SurfaceShock] = []
        
    def detect(self, z_accel_ms2: float, timestamp: float) -> SurfaceShock | None:
        """Detect shock from IMU z-acceleration."""
        z_g = abs(z_accel_ms2) / 9.81
        self.z_history.append(z_g)
        
        # Cooldown check
        if self.last_shock_time:
            if timestamp - self.last_shock_time < self.cooldown_s:
                return None
        
        if len(self.z_history) >= 3:
            recent = list(self.z_history)[-3:]
            recent_avg = np.mean(recent[:-1])
            current = recent[-1]
            
            # Spike detection: current > threshold AND > 1.5x average
            if current > self.threshold_g and current > recent_avg * 1.5:
                self.last_shock_time = timestamp
                shock = SurfaceShock(
                    shock_type="bump",
                    source="imu",
                    distance_m=0.0,  # Already happened
                    lateral_m=0.0,
                    severity=min(1.0, current / self.threshold_g - 1.0),
                    confidence=min(1.0, current / self.threshold_g - 1.0),
                    timestamp=timestamp
                )
                self.recent_shocks.append(shock)
                return shock
        return None
    
    def get_shock_count_and_clear(self) -> int:
        """Get count of recent shocks and clear."""
        count = len(self.recent_shocks)
        self.recent_shocks = []
        return count


# =============================================================================
# Stereo Surface Analyzer
# =============================================================================

class StereoSurfaceAnalyzer:
    """Stereo-based surface analysis (predictive real-time)."""
    
    def __init__(
        self,
        baseline_mm: float = 160.0,
        bump_height_thresh: float = 0.05,
        pothole_depth_thresh: float = 0.08,
        cooldown_s: float = 2.0
    ):
        self.baseline_mm = baseline_mm
        self.max_range = 80.0 if baseline_mm >= 160 else 40.0
        self.bump_height_thresh = bump_height_thresh
        self.pothole_depth_thresh = pothole_depth_thresh
        self.cooldown_s = cooldown_s
        
        self.plane_history = deque(maxlen=5)
        self.last_detection_time: float | None = None
        self.last_obstacles: list[SurfaceShock] = []
        
    def _estimate_ground_plane(self, points: np.ndarray) -> np.ndarray:
        """Estimate ground plane from point cloud using SVD."""
        if len(points) < 100:
            return np.array([0, 1, 0, 0])
        
        # Filter to forward-facing points
        valid = (points[:, 0] > 0) & (points[:, 0] < self.max_range)
        valid &= (points[:, 2] > -2) & (points[:, 2] < 2)
        
        if np.sum(valid) < 50:
            return np.array([0, 1, 0, 0])
        
        valid_points = points[valid]
        centroid = np.mean(valid_points, axis=0)
        centered = valid_points - centroid
        _, _, Vt = np.linalg.svd(centered)
        normal = Vt[-1]
        d = -np.dot(normal, centroid)
        plane = np.array([normal[0], normal[1], normal[2], d])
        
        # Temporal smoothing
        self.plane_history.append(plane)
        if len(self.plane_history) > 1:
            return np.mean(self.plane_history, axis=0)
        return plane
    
    def analyze(self, points_xyz: np.ndarray, timestamp: float) -> list[SurfaceShock]:
        """Analyze point cloud for surface obstacles."""
        self.last_obstacles = []
        
        # Cooldown
        if self.last_detection_time:
            if timestamp - self.last_detection_time < self.cooldown_s:
                return []
        
        if len(points_xyz) < 100:
            return []
        
        plane = self._estimate_ground_plane(points_xyz)
        a, b, c, d = plane
        norm = np.sqrt(a**2 + b**2 + c**2)
        
        obstacles = []
        
        for point in points_xyz:
            # Distance from plane
            dist = (a * point[0] + b * point[1] + c * point[2] + d) / norm
            forward_dist = point[0]
            lateral_dist = point[2]
            
            if forward_dist > self.max_range:
                continue
            
            # Raised obstacle (bump/speed table)
            if dist > self.bump_height_thresh:
                obstacles.append(SurfaceShock(
                    shock_type="bump",
                    source="stereo",
                    distance_m=float(forward_dist),
                    lateral_m=float(lateral_dist),
                    severity=min(1.0, dist / self.bump_height_thresh - 1.0),
                    confidence=min(1.0, dist / self.bump_height_thresh - 1.0),
                    timestamp=timestamp
                ))
                self.last_detection_time = timestamp
                
            # Depressed obstacle (pothole)
            elif dist < -self.pothole_depth_thresh:
                obstacles.append(SurfaceShock(
                    shock_type="pothole",
                    source="stereo",
                    distance_m=float(forward_dist),
                    lateral_m=float(lateral_dist),
                    severity=min(1.0, abs(dist) / self.pothole_depth_thresh - 1.0),
                    confidence=min(1.0, abs(dist) / self.pothole_depth_thresh - 1.0),
                    timestamp=timestamp
                ))
                self.last_detection_time = timestamp
        
        self.last_obstacles = obstacles
        return obstacles
    
    def get_obstacle_count_and_clear(self) -> int:
        """Get count of recent obstacles and clear."""
        count = len(self.last_obstacles)
        self.last_obstacles = []
        return count


# =============================================================================
# Surface Quality Analyzer
# =============================================================================

class SurfaceQualityAnalyzer:
    """Analyze surface quality from continuous IMU data."""
    
    WINDOW_DURATION_S = 2.0
    SAMPLE_RATE_HZ = 100
    WINDOW_SIZE = int(WINDOW_DURATION_S * SAMPLE_RATE_HZ)
    
    def __init__(self):
        self.z_history = deque(maxlen=self.WINDOW_SIZE)
        self.timestamps = deque(maxlen=self.WINDOW_SIZE)
        self.quality_history = deque(maxlen=10)
        
    def add_sample(self, z_accel_ms2: float, timestamp: float):
        """Add IMU sample for analysis."""
        self.z_history.append(z_accel_ms2)
        self.timestamps.append(timestamp)
    
    def analyze(self) -> SurfaceQuality | None:
        """Analyze surface quality from recent samples."""
        if len(self.z_history) < self.WINDOW_SIZE * 0.5:
            return None
        
        values = np.array(self.z_history)
        values_centered = values - np.mean(values)
        
        # RMS roughness
        rms = np.sqrt(np.mean(values_centered ** 2)) if len(values_centered) > 0 else 0.0
        
        # Peak detection
        peak_threshold = 2.0 * 9.81
        peak_count = 0
        for i in range(1, len(values_centered) - 1):
            if abs(values_centered[i]) > peak_threshold:
                if abs(values_centered[i]) > abs(values_centered[i-1]) and abs(values_centered[i]) > abs(values_centered[i+1]):
                    peak_count += 1
        
        # Normalize score
        rms_normalized = min(rms / 4.0, 1.0)
        peak_contribution = min(peak_count / 5.0, 0.3)
        raw_score = rms_normalized * 0.7 + peak_contribution
        
        self.quality_history.append(raw_score)
        score = float(np.mean(self.quality_history))
        
        # Texture classification
        if score < 0.2:
            texture = "smooth"
        elif score < 0.5:
            texture = "normal"
        elif score < 0.8:
            texture = "rough"
        else:
            texture = "very_rough"
        
        confidence = min(len(self.z_history) / self.WINDOW_SIZE, 1.0)
        
        return SurfaceQuality(
            score=score,
            texture=texture,
            roughness_rms=rms,
            confidence=confidence
        )
    
    def reset(self):
        """Reset analyzer."""
        self.z_history.clear()
        self.timestamps.clear()


# =============================================================================
# Surface History Database
# =============================================================================

def _encode_geohash(lat: float, lon: float, precision: int = 7) -> str:
    """Encode lat/lon to geohash string."""
    base32 = "0123456789bcdefghjkmnpqrstuvwxyz"
    lat_range = [-90.0, 90.0]
    lon_range = [-180.0, 180.0]
    
    geohash = []
    bits = 0
    bits_total = 0
    is_even = True
    
    while len(geohash) < precision:
        if is_even:
            mid = (lon_range[0] + lon_range[1]) / 2
            if lon >= mid:
                bits = bits * 2 + 1
                lon_range[0] = mid
            else:
                bits = bits * 2
                lon_range[1] = mid
        else:
            mid = (lat_range[0] + lat_range[1]) / 2
            if lat >= mid:
                bits = bits * 2 + 1
                lat_range[0] = mid
            else:
                bits = bits * 2
                lat_range[1] = mid
        
        is_even = not is_even
        bits_total += 1
        
        if bits_total == 5:
            geohash.append(base32[bits])
            bits = 0
            bits_total = 0
    
    return "".join(geohash)


def _calculate_destination(lat: float, lon: float, bearing: float, distance_m: float) -> tuple[float, float]:
    """Calculate destination point given start, bearing, and distance."""
    R = 6371000  # Earth radius in meters
    
    lat_rad = math.radians(lat)
    lon_rad = math.radians(lon)
    bearing_rad = math.radians(bearing)
    
    angular_distance = distance_m / R
    
    dest_lat = math.asin(
        math.sin(lat_rad) * math.cos(angular_distance) +
        math.cos(lat_rad) * math.sin(angular_distance) * math.cos(bearing_rad)
    )
    
    dest_lon = lon_rad + math.atan2(
        math.sin(bearing_rad) * math.sin(angular_distance) * math.cos(lat_rad),
        math.cos(angular_distance) - math.sin(lat_rad) * math.sin(dest_lat)
    )
    
    return math.degrees(dest_lat), math.degrees(dest_lon)


class SurfaceQualityDB:
    """SQLite database for surface quality with GPS indexing.
    
    Uses exopilot_shared schema v1.0 for compatibility with EVP.
    """
    
    DB_PATH = Path("/data/shared/exopilot/surface.db")
    GEOHASH_PRECISION = 7  # ~150m precision
    
    def __init__(self):
        self.DB_PATH.parent.mkdir(parents=True, exist_ok=True)
        self._init_db()
    
    def _init_db(self):
        """Initialize database schema - matches exopilot_shared package."""
        with sqlite3.connect(self.DB_PATH) as conn:
            conn.execute("""
                CREATE TABLE IF NOT EXISTS surface_quality (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    geohash TEXT NOT NULL,
                    lat REAL NOT NULL,
                    lon REAL NOT NULL,
                    heading_bucket INTEGER NOT NULL CHECK(heading_bucket BETWEEN 0 AND 7),
                    quality_score REAL CHECK(quality_score BETWEEN 0 AND 1),
                    roughness_rms REAL,
                    texture TEXT CHECK(texture IN ('smooth', 'normal', 'rough', 'very_rough')),
                    feature_type TEXT,
                    shock_count INTEGER DEFAULT 0,
                    recommended_speed_ms REAL,
                    section_length_m REAL,
                    timestamp REAL NOT NULL,
                    record_count INTEGER DEFAULT 1,
                    confidence REAL CHECK(confidence BETWEEN 0 AND 1),
                    source TEXT DEFAULT 'exopilot01m' CHECK(source IN ('exopilot01m', 'exopilot02m', 'exopilot03m', 'merged'))
                )
            """)
            
            conn.execute("""
                CREATE INDEX IF NOT EXISTS idx_spatial 
                ON surface_quality(geohash, heading_bucket)
            """)
            
            conn.execute("""
                CREATE INDEX IF NOT EXISTS idx_timestamp 
                ON surface_quality(timestamp)
            """)
            
            conn.execute("""
                CREATE INDEX IF NOT EXISTS idx_source 
                ON surface_quality(source)
            """)
            
            conn.commit()
    
    def _heading_to_bucket(self, heading: float) -> int:
        """Convert heading to 8-direction bucket."""
        heading = heading % 360.0
        return int(heading / 45.0) % 8
    
    def _get_heading_difference(self, h1: float, h2: float) -> float:
        """Get smallest angle difference between headings."""
        diff = abs((h1 % 360) - (h2 % 360))
        if diff > 180:
            diff = 360 - diff
        return diff
    
    def _estimate_feature_type(self, shock_count: int, quality_score: float) -> str:
        """Estimate feature type from recorded data."""
        if shock_count >= 3:
            return SurfaceFeatureType.POTHOLE_CLUSTER.value
        elif shock_count >= 1:
            return SurfaceFeatureType.POTHOLE.value
        elif quality_score > 0.8:
            return SurfaceFeatureType.CONSTRUCTION.value
        elif quality_score > 0.6:
            return SurfaceFeatureType.ROUGH_ROAD.value
        elif quality_score > 0.4:
            return SurfaceFeatureType.GRAVEL_TRANSITION.value
        return SurfaceFeatureType.UNKNOWN.value
    
    def record_surface(
        self,
        lat: float,
        lon: float,
        quality_score: float,
        shock_count: int = 0,
        roughness_rms: float = 0.0,
        texture: str = "unknown",
        heading: float = 0.0,
        v_ego_ms: float = 0.0,
        section_length_m: float = 0.0,
        timestamp: float | None = None
    ) -> bool:
        """Record surface quality at a location."""
        if timestamp is None:
            timestamp = time.monotonic()
        
        # Calculate recommended speed
        if shock_count > 0:
            base_speed = 8.0
        elif quality_score > 0.7:
            base_speed = 12.0
        elif quality_score > 0.5:
            base_speed = 15.0
        elif quality_score > 0.3:
            base_speed = 20.0
        else:
            base_speed = 30.0
        
        if 0 < v_ego_ms < base_speed:
            recommended_speed = v_ego_ms
        else:
            recommended_speed = base_speed
        
        feature_type = self._estimate_feature_type(shock_count, quality_score)
        
        geohash = _encode_geohash(lat, lon, self.GEOHASH_PRECISION)
        heading_bucket = self._heading_to_bucket(heading)
        
        try:
            with sqlite3.connect(self.DB_PATH) as conn:
                cursor = conn.execute(
                    """SELECT quality_score, record_count, shock_count, recommended_speed_ms 
                       FROM surface_quality WHERE geohash = ? AND heading_bucket = ?""",
                    (geohash, heading_bucket)
                )
                existing = cursor.fetchone()
                
                if existing:
                    old_score, pass_count, old_shocks, old_speed = existing
                    new_pass_count = min(pass_count + 1, 100)
                    
                    alpha = 1.0 / new_pass_count
                    new_score = old_score * (1 - alpha) + quality_score * alpha
                    new_shocks = int(old_shocks * (1 - alpha) + shock_count * alpha)
                    
                    old_conf = min(pass_count / 10.0, 1.0)
                    if old_conf + alpha > 0:
                        new_speed = (old_speed * old_conf + recommended_speed * alpha) / (old_conf + alpha)
                    else:
                        new_speed = recommended_speed
                    
                    confidence = min(new_pass_count / 10.0, 1.0)
                    
                    conn.execute(
                        """UPDATE surface_quality SET
                            lat = ?, lon = ?,
                            quality_score = ?, shock_count = ?, roughness_rms = ?,
                            texture = ?, feature_type = ?, recommended_speed_ms = ?,
                            section_length_m = ?, timestamp = ?, record_count = ?, confidence = ?
                        WHERE geohash = ? AND heading_bucket = ?""",
                        (lat, lon, new_score, new_shocks, roughness_rms,
                         texture, feature_type, new_speed, section_length_m,
                         timestamp, new_pass_count, confidence, geohash, heading_bucket)
                    )
                else:
                    confidence = 0.1
                    conn.execute(
                        """INSERT INTO surface_quality 
                        (geohash, lat, lon, heading_bucket, quality_score, shock_count, 
                         roughness_rms, texture, feature_type, recommended_speed_ms, section_length_m,
                         timestamp, record_count, confidence, source)
                        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                        (geohash, lat, lon, heading_bucket, quality_score, shock_count,
                         roughness_rms, texture, feature_type, recommended_speed, section_length_m,
                         timestamp, 1, confidence, 'exopilot01m')
                    )
                
                conn.commit()
                return True
                
        except sqlite3.Error as e:
            print(f"SurfaceQualityDB error: {e}")
            return False
    
    def predict_ahead(
        self,
        lat: float,
        lon: float,
        heading: float,
        v_ego_ms: float,
        distances_m: list[float] = None
    ) -> list[SurfacePrediction]:
        """Predict surface quality at distances ahead using historical data."""
        if distances_m is None:
            if v_ego_ms > 5.0:
                distances_m = [int(v_ego_ms * t) for t in [2, 4, 8, 12]]
            else:
                distances_m = [50, 100, 200, 300]
        
        predictions = []
        heading_bucket = self._heading_to_bucket(heading)
        
        for distance in distances_m:
            dest_lat, dest_lon = _calculate_destination(lat, lon, heading, distance)
            dest_geohash = _encode_geohash(dest_lat, dest_lon, self.GEOHASH_PRECISION)
            
            try:
                with sqlite3.connect(self.DB_PATH) as conn:
                    # Query by geohash and heading bucket
                    cursor = conn.execute(
                        """SELECT quality_score, texture, feature_type, recommended_speed_ms, 
                                  record_count, confidence, heading_bucket
                           FROM surface_quality 
                           WHERE geohash = ? AND heading_bucket = ?
                           ORDER BY record_count DESC, confidence DESC LIMIT 1""",
                        (dest_geohash, heading_bucket)
                    )
                    row = cursor.fetchone()
                    
                    if row:
                        quality_score, texture, feature_type, rec_speed, record_count, conf, row_bucket = row
                        
                        # Adjust confidence based on heading bucket match
                        bucket_diff = abs(row_bucket - heading_bucket)
                        bucket_diff = min(bucket_diff, 8 - bucket_diff)  # Wrap around
                        heading_boost = 1.0 - (bucket_diff / 8.0)
                        conf = conf * (0.7 + 0.3 * heading_boost)
                        
                        predictions.append(SurfacePrediction(
                            distance_m=distance,
                            quality_score=quality_score,
                            texture=texture,
                            feature_type=feature_type,
                            recommended_speed_ms=rec_speed if rec_speed > 0 else 15.0,
                            confidence=conf
                        ))
                    else:
                        predictions.append(SurfacePrediction(
                            distance_m=distance,
                            quality_score=0.0,
                            texture="unknown",
                            feature_type="unknown",
                            recommended_speed_ms=0.0,
                            confidence=0.0
                        ))
                        
            except sqlite3.Error:
                predictions.append(SurfacePrediction(
                    distance_m=distance,
                    quality_score=0.0,
                    texture="unknown",
                    feature_type="unknown",
                    recommended_speed_ms=0.0,
                    confidence=0.0
                ))
        
        return predictions
    
    def get_stats(self) -> dict:
        """Get database statistics."""
        try:
            with sqlite3.connect(self.DB_PATH) as conn:
                cursor = conn.execute(
                    "SELECT COUNT(*), AVG(confidence), AVG(quality_score) FROM surface_quality"
                )
                row = cursor.fetchone()
                
                return {
                    "total_segments": row[0] or 0,
                    "avg_confidence": row[1] or 0.0,
                    "avg_quality_score": row[2] or 0.0
                }
        except sqlite3.Error:
            return {"total_segments": 0, "avg_confidence": 0.0, "avg_quality_score": 0.0}

"""
cslb.py - Curve Speed Learning Database

Library for learning and querying SAFE SPEEDS through curves.
Used by MTSC (Map Turn Speed Control) for crowd-sourced safe speeds.

Key Design Principles:
  - NOT personalized: stores safe speeds, not driver preferences
  - Crowd-shared: all vehicles contribute to shared database
  - Slope-aware: considers road grade (uphill/downhill) for speed calculation
  - Physics-based: safe speed depends on curvature + slope + surface

Database location (shared format):
  - Default: /data/shared/exopilot/curve.db
  - Schema version: 2.0 (added slope/grade awareness)
"""
from __future__ import annotations

import os
import time
import logging
import sqlite3
import math
from pathlib import Path

from openpilot.selfdrive.controls.lib.eop_utils import detect_exopilot_platform

logger = logging.getLogger(__name__)

# Configuration - shared database location
DEFAULT_DB_PATH = Path("/data/shared/exopilot/curve.db")
SCHEMA_VERSION = "2.0"
MIN_CURVE_RADIUS = 30.0  # meters (ignore straights)
MAX_CURVE_RADIUS = 1000.0  # meters
MIN_SPEED = 20.0  # kph (ignore parking)
MAX_SPEED = 200.0  # kph
GPS_PRECISION = 100.0  # meters (hash precision)
UPDATE_WEIGHT = 0.1  # EMA weight for updates (lower = more conservative)

# Safe speed calculation constants
MAX_LATERAL_ACCEL = 3.5  # m/s^2 - comfortable lateral acceleration
GRAVITY = 9.81  # m/s^2

# EOP-CLEANUP: GPS_TOLERANCE_M moved to eop_utils.py (all platforms use 50.0m).
# Import from there if needed: from openpilot.selfdrive.controls.lib.eop_utils import GPS_TOLERANCE_M


class CurveSpeedEntry:
    """Represents a curve speed database entry."""
    
    def __init__(self, lat: float, lon: float, radius: float, speed_mps: float,
                 grade: float = 0.0, heading: float = 0.0, sample_count: int = 1,
                 confidence: float = 0.0):
        self.lat = lat
        self.lon = lon
        self.radius = radius  # meters (positive)
        self.speed_mps = speed_mps  # safe speed through curve
        self.grade = grade  # slope grade (-1.0 to +1.0, negative = downhill)
        self.heading = heading  # approach heading
        self.sample_count = sample_count
        self.confidence = confidence  # 0-1 based on sample count


class CurveDatabase:
    """SQLite database for crowd-sourced curve safe speeds.
    
    The database stores safe speeds for curves based on:
    - Curvature (radius)
    - Slope/grade (uphill/downhill matters!)
    - Surface conditions (future: from SQSC integration)
    
    Safe speed is crowd-sourced from all vehicles, not personalized.
    """
    
    def __init__(self, db_path: Path | None = None):
        self.db_path = db_path or DEFAULT_DB_PATH
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self.platform = self._detect_platform()
        self._conn: sqlite3.Connection | None = None
        self._init_schema()
    
    def _get_conn(self) -> sqlite3.Connection:
        """Get or create persistent SQLite connection."""
        if self._conn is None:
            self._conn = sqlite3.connect(self.db_path)
        return self._conn

    def close(self):
        """Close persistent connection (call on shutdown)."""
        if self._conn is not None:
            self._conn.close()
            self._conn = None

    def _init_schema(self):
        """Initialize v2.0 schema with slope/grade support."""
        conn = self._get_conn()
        # Main curve speeds table with grade/slope
        conn.execute("""
            CREATE TABLE IF NOT EXISTS curve_speeds (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                lat REAL NOT NULL,
                lon REAL NOT NULL,
                heading REAL NOT NULL,
                curvature REAL NOT NULL,  -- 1/radius in 1/m
                radius_m REAL NOT NULL,   -- cached radius for query
                grade REAL DEFAULT 0.0,   -- slope grade (-1.0 to +1.0)
                speed_mps REAL NOT NULL,  -- safe speed (crowd-sourced)
                timestamp REAL NOT NULL,
                source TEXT DEFAULT 'exopilot01m',
                speed_confidence REAL DEFAULT 0.0,  -- based on sample count
                sample_count INTEGER DEFAULT 1,
                road_hash TEXT,

                -- Composite index for efficient queries
                UNIQUE(lat, lon, heading, grade)
            )
        """)

        # Indexes for fast lookups
        conn.execute("CREATE INDEX IF NOT EXISTS idx_curve_latlon ON curve_speeds(lat, lon)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_curve_curvature ON curve_speeds(curvature)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_curve_grade ON curve_speeds(grade)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_curve_radius ON curve_speeds(radius_m)")

        # Metadata table
        conn.execute("""
            CREATE TABLE IF NOT EXISTS db_metadata (
                key TEXT PRIMARY KEY,
                value TEXT
            )
        """)

        # Store schema version
        conn.execute("""
            INSERT OR REPLACE INTO db_metadata (key, value)
            VALUES ('schema_version', ?)
        """, (SCHEMA_VERSION,))

        conn.commit()
        logger.info(f"Curve DB initialized (v{SCHEMA_VERSION}): {self.db_path}")
    
    def _detect_platform(self) -> str:
        """Detect ExoPilot platform (delegated to shared utility)."""
        return detect_exopilot_platform()
    
    def _haversine(self, lat1: float, lon1: float, lat2: float, lon2: float) -> float:
        """Calculate distance between two GPS coordinates (meters)."""
        R = 6371000
        lat1_rad = math.radians(lat1)
        lat2_rad = math.radians(lat2)
        dlat = math.radians(lat2 - lat1)
        dlon = math.radians(lon2 - lon1)
        
        a = (math.sin(dlat/2) ** 2 + 
             math.cos(lat1_rad) * math.cos(lat2_rad) * math.sin(dlon/2) ** 2)
        c = 2 * math.atan2(math.sqrt(a), math.sqrt(1-a))
        
        return R * c
    
    @staticmethod
    def calculate_theoretical_speed(radius: float, grade: float = 0.0,
                                    max_lat_accel: float = MAX_LATERAL_ACCEL) -> float:
        """Calculate theoretical safe speed for a curve with slope.
        
        Physics: v = sqrt(r * g * (μ + tan(θ)) / (1 - μ*tan(θ)))
        Simplified: v = sqrt(r * (a_max + g*sin(θ)))
        
        Args:
            radius: Curve radius in meters
            grade: Road grade (-1.0 to +1.0, negative = downhill)
            max_lat_accel: Maximum comfortable lateral acceleration
            
        Returns:
            Theoretical safe speed in m/s
        """
        # Effective lateral acceleration depends on grade
        # Uphill: more normal force, better traction
        # Downhill: less normal force, worse traction
        grade_effect = GRAVITY * grade * 0.3  # Simplified grade effect
        
        effective_accel = max_lat_accel + grade_effect
        effective_accel = max(1.0, effective_accel)  # Minimum 1 m/s^2
        
        return math.sqrt(radius * effective_accel)
    
    def get_speed(self, lat: float, lon: float, radius: float,
                  grade: float = 0.0, heading: float = 0.0,
                  gps_tolerance_m: float = 30.0,
                  use_crowd: bool = True) -> float | None:
        """Get safe speed for curve with GPS tolerance and grade consideration.
        
        Args:
            lat, lon: GPS coordinates
            radius: Curve radius in meters
            grade: Road grade (-1.0 to +1.0, negative = downhill)
            heading: Approach heading for direction-aware curves
            gps_tolerance_m: How far off GPS can be (default 30m)
            use_crowd: If True, query database; if False, use theoretical only
            
        Returns:
            Safe speed in kph or None if not found
        """
        curvature = 1.0 / radius if radius > 0 else 0
        
        # Calculate theoretical speed as baseline
        theoretical_speed = self.calculate_theoretical_speed(radius, grade)
        
        if not use_crowd or not self.db_path.exists():
            return theoretical_speed * 3.6  # Convert to kph
        
        conn = self._get_conn()
        cursor = conn.cursor()

        # Search nearby curves with similar grade
        cursor.execute('''
            SELECT lat, lon, radius_m, grade, speed_mps, sample_count, speed_confidence
            FROM curve_speeds
            WHERE ABS(curvature - ?) < 0.01
              AND ABS(grade - ?) < 0.05  -- Similar grade ±5%
            ORDER BY ABS(lat - ?) + ABS(lon - ?)
            LIMIT 10
        ''', (curvature, grade, lat, lon))

        results = cursor.fetchall()
        
        if not results:
            # No crowd data, use theoretical
            return theoretical_speed * 3.6
        
        # Find best match within GPS tolerance
        best_match = None
        best_confidence = 0.0
        
        for row_lat, row_lon, row_radius, row_grade, speed_mps, count, conf in results:
            distance_m = self._haversine(lat, lon, row_lat, row_lon)
            
            if distance_m > gps_tolerance_m:
                continue
            
            # Prefer higher confidence (more samples) and closer matches
            combined_score = conf * 0.7 + (1 - distance_m / gps_tolerance_m) * 0.3
            
            if combined_score > best_confidence:
                best_confidence = combined_score
                best_match = speed_mps
        
        if best_match and best_confidence > 0.3:
            # Blend crowd data with theoretical (crowd gets more weight with more samples)
            crowd_weight = min(0.8, best_confidence)
            blended_speed = crowd_weight * best_match + (1 - crowd_weight) * theoretical_speed
            return blended_speed * 3.6
        
        return theoretical_speed * 3.6
    
    def get_speeds_in_range(self, lat: float, lon: float,
                           radius_range: tuple[float, float] = (30.0, 1000.0),
                           max_distance_m: float = 2000.0) -> list[tuple[float, float, float, float]]:
        """Get learned safe speeds for curves in range (predictive lookup).
        
        Used by MTSC for predictive speed control - look ahead 1-2km
        and adjust speed BEFORE reaching the curve.
        
        Args:
            lat, lon: Current GPS coordinates
            radius_range: (min_radius, max_radius) to query
            max_distance_m: Maximum distance to search (default 2km)
            
        Returns:
            List of (distance_m, radius_m, grade, speed_kph) tuples
                     sorted by distance from current position
        """
        conn = self._get_conn()
        cursor = conn.cursor()

        # Search in bounding box first (faster than haversine for all rows)
        lat_range = max_distance_m / 111000.0  # ~111km per degree
        lon_range = lat_range / math.cos(math.radians(lat))

        cursor.execute('''
            SELECT lat, lon, radius_m, grade, speed_mps
            FROM curve_speeds
            WHERE lat BETWEEN ? AND ?
              AND lon BETWEEN ? AND ?
              AND radius_m BETWEEN ? AND ?
              AND sample_count >= 3  -- Minimum samples for reliability
            ORDER BY sample_count DESC
            LIMIT 50
        ''', (lat - lat_range, lat + lat_range,
              lon - lon_range, lon + lon_range,
              radius_range[0], radius_range[1]))

        results = []
        for row in cursor.fetchall():
            row_lat, row_lon, radius, grade, speed_mps = row
            distance_m = self._haversine(lat, lon, row_lat, row_lon)

            if distance_m <= max_distance_m:
                results.append((distance_m, radius, grade, speed_mps * 3.6))

        return sorted(results, key=lambda x: x[0])  # Sort by distance
    
    def update_speed(self, lat: float, lon: float, radius: float, speed_kph: float,
                     grade: float = 0.0, heading: float = 0.0, source: str = None):
        """Update or insert curve safe speed (crowd-sourced).
        
        This is NOT personalized - it updates the shared safe speed database
        that all vehicles use. The speed should be what the vehicle actually
        drove through the curve safely, not driver preference.
        
        Args:
            lat, lon: GPS coordinates
            radius: Curve radius in meters
            speed_kph: Observed safe speed in kph
            grade: Road grade (-1.0 to +1.0, negative = downhill)
            heading: Vehicle heading (approach direction)
            source: Platform identifier (None = auto-detect)
        """
        if source is None:
            source = self.platform
        
        if not (MIN_SPEED <= speed_kph <= MAX_SPEED):
            return
        if not (MIN_CURVE_RADIUS <= radius <= MAX_CURVE_RADIUS):
            return
        
        curvature = 1.0 / radius if radius > 0 else 0
        speed_mps = speed_kph / 3.6
        
        # Validate speed is reasonable for curve (with grade)
        theoretical = self.calculate_theoretical_speed(radius, grade)
        if speed_mps > theoretical * 1.5:
            # Speed is too fast for physics, probably not a safe baseline
            logger.warning(f"Curve speed {speed_kph:.1f} kph exceeds theoretical limit "
                          f"{theoretical*3.6:.1f} kph for r={radius}m, grade={grade:.2%}")
            return
        
        conn = self._get_conn()
        cursor = conn.cursor()

        # Check if exists near this location with similar grade
        cursor.execute('''
            SELECT id, speed_mps, sample_count FROM curve_speeds
            WHERE ABS(lat - ?) < 0.001
              AND ABS(lon - ?) < 0.001
              AND ABS(curvature - ?) < 0.01
              AND ABS(grade - ?) < 0.05
        ''', (lat, lon, curvature, grade))

        result = cursor.fetchone()

        if result:
            # Update with EMA - more samples = more stable
            row_id, old_speed, count = result

            # Adaptive weight: more samples = lower weight for new data
            adaptive_weight = UPDATE_WEIGHT / (1 + count * 0.1)
            new_speed = (1 - adaptive_weight) * old_speed + adaptive_weight * speed_mps

            new_confidence = min(1.0, (count + 1) / 50.0)  # Max confidence at 50 samples

            cursor.execute('''
                UPDATE curve_speeds
                SET speed_mps = ?,
                    sample_count = sample_count + 1,
                    speed_confidence = ?,
                    timestamp = ?,
                    source = ?
                WHERE id = ?
            ''', (new_speed, new_confidence, time.monotonic(), source, row_id))
        else:
            # Insert new entry
            cursor.execute('''
                INSERT INTO curve_speeds
                (lat, lon, heading, curvature, radius_m, grade, speed_mps,
                 timestamp, source, sample_count, speed_confidence)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 1, 0.1)
            ''', (lat, lon, heading, curvature, radius, grade, speed_mps,
                  time.monotonic(), source))

        conn.commit()


def get_curve_speed(lat: float, lon: float, radius: float,
                    grade: float = 0.0,
                    gps_tolerance_m: float = 30.0,
                    db: CurveDatabase | None = None) -> float | None:
    """Static helper to query safe curve speed.

    Used by MTSC to get safe speed. Combines crowd-sourced data with
    physics-based theoretical calculation.

    Args:
        lat, lon: GPS coordinates
        radius: Curve radius in meters
        grade: Road grade (-1.0 to +1.0, negative = downhill)
        gps_tolerance_m: GPS position tolerance in meters (default 30m)
        db: Optional CurveDatabase instance (avoids re-instantiation)

    Returns:
        Safe speed in kph or theoretical calculation if no data
    """
    if db is None:
        db = CurveDatabase()
    return db.get_speed(lat, lon, radius, grade, gps_tolerance_m=gps_tolerance_m)


def update_curve_speed(lat: float, lon: float, curvature: float, speed_ms: float,
                       grade: float = 0.0, db: CurveDatabase | None = None):
    """Update curve safe speed in database (crowd-sourced learning).

    Called by longitudinal planner when driving through curves.
    This contributes to the shared safe speed database.

    Args:
        lat, lon: GPS coordinates
        curvature: Road curvature (1/m)
        speed_ms: Current speed in m/s (safe speed observed)
        grade: Road grade (-1.0 to +1.0)
        db: Optional CurveDatabase instance (avoids re-instantiation)
    """
    if abs(curvature) < 1e-6:
        return

    radius = 1.0 / abs(curvature)
    speed_kph = speed_ms * 3.6

    if db is None:
        db = CurveDatabase()
    db.update_speed(lat, lon, radius, speed_kph, grade)

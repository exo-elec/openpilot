#!/usr/bin/env python3
"""
surface_quality_db.py - Surface Quality Database

Active learning database for road surface conditions.
Similar to CSLB (Curve Speed Learner) but for surface quality instead of curves.

Records surface quality in real-time using EMA (exponential moving average) to improve
precision with each pass. Provides predictive lookups for SQSC controller.

Features tracked:
- Road texture (smooth/normal/rough/very_rough)
- Shock events (potholes, bumps, railroad crossings)
- Learned recommended speeds (driver behavior)
- Section length (how long rough patches last)

Database: /data/shared/exopilot/surface.db (shared with EVP)

Usage:
  from surface_quality_db import SurfaceQualityDB, get_surface_prediction

  # Record surface quality (updates existing if same location/heading)
  db = SurfaceQualityDB()
  db.record_surface(lat, lon, heading=45.0, quality_score=0.6,
                    shock_count=2, texture='rough', feature_type='pothole_cluster')

  # Predict surface quality ahead
  predictions = db.predict_ahead(lat, lon, heading, v_ego_ms)
"""

import sqlite3
import math
import time
from pathlib import Path
from openpilot.common.swaglog import cloudlog
from dataclasses import dataclass
from enum import Enum

from openpilot.selfdrive.controls.lib.eop_utils import detect_exopilot_platform

# Use ExoPilot shared package when deployed (/data on device); fall back to
# in-repo implementations so this module works without the external package.
import sys
sys.path.insert(0, '/data')
try:
  from exopilot_shared.geohash import encode as geohash_encode, heading_to_bucket
  from exopilot_shared.surface_quality import get_default_source
except ImportError:
  def geohash_encode(lat: float, lon: float, precision: int = 7) -> str:
    """Standard base32 geohash (same algorithm as surfaced/surface_detector.py)."""
    base32 = "0123456789bcdefghjkmnpqrstuvwxyz"
    lat_range = [-90.0, 90.0]
    lon_range = [-180.0, 180.0]
    out: list[str] = []
    bits = 0
    bits_total = 0
    is_even = True
    while len(out) < precision:
      rng, val = (lon_range, lon) if is_even else (lat_range, lat)
      mid = (rng[0] + rng[1]) / 2
      if val >= mid:
        bits = bits * 2 + 1
        rng[0] = mid
      else:
        bits = bits * 2
        rng[1] = mid
      is_even = not is_even
      bits_total += 1
      if bits_total == 5:
        out.append(base32[bits])
        bits = 0
        bits_total = 0
    return "".join(out)

  def heading_to_bucket(heading: float) -> int:
    """Convert heading to 8-direction bucket (0-7, 45-degree sectors)."""
    return int((heading % 360.0) // 45.0) % 8

  def get_default_source() -> str:
    """Default DB source = this platform's name (exopilot01m/exopilot02m)."""
    return detect_exopilot_platform()


class SurfaceFeatureType(Enum):
    """Types of surface features that affect driving."""
    UNKNOWN = "unknown"
    ROUGH_ROAD = "rough_road"           # Generally rough surface
    POTHOLE = "pothole"                 # Single pothole
    POTHOLE_CLUSTER = "pothole_cluster" # Multiple potholes
    SPEED_BUMP = "speed_bump"           # Traffic calming bump
    SPEED_TABLE = "speed_table"         # Longer speed bump
    RAILROAD_CROSSING = "railroad"      # Railroad tracks
    BRIDGE_JOINT = "bridge_joint"       # Expansion joint
    MANHOLE_COVER = "manhole"           # Raised/sunken manhole
    DRAINAGE_GRATE = "drainage"         # Metal drainage grate
    METAL_PLATE = "metal_plate"         # Temporary road cover
    GRAVEL_TRANSITION = "gravel"        # Paved to gravel transition
    CONSTRUCTION = "construction"       # Construction zone
    FROST_HEAVE = "frost_heave"         # Seasonal road damage


# Geohash now imported from exopilot_shared.geohash
# Kept for backward compatibility:
_encode_geohash = geohash_encode


def _get_neighbors(geohash: str) -> list[str]:
    """Get neighboring geohash cells (8 neighbors + self)."""
    if not geohash:
        return []

    # Simplified - return self and basic neighbors
    # For production, use proper geohash library
    neighbors = [geohash]

    # Add simple offsets (not geohash-correct but good enough for nearby search)
    if len(geohash) >= 6:
        base = geohash[:-1]
        last = geohash[-1]
        base32 = '0123456789bcdefghjkmnpqrstuvwxyz'
        if last in base32:
            idx = base32.index(last)
            if idx > 0:
                neighbors.append(base + base32[idx - 1])
            if idx < 31:
                neighbors.append(base + base32[idx + 1])

    return neighbors


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


@dataclass
class SurfacePrediction:
    """Surface quality prediction result."""
    quality_score: float
    texture: str
    confidence: float
    distance_m: float
    shock_probability: float
    recommended_speed_ms: float
    feature_type: str
    section_length_m: float
    source: str  # 'history', 'realtime', 'interpolated'


@dataclass
class SurfaceRecord:
    """Complete surface record for database storage."""
    lat: float
    lon: float
    heading: float
    quality_score: float
    shock_count: int
    roughness_rms: float
    texture: str
    feature_type: str
    recommended_speed_ms: float
    section_length_m: float
    timestamp: float
    record_count: int
    confidence: float


class SurfaceQualityDB:
    """SQLite database for surface quality with active learning (EMA updates)."""

    DB_PATH = Path("/data/shared/exopilot/surface.db")
    SCHEMA_VERSION = "1.0"
    GEOHASH_PRECISION = 7  # ~150m precision
    CACHE_SIZE = 128  # LRU cache size for lookups
    CACHE_TTL_SECONDS = 5.0  # Cache TTL for dynamic data

    def __init__(self):
        self.DB_PATH.parent.mkdir(parents=True, exist_ok=True)
        self.platform = self._detect_platform()
        self._conn: sqlite3.Connection | None = None
        self._init_schema()
        # Simple LRU cache: {(geohash, heading_bucket): (timestamp, result)}
        self._cache: dict[tuple[str, int], tuple[float, dict | None]] = {}

    def _get_conn(self) -> sqlite3.Connection:
        """Get or create persistent SQLite connection."""
        if self._conn is None:
            self._conn = sqlite3.connect(self.DB_PATH)
        return self._conn

    def close(self):
        """Close persistent connection (call on shutdown)."""
        if self._conn is not None:
            self._conn.close()
            self._conn = None

    def _cache_key(self, lat: float, lon: float, heading: float) -> tuple[str, int]:
        """Generate cache key from location."""
        geohash = _encode_geohash(lat, lon, self.GEOHASH_PRECISION)
        heading_bucket = self._heading_to_bucket(heading)
        return (geohash, heading_bucket)

    def _get_cached(self, key: tuple[str, int]) -> dict | None:
        """Get cached result if valid."""
        if key not in self._cache:
            return None
        timestamp, result = self._cache[key]
        if time.monotonic() - timestamp > self.CACHE_TTL_SECONDS:
            del self._cache[key]
            return None
        return result

    def _set_cached(self, key: tuple[str, int], result: dict | None):
        """Cache result with timestamp."""
        # Simple LRU: remove oldest if at capacity
        if len(self._cache) >= self.CACHE_SIZE:
            oldest_key = min(self._cache.keys(), key=lambda k: self._cache[k][0])
            del self._cache[oldest_key]
        self._cache[key] = (time.monotonic(), result)

    def _init_schema(self):
        """Initialize clean v1.0 schema - matches exopilot_shared package."""
        conn = self._get_conn()
        # Main surface quality table - shared format with exopilot_shared
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

        # Indexes for performance
        conn.execute("CREATE INDEX IF NOT EXISTS idx_surface_geohash ON surface_quality(geohash)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_surface_timestamp ON surface_quality(timestamp)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_surface_source ON surface_quality(source)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_spatial ON surface_quality(geohash, heading_bucket)")

        conn.commit()

    def _detect_platform(self) -> str:
        """Detect ExoPilot platform (delegated to shared utility)."""
        return detect_exopilot_platform()

    def _heading_to_bucket(self, heading: float) -> int:
        """Convert heading to 8-direction bucket (0-7, 45-degree sectors)."""
        return heading_to_bucket(heading)

    def _get_heading_difference(self, h1: float, h2: float) -> float:
        """Get smallest angle difference between two headings."""
        diff = abs((h1 % 360) - (h2 % 360))
        if diff > 180:
            diff = 360 - diff
        return diff

    def _estimate_feature_type(self, shock_count: int, quality_score: float,
                               roughness_rms: float) -> str:
        """Estimate feature type from recorded data."""
        if shock_count >= 3:
            return SurfaceFeatureType.POTHOLE_CLUSTER.value
        elif shock_count == 1 or shock_count == 2:
            if quality_score > 0.7:
                return SurfaceFeatureType.RAILROAD_CROSSING.value
            return SurfaceFeatureType.POTHOLE.value
        elif quality_score > 0.8:
            return SurfaceFeatureType.CONSTRUCTION.value
        elif quality_score > 0.6:
            return SurfaceFeatureType.ROUGH_ROAD.value
        elif quality_score > 0.4:
            return SurfaceFeatureType.GRAVEL_TRANSITION.value
        return SurfaceFeatureType.UNKNOWN.value

    def _calculate_recommended_speed(self, quality_score: float,
                                     shock_count: int, v_ego_at_detection: float) -> float:
        """Calculate recommended speed based on surface quality."""
        # Base speed limits by quality
        if shock_count > 0:
            base_speed = 8.0  # ~30 kph for shocks
        elif quality_score > 0.7:
            base_speed = 12.0  # ~43 kph (EVP zone 1 ceiling)
        elif quality_score > 0.5:
            base_speed = 15.0  # ~55 kph
        elif quality_score > 0.3:
            base_speed = 20.0  # ~72 kph
        else:
            base_speed = 30.0  # ~108 kph

        # If driver slowed down more than our base, use their speed
        if v_ego_at_detection > 0 and v_ego_at_detection < base_speed:
            return v_ego_at_detection

        return base_speed

    def record_surface(
        self,
        lat: float,
        lon: float,
        quality_score: float,
        shock_count: int = 0,
        roughness_rms: float = 0.0,
        texture: str = 'unknown',
        heading: float = 0.0,
        v_ego_ms: float = 0.0,
        feature_type: str = None,
        section_length_m: float = 0.0,
        timestamp: float | None = None,
        source: str = None  # None = use detected platform
    ) -> bool:
        """
        Record surface quality at a location with enhanced data.

        Auto-detects feature type if not specified.
        Calculates recommended speed based on driver behavior.
        """
        if timestamp is None:
            timestamp = time.monotonic()

        # Normalize texture to valid DB values (CHECK constraint)
        _VALID_TEXTURES = ('smooth', 'normal', 'rough', 'very_rough')
        if texture not in _VALID_TEXTURES:
            texture = 'normal'

        # Use ExoPilot platform identifier
        if source == 'eop':
            source = get_default_source()

        # Use detected platform if source not specified
        if source is None:
            source = self.platform

        # Auto-detect feature type
        if feature_type is None:
            feature_type = self._estimate_feature_type(shock_count, quality_score, roughness_rms)

        # Calculate recommended speed
        recommended_speed = self._calculate_recommended_speed(quality_score, shock_count, v_ego_ms)

        geohash = _encode_geohash(lat, lon, self.GEOHASH_PRECISION)
        heading_bucket = self._heading_to_bucket(heading)

        try:
            conn = self._get_conn()
            # Look for existing record at this geohash + heading bucket
            cursor = conn.execute(
                """SELECT id, quality_score, record_count, shock_count, recommended_speed_ms
                   FROM surface_quality
                   WHERE geohash = ? AND heading_bucket = ?
                   LIMIT 1""",
                (geohash, heading_bucket)
            )
            existing = cursor.fetchone()

            if existing:
                record_id, old_score, record_count, old_shocks, old_speed = existing
                new_record_count = min(record_count + 1, 100)

                # Exponential moving average
                alpha = 1.0 / new_record_count
                new_score = old_score * (1 - alpha) + quality_score * alpha
                new_shocks = int(old_shocks * (1 - alpha) + shock_count * alpha)

                # Average recommended speed (weighted by confidence)
                old_conf = min(record_count / 10.0, 1.0)
                new_speed = (old_speed * old_conf + recommended_speed * alpha) / (old_conf + alpha) if (old_conf + alpha) > 0 else recommended_speed

                confidence = min(new_record_count / 10.0, 1.0)

                conn.execute(
                    """UPDATE surface_quality SET
                        lat = ?, lon = ?, heading_bucket = ?,
                        quality_score = ?, shock_count = ?, roughness_rms = ?,
                        texture = ?, feature_type = ?, recommended_speed_ms = ?,
                        section_length_m = ?, timestamp = ?, record_count = ?, confidence = ?,
                        source = ?
                    WHERE id = ?""",
                    (lat, lon, heading_bucket, new_score, new_shocks, roughness_rms,
                     texture, feature_type, new_speed, section_length_m,
                     timestamp, new_record_count, confidence, source, record_id)
                )
            else:
                confidence = 0.1
                conn.execute(
                    """INSERT INTO surface_quality
                    (geohash, lat, lon, heading_bucket, quality_score, timestamp, source,
                     texture, roughness_rms, feature_type, recommended_speed_ms,
                     section_length_m, shock_count, record_count, confidence)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                    (geohash, lat, lon, heading_bucket, quality_score, timestamp, source,
                     texture, roughness_rms, feature_type, recommended_speed,
                     section_length_m, shock_count, 1, confidence)
                )

            conn.commit()
            return True

        except sqlite3.Error as e:
            cloudlog.error(f"SurfaceQualityDB error: {e}")
            return False

    def lookup_surface(
        self,
        lat: float,
        lon: float,
        heading: float = 0.0,
        search_radius_m: float = 100.0,
        max_heading_diff: float = 90.0,
        use_cache: bool = True
    ) -> dict | None:
        """Look up surface quality at a location with heading matching.

        Args:
            lat, lon: GPS coordinates
            heading: Vehicle heading in degrees
            search_radius_m: Not used (geohash precision defines search area)
            max_heading_diff: Not used (heading bucket matching is exact)
            use_cache: Whether to use LRU cache (default True)
        """
        cache_key = self._cache_key(lat, lon, heading)
        if use_cache:
            cached = self._get_cached(cache_key)
            if cached is not None:
                return cached

        geohash = _encode_geohash(lat, lon, self.GEOHASH_PRECISION)
        heading_bucket = self._heading_to_bucket(heading)

        try:
            conn = self._get_conn()
            # Query by geohash and heading bucket (exact match first)
            cursor = conn.execute(
                """SELECT lat, lon, heading_bucket, quality_score, shock_count, roughness_rms,
                       texture, feature_type, recommended_speed_ms, section_length_m,
                       record_count, confidence, timestamp
                FROM surface_quality
                WHERE geohash = ? AND heading_bucket = ?
                ORDER BY confidence DESC, timestamp DESC
                LIMIT 1""",
                (geohash, heading_bucket)
            )
            row = cursor.fetchone()

            if row:
                result = {
                    'lat': row[0], 'lon': row[1], 'heading_bucket': row[2],
                    'quality_score': row[3], 'shock_count': row[4], 'roughness_rms': row[5],
                    'texture': row[6], 'feature_type': row[7],
                    'recommended_speed_ms': row[8], 'section_length_m': row[9],
                    'record_count': row[10], 'confidence': row[11], 'timestamp': row[12]
                }
                if use_cache:
                    self._set_cached(cache_key, result)
                return result

            # Fall back to geohash-only match with adjacent heading buckets
            adjacent_buckets = [
                (heading_bucket - 1) % 8,
                (heading_bucket + 1) % 8
            ]
            cursor = conn.execute(
                """SELECT lat, lon, heading_bucket, quality_score, shock_count, roughness_rms,
                       texture, feature_type, recommended_speed_ms, section_length_m,
                       record_count, confidence, timestamp
                FROM surface_quality
                WHERE geohash = ? AND heading_bucket IN (?, ?)
                ORDER BY confidence DESC, timestamp DESC
                LIMIT 1""",
                (geohash, adjacent_buckets[0], adjacent_buckets[1])
            )
            row = cursor.fetchone()

            if row:
                result = {
                    'lat': row[0], 'lon': row[1], 'heading_bucket': row[2],
                    'quality_score': row[3], 'shock_count': row[4], 'roughness_rms': row[5],
                    'texture': row[6], 'feature_type': row[7],
                    'recommended_speed_ms': row[8], 'section_length_m': row[9],
                    'record_count': row[10], 'confidence': row[11], 'timestamp': row[12]
                }
                if use_cache:
                    self._set_cached(cache_key, result)
                return result

            if use_cache:
                self._set_cached(cache_key, None)
            return None

        except sqlite3.Error as e:
            cloudlog.error(f"SurfaceQualityDB lookup error: {e}")
            return None

    def predict_ahead(
        self,
        lat: float,
        lon: float,
        heading: float,
        v_ego_ms: float,
        distances_m: list[float] = None,
        width_m: float = 10.0
    ) -> list[SurfacePrediction]:
        """
        Predict surface quality at multiple distances ahead.

        Args:
            lat, lon: Current GPS position
            heading: Vehicle heading in degrees
            v_ego_ms: Current speed (for time-to-impact calculation)
            distances_m: list of distances to check, defaults to [50, 100, 200, 300]
            width_m: Search width perpendicular to heading
        """
        if distances_m is None:
            # Default: look ahead 2s, 4s, 8s, 12s at current speed
            if v_ego_ms > 5.0:
                distances_m = [int(v_ego_ms * t) for t in [2, 4, 8, 12]]
            else:
                distances_m = [50, 100, 200, 300]

        predictions = []

        for distance in distances_m:
            dest_lat, dest_lon = _calculate_destination(lat, lon, heading, distance)

            surface_data = self.lookup_surface(
                dest_lat, dest_lon, heading=heading,
                search_radius_m=width_m, max_heading_diff=90.0
            )

            if surface_data:
                shock_prob = min(surface_data['shock_count'] / 10.0, 1.0)

                # Calculate heading match from bucket (0-7, 45° each)
                data_bucket = surface_data['heading_bucket']
                query_bucket = self._heading_to_bucket(heading)
                bucket_diff = abs(data_bucket - query_bucket)
                bucket_diff = min(bucket_diff, 8 - bucket_diff)  # Wrap around
                heading_match = bucket_diff * 45.0  # Convert to degrees

                heading_boost = 1.0 - (heading_match / 180.0)
                adjusted_confidence = surface_data['confidence'] * (0.7 + 0.3 * heading_boost)

                # Use stored recommended speed or calculate default
                rec_speed = surface_data['recommended_speed_ms']
                if rec_speed <= 0:
                    rec_speed = self._calculate_recommended_speed(
                        surface_data['quality_score'], surface_data['shock_count'], 0
                    )

                predictions.append(SurfacePrediction(
                    quality_score=surface_data['quality_score'],
                    texture=surface_data['texture'],
                    confidence=adjusted_confidence,
                    distance_m=distance,
                    shock_probability=shock_prob,
                    recommended_speed_ms=rec_speed,
                    feature_type=surface_data['feature_type'],
                    section_length_m=surface_data['section_length_m'],
                    source='history'
                ))
            else:
                predictions.append(SurfacePrediction(
                    quality_score=0.0, texture='unknown', confidence=0.0,
                    distance_m=distance, shock_probability=0.0,
                    recommended_speed_ms=0.0, feature_type='unknown',
                    section_length_m=0.0, source='unknown'
                ))

        return predictions

    def get_rough_sections_ahead(
        self,
        lat: float,
        lon: float,
        heading: float,
        max_distance_m: float = 500.0,
        min_quality_score: float = 0.5
    ) -> list[dict]:
        """
        Get all rough sections ahead within max_distance.

        Returns list of sections with recommended speeds.
        Useful for planning speed profile over longer distances.
        """
        sections = []
        check_distances = list(range(50, int(max_distance_m) + 1, 50))

        predictions = self.predict_ahead(lat, lon, heading, 0, check_distances)

        for pred in predictions:
            if pred.confidence > 0.3 and pred.quality_score >= min_quality_score:
                sections.append({
                    'distance_m': pred.distance_m,
                    'quality_score': pred.quality_score,
                    'recommended_speed_ms': pred.recommended_speed_ms,
                    'feature_type': pred.feature_type,
                    'confidence': pred.confidence
                })

        return sections

    def cleanup_old_data(self, max_age_days: int = 90):
        """Remove data older than specified days."""
        cutoff_time = time.monotonic() - (max_age_days * 24 * 3600)

        try:
            conn = self._get_conn()
            conn.execute("DELETE FROM surface_quality WHERE timestamp < ?", (cutoff_time,))
            conn.commit()
        except sqlite3.Error as e:
            cloudlog.error(f"SurfaceQualityDB cleanup error: {e}")

    def get_stats(self) -> dict:
        """Get database statistics."""
        try:
            conn = self._get_conn()
            cursor = conn.execute("""
                SELECT COUNT(*), AVG(confidence), AVG(quality_score),
                       AVG(recommended_speed_ms), SUM(shock_count)
                FROM surface_quality
            """)
            row = cursor.fetchone()

            # Count by feature type
            cursor2 = conn.execute("""
                SELECT feature_type, COUNT(*) FROM surface_quality
                GROUP BY feature_type ORDER BY COUNT(*) DESC
            """)
            feature_counts = {row[0]: row[1] for row in cursor2.fetchall()}

            return {
                    'total_segments': row[0] or 0,
                    'avg_confidence': row[1] or 0.0,
                    'avg_quality_score': row[2] or 0.0,
                    'avg_recommended_speed_kph': (row[3] or 0) * 3.6,
                    'total_shocks_recorded': row[4] or 0,
                    'feature_breakdown': feature_counts
                }
        except sqlite3.Error:
            return {'total_segments': 0}


def get_surface_prediction(
    lat: float,
    lon: float,
    heading: float,
    v_ego_ms: float,
    distance_m: float,
    db: SurfaceQualityDB | None = None
) -> SurfacePrediction | None:
    """Get surface prediction at a specific distance ahead."""
    if db is None:
        db = SurfaceQualityDB()

    predictions = db.predict_ahead(lat, lon, heading, v_ego_ms, [distance_m])
    return predictions[0] if predictions else None

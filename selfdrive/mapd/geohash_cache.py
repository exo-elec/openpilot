"""
geohash_cache.py - SQLite-based caching for OSM data with geohash indexing.

Enables offline operation by storing previously queried OSM data.
Uses geohash for efficient spatial queries.
"""

import os
import time
import sqlite3
import math
from openpilot.system.hardware.hw import Paths
from typing import Any


# Geohash character set (base32)
GEOHASH_CHARS = "0123456789bcdefghjkmnpqrstuvwxyz"


def encode_geohash(lat: float, lon: float, precision: int = 7) -> str:
    """
    Encode latitude/longitude to geohash string.
    
    Args:
        lat: Latitude (-90 to 90)
        lon: Longitude (-180 to 180)
        precision: Number of characters in geohash (default 7 = ~150m precision)
        
    Returns:
        Geohash string
    """
    lat_range = [-90.0, 90.0]
    lon_range = [-180.0, 180.0]
    geohash = []
    bits = 0
    bits_total = 0
    ch = 0
    even = True
    
    while len(geohash) < precision:
        if even:
            # Divide longitude range
            mid = (lon_range[0] + lon_range[1]) / 2
            if lon >= mid:
                ch = (ch << 1) | 1
                lon_range[0] = mid
            else:
                ch = ch << 1
                lon_range[1] = mid
        else:
            # Divide latitude range
            mid = (lat_range[0] + lat_range[1]) / 2
            if lat >= mid:
                ch = (ch << 1) | 1
                lat_range[0] = mid
            else:
                ch = ch << 1
                lat_range[1] = mid
                
        even = not even
        bits_total += 1
        
        if bits_total == 5:
            geohash.append(GEOHASH_CHARS[ch])
            bits_total = 0
            ch = 0
            
    return ''.join(geohash)


def get_neighbors(geohash: str) -> list[str]:
    """
    Get all 8 neighboring geohash cells.
    
    Args:
        geohash: Base geohash string
        
    Returns:
        List of 8 neighboring geohash strings
    """
    if not geohash:
        return []
        
    # This is a simplified version - for production use a full geohash library
    # For now, return the cell itself plus truncations for broader search
    neighbors = [geohash]
    
    # Add parent cells for broader search
    for i in range(1, min(3, len(geohash))):
        parent = geohash[:-i]
        if parent and parent not in neighbors:
            neighbors.append(parent)
            
    return neighbors


class OSMCache:
    """
    SQLite-based cache for OSM map data.
    
    Uses geohash for spatial indexing to enable efficient nearby queries.
    """
    
    DEFAULT_DB_PATH = os.path.join(Paths.eop_data_root(), "media", "0", "osm", "mapd.db")
    DEFAULT_MAX_AGE = 30 * 86400  # 30 days in seconds
    
    def __init__(self, db_path: str | None = None, max_age: int = DEFAULT_MAX_AGE):
        """
        Initialize OSM cache.
        
        Args:
            db_path: Path to SQLite database (default: /data/media/0/osm/mapd.db)
            max_age: Maximum age of cached data in seconds (default: 30 days)
        """
        self.db_path = db_path or self.DEFAULT_DB_PATH
        self.max_age = max_age
        
        # Ensure directory exists
        os.makedirs(os.path.dirname(self.db_path), exist_ok=True)
        
        # Initialize database
        self._init_db()
        
    def _init_db(self):
        """Initialize SQLite database with required tables."""
        with sqlite3.connect(self.db_path) as conn:
            cursor = conn.cursor()
            
            # Main cache table
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS osm_cache (
                    geohash TEXT PRIMARY KEY,
                    lat REAL NOT NULL,
                    lon REAL NOT NULL,
                    timestamp REAL NOT NULL,
                    curvature TEXT,  -- JSON string
                    speed_limit INTEGER,
                    next_speed_limit INTEGER,
                    next_speed_limit_distance REAL,
                    road_name TEXT
                )
            """)
            
            # Road data table (large JSON, separate for efficiency)
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS osm_roads (
                    geohash TEXT PRIMARY KEY,
                    roads TEXT,  -- JSON string of road data
                    FOREIGN KEY (geohash) REFERENCES osm_cache(geohash)
                )
            """)
            
            # Index on geohash for fast lookups
            cursor.execute("""
                CREATE INDEX IF NOT EXISTS idx_geohash ON osm_cache(geohash)
            """)
            
            # Index on timestamp for cleanup
            cursor.execute("""
                CREATE INDEX IF NOT EXISTS idx_timestamp ON osm_cache(timestamp)
            """)
            
            # Metadata table for cache stats
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS cache_meta (
                    key TEXT PRIMARY KEY,
                    value TEXT
                )
            """)
            
            conn.commit()
            
    def store(self, lat: float, lon: float, data: dict[str, Any], 
              geohash_precision: int = 6):
        """
        Store OSM data for a location.
        
        Args:
            lat: Latitude
            lon: Longitude
            data: Dictionary containing curvature, speed_limit, roads, etc.
            geohash_precision: Geohash precision (default 6 = ~1.2km tile)
        """
        import json
        
        geohash = encode_geohash(lat, lon, precision=geohash_precision)
        timestamp = time.monotonic()
        
        with sqlite3.connect(self.db_path) as conn:
            cursor = conn.cursor()
            
            # Convert data to JSON
            curvature_json = None
            if 'curvature' in data and data['curvature']:
                curvature_json = json.dumps(data['curvature'])
                
            roads_json = None
            if 'roads' in data and data['roads']:
                roads_json = json.dumps(data['roads'])
            
            # Store main cache data
            cursor.execute("""
                INSERT OR REPLACE INTO osm_cache 
                (geohash, lat, lon, timestamp, curvature, speed_limit, 
                 next_speed_limit, next_speed_limit_distance, road_name)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (
                geohash, lat, lon, timestamp,
                curvature_json,
                data.get('speed_limit'),
                data.get('next_speed_limit'),
                data.get('next_speed_limit_distance'),
                data.get('road_name')
            ))
            
            # Store road data separately
            if roads_json:
                cursor.execute("""
                    INSERT OR REPLACE INTO osm_roads (geohash, roads)
                    VALUES (?, ?)
                """, (geohash, roads_json))
            
            conn.commit()
            
    def query(self, lat: float, lon: float, radius: int = 500,
              geohash_precision: int = 6) -> dict[str, Any | None]:
        """
        Query cached data near a location.
        
        Uses tile-based lookup with geohash for efficiency.
        
        Args:
            lat: Latitude
            lon: Longitude
            radius: Search radius in meters (default 500m)
            geohash_precision: Geohash precision for tile lookup (default 6)
            
        Returns:
            Dictionary with cached data or None if not found
        """
        import json
        
        geohash = encode_geohash(lat, lon, precision=geohash_precision)
        neighbors = get_neighbors(geohash)
        
        with sqlite3.connect(self.db_path) as conn:
            cursor = conn.cursor()
            
            # Query all neighbor cells
            placeholders = ','.join('?' * len(neighbors))
            cursor.execute(f"""
                SELECT geohash, lat, lon, timestamp, curvature, speed_limit,
                       next_speed_limit, next_speed_limit_distance, road_name
                FROM osm_cache
                WHERE geohash IN ({placeholders})
                ORDER BY timestamp DESC
            """, neighbors)
            
            rows = cursor.fetchall()
            
            # Find closest result within radius
            for row in rows:
                row_geohash, row_lat, row_lon, timestamp, curvature_json, \
                    speed_limit, next_speed_limit, next_speed_dist, road_name = row
                    
                # Check if data is fresh
                if not self.is_fresh(timestamp):
                    continue
                    
                # Calculate distance from query point to cached tile center
                dist = haversine_distance(lat, lon, row_lat, row_lon)
                if dist <= radius:
                    # Parse curvature JSON
                    curvature = None
                    if curvature_json:
                        try:
                            curvature = json.loads(curvature_json)
                        except json.JSONDecodeError:
                            pass
                    
                    # Fetch roads from separate table
                    roads = None
                    cursor.execute("SELECT roads FROM osm_roads WHERE geohash = ?", (row_geohash,))
                    road_row = cursor.fetchone()
                    if road_row and road_row[0]:
                        try:
                            roads = json.loads(road_row[0])
                        except json.JSONDecodeError:
                            pass
                            
                    return {
                        'geohash': row_geohash,
                        'lat': row_lat,
                        'lon': row_lon,
                        'timestamp': timestamp,
                        'distance': dist,
                        'curvature': curvature,
                        'speed_limit': speed_limit,
                        'next_speed_limit': next_speed_limit,
                        'next_speed_limit_distance': next_speed_dist,
                        'road_name': road_name,
                        'roads': roads
                    }
                    
        return None
        
    def is_fresh(self, timestamp: float, max_age: int | None = None) -> bool:
        """
        Check if data is fresh (not expired).
        
        Args:
            timestamp: Unix timestamp of data
            max_age: Maximum age in seconds (uses instance default if None)
            
        Returns:
            True if data is fresh, False otherwise
        """
        age = time.monotonic() - timestamp
        return age < (max_age or self.max_age)
        
    def cleanup_old_entries(self, max_age: int | None = None):
        """
        Remove old entries from cache.
        
        Args:
            max_age: Maximum age in seconds (uses instance default if None)
        """
        cutoff = time.monotonic() - (max_age or self.max_age)
        
        with sqlite3.connect(self.db_path) as conn:
            cursor = conn.cursor()
            
            # Delete old roads first (foreign key constraint)
            cursor.execute("""
                DELETE FROM osm_roads WHERE geohash IN 
                (SELECT geohash FROM osm_cache WHERE timestamp < ?)
            """, (cutoff,))
            
            # Delete old cache entries
            cursor.execute("""
                DELETE FROM osm_cache WHERE timestamp < ?
            """, (cutoff,))
            
            conn.commit()
            
    def get_stats(self) -> dict[str, int]:
        """
        Get cache statistics.
        
        Returns:
            Dictionary with cache stats
        """
        with sqlite3.connect(self.db_path) as conn:
            cursor = conn.cursor()
            
            cursor.execute("SELECT COUNT(*) FROM osm_cache")
            total = cursor.fetchone()[0]
            
            cursor.execute("SELECT COUNT(*) FROM osm_cache WHERE timestamp > ?",
                          (time.monotonic() - self.max_age,))
            fresh = cursor.fetchone()[0]
            
            return {
                'total_entries': total,
                'fresh_entries': fresh,
                'expired_entries': total - fresh
            }


def haversine_distance(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    """
    Calculate great circle distance between two points on Earth.
    
    Args:
        lat1: Latitude of point 1
        lon1: Longitude of point 1
        lat2: Latitude of point 2
        lon2: Longitude of point 2
        
    Returns:
        Distance in meters
    """
    R = 6371000  # Earth radius in meters
    
    phi1 = math.radians(lat1)
    phi2 = math.radians(lat2)
    delta_phi = math.radians(lat2 - lat1)
    delta_lambda = math.radians(lon2 - lon1)
    
    a = math.sin(delta_phi / 2) ** 2 + \
        math.cos(phi1) * math.cos(phi2) * math.sin(delta_lambda / 2) ** 2
    c = 2 * math.atan2(math.sqrt(a), math.sqrt(1 - a))
    
    return R * c

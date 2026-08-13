#!/usr/bin/env python3
"""
osm_localizer.py - OSM road network map matching for coordinationd.

Provides map matching functionality to snap GNSS positions to the
nearest road in OpenStreetMap data.

Uses mapd's OSM cache for road geometry data.
"""

from __future__ import annotations

import math
import sqlite3
from pathlib import Path
from dataclasses import dataclass


from openpilot.common.swaglog import cloudlog
from openpilot.selfdrive.mapd.geohash_cache import OSMCache
from openpilot.selfdrive.coordinationd.fusion import RoadConstraint  # noqa: F401 — re-exported
from openpilot.system.hardware.hw import Paths


@dataclass
class OSMWay:
    """OSM road segment."""
    way_id: int
    name: str
    road_type: str
    lanes: int
    oneway: bool
    coordinates: list[tuple[float, float]]


@dataclass
class MapMatchResult:
    """Result of map matching."""
    matched_lat: float
    matched_lon: float
    matched_heading: float
    way_id: int
    road_name: str
    road_type: str
    lane_index: int
    distance_to_road: float
    confidence: float
    is_oneway: bool


class OSMCacheDB:
    """
    SQLite cache for OSM road data (localization use).
    Falls back to mapd's cache if available.
    """

    CACHE_PATH = Path(Paths.eop_data_root()) / "media" / "0" / "osm_localizer_cache.db"

    def __init__(self):
        self.CACHE_PATH.parent.mkdir(parents=True, exist_ok=True)
        self._init_db()
        # Also try to use mapd's cache
        try:
            self.mapd_cache = OSMCache()
        except Exception:
            self.mapd_cache = None

    def _init_db(self):
        """Initialize database."""
        with sqlite3.connect(self.CACHE_PATH) as conn:
            conn.execute("""
                CREATE TABLE IF NOT EXISTS ways (
                    way_id INTEGER PRIMARY KEY, name TEXT, road_type TEXT,
                    lanes INTEGER, oneway INTEGER,
                    lat_min REAL, lat_max REAL, lon_min REAL, lon_max REAL
                )
            """)
            conn.execute("""
                CREATE TABLE IF NOT EXISTS way_nodes (
                    way_id INTEGER, sequence INTEGER, lat REAL, lon REAL,
                    PRIMARY KEY (way_id, sequence)
                )
            """)
            conn.execute("""
                CREATE INDEX IF NOT EXISTS idx_spatial
                ON ways(lat_min, lat_max, lon_min, lon_max)
            """)
            conn.commit()

    def get_ways_in_bbox(self, lat_min: float, lat_max: float,
                         lon_min: float, lon_max: float) -> list[OSMWay]:
        """Get ways within bounding box."""
        ways = []
        try:
            with sqlite3.connect(self.CACHE_PATH) as conn:
                cursor = conn.execute(
                    """SELECT way_id, name, road_type, lanes, oneway FROM ways
                       WHERE lat_max >= ? AND lat_min <= ?
                         AND lon_max >= ? AND lon_min <= ?""",
                    (lat_min, lat_max, lon_min, lon_max)
                )

                for row in cursor.fetchall():
                    way_id, name, road_type, lanes, oneway = row
                    node_cursor = conn.execute(
                        """SELECT lat, lon FROM way_nodes
                           WHERE way_id = ? ORDER BY sequence""", (way_id,)
                    )
                    coordinates = [(lat, lon) for lat, lon in node_cursor.fetchall()]

                    ways.append(OSMWay(
                        way_id=way_id, name=name or "",
                        road_type=road_type or "unknown",
                        lanes=lanes or 2, oneway=bool(oneway),
                        coordinates=coordinates
                    ))
        except sqlite3.Error as e:
            cloudlog.error(f"OSM cache error: {e}")

        return ways

    def get_ways_from_mapd_cache(self, lat: float, lon: float,
                                  radius: float = 100.0) -> list[OSMWay]:
        """
        Try to get ways from mapd's cache.

        Args:
            lat: Latitude
            lon: Longitude
            radius: Search radius in meters

        Returns:
            List of OSMWay objects
        """
        if self.mapd_cache is None:
            return []

        try:
            cached = self.mapd_cache.query(lat, lon, radius=radius)
            if cached and 'roads' in cached and cached['roads']:
                ways = []
                for road in cached['roads']:
                    if 'nodes' in road:
                        ways.append(OSMWay(
                            way_id=road.get('id', 0),
                            name=road.get('name', ''),
                            road_type=road.get('highway', 'unknown'),
                            lanes=road.get('lanes', 2),
                            oneway=road.get('oneway', False),
                            coordinates=road['nodes']
                        ))
                return ways
        except Exception as e:
            cloudlog.debug(f"OSM: failed to query mapd cache: {e}")

        return []


class OSMLocalizerModule:
    """
    OSM road network matching module.

    Map-matches GNSS positions to the nearest road in OSM data.
    Uses both local cache and mapd's cache for road geometry.
    """

    def __init__(self):
        self.enabled = True
        self.max_search_distance = 50.0  # meters
        self.osm_db = OSMCacheDB()

        # Road type priority for matching
        self.road_priority = {
            "motorway": 10, "trunk": 9, "primary": 8, "secondary": 7,
            "tertiary": 6, "residential": 5, "service": 3, "unclassified": 4,
        }

        # Statistics
        self.match_count = 0
        self.miss_count = 0

    def get_ways_near_position(self, lat: float, lon: float,
                                radius: float = 100.0) -> list[OSMWay]:
        """
        Get OSM ways near position.

        First tries mapd's cache, then falls back to local cache.

        Args:
            lat: Latitude
            lon: Longitude
            radius: Search radius in meters

        Returns:
            List of OSMWay objects
        """
        # Try mapd cache first (has better data)
        ways = self.osm_db.get_ways_from_mapd_cache(lat, lon, radius)
        if ways:
            return ways

        # Fall back to local cache
        lat_offset = radius / 111320.0
        lon_offset = radius / (111320.0 * math.cos(math.radians(lat)))

        return self.osm_db.get_ways_in_bbox(
            lat - lat_offset, lat + lat_offset,
            lon - lon_offset, lon + lon_offset
        )

    def match(self, lat: float, lon: float, heading: float,
              speed: float) -> MapMatchResult | None:
        """
        Match position to OSM road network.

        Args:
            lat: Latitude
            lon: Longitude
            heading: Heading in degrees
            speed: Speed in m/s

        Returns:
            MapMatchResult if successful, None otherwise
        """
        ways = self.get_ways_near_position(lat, lon, self.max_search_distance)

        if not ways:
            self.miss_count += 1
            return None

        best_match = None
        best_score = float('inf')

        for way in ways:
            match = self._match_to_way(lat, lon, heading, way)
            if match and match.distance_to_road < best_score:
                best_score = match.distance_to_road
                best_match = match

        if best_match:
            self.match_count += 1
        else:
            self.miss_count += 1

        return best_match

    def _match_to_way(self, lat: float, lon: float,
                      heading: float, way: OSMWay) -> MapMatchResult | None:
        """
        Match position to a specific way.

        Args:
            lat: Latitude
            lon: Longitude
            heading: Heading in degrees
            way: OSMWay to match against

        Returns:
            MapMatchResult if match is valid, None otherwise
        """
        coords = way.coordinates
        if len(coords) < 2:
            return None

        min_distance = float('inf')
        closest_lat, closest_lon = lat, lon
        segment_heading = heading

        # Find closest point on any segment of the way
        for i in range(len(coords) - 1):
            lat1, lon1 = coords[i]
            lat2, lon2 = coords[i + 1]

            dist, proj_lat, proj_lon = self._point_to_segment_distance(
                lat, lon, lat1, lon1, lat2, lon2
            )

            if dist < min_distance:
                min_distance = dist
                closest_lat, closest_lon = proj_lat, proj_lon
                segment_heading = self._bearing_between(lat1, lon1, lat2, lon2)

        # Convert distance to meters (approximate)
        distance_m = min_distance * 111320.0

        if distance_m > self.max_search_distance:
            return None

        # Check heading consistency (handle bidirectional roads)
        heading_diff = self._angle_difference(heading, segment_heading)
        if heading_diff > 90:
            reverse_heading = (segment_heading + 180) % 360
            reverse_diff = self._angle_difference(heading, reverse_heading)
            if reverse_diff < heading_diff:
                segment_heading = reverse_heading
                heading_diff = reverse_diff

        # Calculate confidence based on distance and heading
        distance_conf = max(0.0, 1.0 - distance_m / self.max_search_distance)
        heading_conf = max(0.0, 1.0 - heading_diff / 90.0)
        confidence = distance_conf * heading_conf

        # Boost confidence for higher priority roads
        road_priority = self.road_priority.get(way.road_type, 5)
        confidence *= (0.8 + 0.02 * road_priority)  # 0.8 to 1.0 boost
        confidence = min(1.0, confidence)

        return MapMatchResult(
            matched_lat=closest_lat, matched_lon=closest_lon,
            matched_heading=segment_heading, way_id=way.way_id,
            road_name=way.name, road_type=way.road_type,
            lane_index=0, distance_to_road=distance_m,
            confidence=confidence, is_oneway=way.oneway
        )

    @staticmethod
    def _point_to_segment_distance(px: float, py: float, x1: float, y1: float,
                                    x2: float, y2: float) -> tuple[float, float, float]:
        """
        Calculate distance from point to line segment.

        Args:
            px, py: Point coordinates
            x1, y1: Segment start
            x2, y2: Segment end

        Returns:
            (distance, closest_x, closest_y)
        """
        dx, dy = x2 - x1, y2 - y1
        if dx == 0 and dy == 0:
            return math.sqrt((px - x1)**2 + (py - y1)**2), x1, y1

        t = max(0.0, min(1.0, ((px - x1) * dx + (py - y1) * dy) / (dx**2 + dy**2)))
        closest_x = x1 + t * dx
        closest_y = y1 + t * dy
        distance = math.sqrt((px - closest_x)**2 + (py - closest_y)**2)

        return distance, closest_x, closest_y

    @staticmethod
    def _bearing_between(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
        """
        Calculate bearing from point 1 to point 2.

        Args:
            lat1, lon1: Start point
            lat2, lon2: End point

        Returns:
            Bearing in degrees (0-360)
        """
        lat1_rad, lat2_rad = math.radians(lat1), math.radians(lat2)
        delta_lon = math.radians(lon2 - lon1)

        x = math.sin(delta_lon) * math.cos(lat2_rad)
        y = (math.cos(lat1_rad) * math.sin(lat2_rad) -
             math.sin(lat1_rad) * math.cos(lat2_rad) * math.cos(delta_lon))

        bearing = math.degrees(math.atan2(x, y))
        return (bearing + 360) % 360

    @staticmethod
    def _angle_difference(angle1: float, angle2: float) -> float:
        """
        Calculate smallest angle difference.

        Args:
            angle1, angle2: Angles in degrees

        Returns:
            Smallest difference in degrees (0-180)
        """
        diff = abs((angle1 % 360) - (angle2 % 360))
        return 360 - diff if diff > 180 else diff

    def get_stats(self) -> dict:
        """Get module statistics."""
        total = self.match_count + self.miss_count
        return {
            'matches': self.match_count,
            'misses': self.miss_count,
            'match_rate': self.match_count / total if total > 0 else 0.0
        }

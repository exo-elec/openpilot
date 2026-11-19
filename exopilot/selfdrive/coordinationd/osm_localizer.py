#!/usr/bin/env python3
"""
osm_localizer.py - OSM road matching for coordinationd, from the offline mapd (third_party/mapd).

mapd already matches the car to an OSM way (mapdOut: wayId, distanceFromWayCenter, oneWay, lanes, highwayClass) and
publishes that way and the ways ahead as a polyline in travel order (mapdExtendedOut.path). This module projects the
position onto that polyline, which gives the point on the road centre line and the road's heading there.
"""

from __future__ import annotations

import math
from dataclasses import dataclass

from exopilot.selfdrive.coordinationd.fusion import RoadConstraint  # noqa: F401 — re-exported

EARTH_RADIUS = 6371000.0


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


class OSMLocalizerModule:
  """Snap a position to the road mapd has matched."""

  # Higher classes are boosted a little: a match onto a motorway is rarely wrong.
  ROAD_PRIORITY = {
    "motorway": 10, "trunk": 9, "primary": 8, "secondary": 7,
    "tertiary": 6, "residential": 5, "service": 3, "unclassified": 4,
  }

  def __init__(self):
    self.max_search_distance = 50.0  # meters
    self.match_count = 0
    self.miss_count = 0

  def match(self, lat: float, lon: float, heading: float, mapd_out, path) -> MapMatchResult | None:
    """Match a position (deg) and heading (deg, 0 = north) to mapd's road.

    mapd_out: a mapdOut reader; path: mapdExtendedOut.path (MapdPathPoint list, travel order).
    """
    result = None
    if mapd_out is not None and mapd_out.tileLoaded and mapd_out.wayId != 0 and len(path) >= 2:
      result = self._match_to_path(lat, lon, heading, mapd_out, [(p.latitude, p.longitude) for p in path])
    if result is None:
      self.miss_count += 1
    else:
      self.match_count += 1
    return result

  def _match_to_path(self, lat, lon, heading, mapd_out, coords) -> MapMatchResult | None:
    # local east/north metres around the position; plenty accurate over a few km
    cos_lat = math.cos(math.radians(lat))

    def to_xy(p_lat, p_lon):
      return (math.radians(p_lon - lon) * cos_lat * EARTH_RADIUS, math.radians(p_lat - lat) * EARTH_RADIUS)

    pts = [to_xy(*c) for c in coords]
    best = None  # (distance, x, y, segment bearing)
    for (x1, y1), (x2, y2) in zip(pts, pts[1:], strict=False):
      dx, dy = x2 - x1, y2 - y1
      seg2 = dx * dx + dy * dy
      if seg2 == 0.0:
        continue
      t = max(0.0, min(1.0, -(x1 * dx + y1 * dy) / seg2))
      px, py = x1 + t * dx, y1 + t * dy
      dist = math.hypot(px, py)
      if best is None or dist < best[0]:
        best = (dist, px, py, math.degrees(math.atan2(dx, dy)) % 360.0)
    if best is None or best[0] > self.max_search_distance:
      return None
    dist, px, py, bearing = best

    road_type = str(mapd_out.highwayClass)
    heading_diff = self._angle_difference(heading, bearing)
    distance_conf = max(0.0, 1.0 - dist / self.max_search_distance)
    heading_conf = max(0.0, 1.0 - heading_diff / 90.0)
    confidence = distance_conf * heading_conf * (0.8 + 0.02 * self.ROAD_PRIORITY.get(road_type, 5))

    return MapMatchResult(
      matched_lat=lat + math.degrees(py / EARTH_RADIUS),
      matched_lon=lon + math.degrees(px / (EARTH_RADIUS * cos_lat)),
      matched_heading=bearing, way_id=int(mapd_out.wayId),
      road_name=mapd_out.roadName or mapd_out.wayName, road_type=road_type,
      lane_index=0, distance_to_road=dist, confidence=min(1.0, confidence), is_oneway=bool(mapd_out.oneWay),
    )

  @staticmethod
  def _angle_difference(angle1: float, angle2: float) -> float:
    """Smallest difference between two angles in degrees (0-180)."""
    diff = abs((angle1 % 360) - (angle2 % 360))
    return 360 - diff if diff > 180 else diff

  def get_stats(self) -> dict:
    total = self.match_count + self.miss_count
    return {'matches': self.match_count, 'misses': self.miss_count,
            'match_rate': self.match_count / total if total > 0 else 0.0}

#!/usr/bin/env python3
"""
mapdd.py - Map Daemon

Provides OSM-based road geometry, speed limits, and curvature data
for MTSC (Map Turn Speed Control) and MSLC (Map Speed Limit Control).

Uses tile-based pre-loading with aggressive caching for offline operation.
"""

import time
import math
import threading

import cereal.messaging as messaging
from openpilot.common.realtime import Ratekeeper
from openpilot.common.core_config import set_daemon_affinity
from openpilot.common.params import Params
from openpilot.common.swaglog import cloudlog

from openpilot.selfdrive.mapd.geohash_cache import OSMCache, encode_geohash, haversine_distance
from openpilot.selfdrive.mapd.osm_client import query_road_geometry, parse_speed_limit
from openpilot.selfdrive.mapd.curvature_calc import parse_osm_geometry, get_upcoming_curves


# Tile configuration
TILE_SIZE_METERS = 2000  # 2km x 2km tiles
MIN_QUERY_INTERVAL = 5.0  # Minimum seconds between OSM queries
CACHE_HIT_RADIUS = 500  # meters - use cache if within this distance


class MapD:
  """
  Map Daemon - provides OSM-based road geometry and speed limits.
  
  Uses tile-based pre-loading:
  1. Divide world into 2km x 2km tiles
  2. Check if current tile is cached
  3. If not cached, fetch entire tile from OSM
  4. Cache for future use (30 days)
  
  This minimizes API calls and enables offline operation.
  """
  
  def __init__(self):
    set_daemon_affinity("mapd")
    self.pm = messaging.PubMaster(['mapData'])
    self.sm = messaging.SubMaster(['gpsLocationExternal', 'fusedPosition', 'navRoute'])
    
    self.params = Params()
    self.enabled = self.params.get_bool("EOPMapdEnabled")
    
    # OSM cache
    self.cache = OSMCache()
    
    # Current state
    self.current_position = None
    self.current_tile = None  # Current geohash tile
    self.last_query_time = 0.0
    self.query_in_progress = False
    
    # Cached data for current area
    self.nearby_roads = []
    self.current_road = None
    self.upcoming_curves = []
    self.current_speed_limit = 0
    self.next_speed_limit = 0
    self.next_speed_limit_distance = 0
    
    # Stats
    self.cache_hits = 0
    self.cache_misses = 0
    
  def get_tile_bounds(self, lat: float, lon: float) -> tuple[str, float, float, float, float]:
    """
    Get tile geohash and bounding box for a location.
    
    Returns:
      (geohash, min_lat, max_lat, min_lon, max_lon)
    """
    # Use geohash with precision 6 (~1.2km x 0.6km)
    geohash = encode_geohash(lat, lon, precision=6)
    
    # Calculate bounding box (approximate)
    # 1 degree lat ~ 111km, 1 degree lon varies by latitude
    lat_span = TILE_SIZE_METERS / 111000.0  # degrees
    lon_span = TILE_SIZE_METERS / (111000.0 * math.cos(math.radians(lat)))
    
    min_lat = lat - lat_span / 2
    max_lat = lat + lat_span / 2
    min_lon = lon - lon_span / 2
    max_lon = lon + lon_span / 2
    
    return geohash, min_lat, max_lat, min_lon, max_lon
    
  def update_position(self) -> bool:
    """Update current position, preferring fusedPosition over raw GPS."""
    # Prefer coordinationd's fused position (road-constrained, SGM/OSM corrected)
    if self.sm.updated['fusedPosition']:
      fused = self.sm['fusedPosition']
      if fused.confidence > 0.3:
        self.current_position = {
          'lat': fused.positionGeodetic.latitude,
          'lon': fused.positionGeodetic.longitude,
          'bearing': self.current_position['bearing'] if self.current_position else 0.0,
          'accuracy': fused.positionECEF.xStd,
        }
        return True

    # Fallback: raw GPS (cold-start or coordinationd not running)
    if not self.sm.updated['gpsLocationExternal']:
      return False

    gps = self.sm['gpsLocationExternal']
    if gps.accuracy > 20:  # Require <20m accuracy
      return False

    self.current_position = {
      'lat': gps.latitude,
      'lon': gps.longitude,
      'bearing': gps.bearingDeg,
      'accuracy': gps.accuracy,
    }
    return True
    
  def check_tile_cache(self, lat: float, lon: float) -> bool:
    """
    Check if current tile is cached.
    
    Returns:
      True if cached and fresh, False if needs fetching
    """
    tile_hash, _, _, _, _ = self.get_tile_bounds(lat, lon)
    
    # Check if we're in same tile as before
    if tile_hash == self.current_tile:
      return True
      
    # Query cache for this location
    cached = self.cache.query(lat, lon, radius=CACHE_HIT_RADIUS)
    
    if cached and self.cache.is_fresh(cached['timestamp']):
      self.cache_hits += 1
      self.current_tile = tile_hash
      cloudlog.debug(f"MapD: Cache hit for tile {tile_hash}")
      return True
      
    self.cache_misses += 1
    return False
    
  def fetch_tile(self, lat: float, lon: float):
    """
    Fetch map tile from OSM.
    
    This runs in background thread to avoid blocking.
    """
    if self.query_in_progress:
      return
      
    # Rate limiting
    elapsed = time.monotonic() - self.last_query_time
    if elapsed < MIN_QUERY_INTERVAL:
      return
      
    self.query_in_progress = True
    
    def do_fetch():
      try:
        cloudlog.info(f"MapD: Fetching tile at {lat:.6f}, {lon:.6f}")
        
        # Query road geometry (includes curvature)
        osm_data = query_road_geometry(lat, lon, radius=TILE_SIZE_METERS // 2)
        
        if osm_data:
          # Parse and cache
          roads = parse_osm_geometry(osm_data)
          
          # Extract speed limits
          speed_limit = parse_speed_limit(osm_data)
          
          # Calculate curvature for storage
          all_curvature = []
          for road in roads:
            if road['curvature']:
              all_curvature.extend(road['curvature'])
              
          # Store in cache
          self.cache.store(lat, lon, {
            'curvature': all_curvature,
            'speed_limit': speed_limit,
            'roads': roads
          })
          
          self.nearby_roads = roads
          cloudlog.info(f"MapD: Cached tile with {len(roads)} roads")
          
        self.last_query_time = time.monotonic()
        
      except Exception as e:
        cloudlog.error(f"MapD: Failed to fetch tile: {e}")
        
      finally:
        self.query_in_progress = False
        
    # Run in background thread
    thread = threading.Thread(target=do_fetch, daemon=True)
    thread.start()
    
  def find_current_road(self) -> dict | None:
    """Find which road the vehicle is currently on."""
    if not self.current_position or not self.nearby_roads:
      return None
      
    lat = self.current_position['lat']
    lon = self.current_position['lon']
    
    nearest_road = None
    min_distance = float('inf')
    
    for road in self.nearby_roads:
      for node in road['nodes']:
        dist = haversine_distance(lat, lon, node[0], node[1])
        if dist < min_distance:
          min_distance = dist
          nearest_road = road
          
    # Only accept if within 50m
    if min_distance < 50:
      return nearest_road
      
    return None
    
  def update_curvature_data(self):
    """Update upcoming curvature data for MTSC."""
    if not self.current_road or not self.current_position:
      self.upcoming_curves = []
      return
      
    lat = self.current_position['lat']
    lon = self.current_position['lon']
    
    # Get upcoming curves on current road
    self.upcoming_curves = get_upcoming_curves(
      self.current_road, lat, lon, lookahead=500
    )
    
  def update_speed_limits(self):
    """Update speed limit data for MSLC."""
    # Current limit from current road
    if self.current_road and self.current_road['speed_limit']:
      self.current_speed_limit = self.current_road['speed_limit']
    else:
      self.current_speed_limit = 0
    
    # Find next speed limit and distance by looking ahead on nearby roads
    self.next_speed_limit = 0
    self.next_speed_limit_distance = 0
    
    if not self.current_position or not self.nearby_roads:
      return
    
    lat = self.current_position['lat']
    lon = self.current_position['lon']
    
    # Look for the nearest road ahead with a different speed limit
    nearest_change_dist = float('inf')
    
    for road in self.nearby_roads:
      # Skip current road (we already have its speed limit)
      if road is self.current_road:
        continue
      
      # Check if this road has a speed limit
      if not road.get('speed_limit'):
        continue
      
      # Find nearest node on this road
      for node in road['nodes']:
        dist = haversine_distance(lat, lon, node[0], node[1])
        # Only consider roads ahead (within 90° of bearing)
        if dist < nearest_change_dist and dist > 10:  # At least 10m ahead
          # Simple bearing check: road should be ahead of vehicle
          if self._is_ahead(node[0], node[1]):
            nearest_change_dist = dist
            self.next_speed_limit = road['speed_limit']
            self.next_speed_limit_distance = int(dist)
    
    # If no different limit found ahead, keep current
    if nearest_change_dist == float('inf'):
      self.next_speed_limit = 0
      self.next_speed_limit_distance = 0
  
  def _is_ahead(self, node_lat: float, node_lon: float) -> bool:
    """Check if a node is ahead of the vehicle (within ~90° of bearing)."""
    if not self.current_position:
      return True  # Default to True if no bearing available
    
    bearing = self.current_position.get('bearing', 0.0)
    lat = self.current_position['lat']
    lon = self.current_position['lon']
    
    # Calculate bearing to node
    dlon = math.radians(node_lon - lon)
    lat1 = math.radians(lat)
    lat2 = math.radians(node_lat)
    
    y = math.sin(dlon) * math.cos(lat2)
    x = math.cos(lat1) * math.sin(lat2) - math.sin(lat1) * math.cos(lat2) * math.cos(dlon)
    
    node_bearing = (math.degrees(math.atan2(y, x)) + 360) % 360
    
    # Check if node is within 90° of vehicle bearing (ahead)
    bearing_diff = abs(node_bearing - bearing)
    if bearing_diff > 180:
      bearing_diff = 360 - bearing_diff
    
    return bearing_diff < 90
    
  def update(self):
    """Main update loop - called at 1Hz."""
    if not self.enabled:
      return
      
    # Update inputs
    self.sm.update(0)
    
    if not self.update_position():
      return
      
    lat = self.current_position['lat']
    lon = self.current_position['lon']
    
    # Check if we have cached data for this area
    if not self.check_tile_cache(lat, lon):
      # Need to fetch new tile
      self.fetch_tile(lat, lon)
      
    # Load data from cache (may be from previous fetch)
    cached = self.cache.query(lat, lon, radius=CACHE_HIT_RADIUS)
    if cached:
      if 'roads' in cached and cached['roads']:
        self.nearby_roads = cached['roads']
      if cached.get('speed_limit'):
        self.current_speed_limit = cached['speed_limit']
        
    # Find current road
    self.current_road = self.find_current_road()
    
    # Update derived data
    self.update_curvature_data()
    self.update_speed_limits()
    
    # Publish results
    self.publish_map_data()
    
  def publish_map_data(self):
    """Publish mapData cereal message."""
    dat = messaging.new_message('mapData')
    dat.valid = True
    
    md = dat.mapData
    md.hasMapData = len(self.upcoming_curves) > 0 or self.current_speed_limit > 0
    
    # Current position
    if self.current_position:
      md.currentLatitude = self.current_position['lat']
      md.currentLongitude = self.current_position['lon']
      
    # Upcoming curvature for MTSC — x=distance(m), y=curvature(1/m)
    curves = self.upcoming_curves  # list of (distance, curvature) tuples
    if curves:
      cp = md.init('upcomingCurvatureDEPRECATED', len(curves))
      for i, (dist, curv) in enumerate(curves):
        cp[i].x = float(dist)
        cp[i].y = float(curv)
    else:
      md.init('upcomingCurvatureDEPRECATED', 0)
    md.curvatureValid = len(curves) > 0
    
    # Speed limit data for MSLC
    md.speedLimit = self.current_speed_limit
    md.nextSpeedLimit = self.next_speed_limit
    md.nextSpeedLimitDistance = self.next_speed_limit_distance
    
    # Road info
    if self.current_road:
      md.roadName = self.current_road.get('name', '')
      md.roadType = self.current_road.get('highway', '')
    else:
      md.roadName = ""
      md.roadType = ""
      
    # Debug stats
    md.cacheHits = self.cache_hits
    md.cacheMisses = self.cache_misses
    
    self.pm.send('mapData', dat)
    
  def run(self):
    """Main daemon loop."""
    cloudlog.info("MapD: Starting daemon")
    
    rk = Ratekeeper(1.0)  # 1 Hz update rate
    
    while True:
      self.update()
      rk.keep_time()


def main():
  mapd = MapD()
  mapd.run()


if __name__ == "__main__":
  main()

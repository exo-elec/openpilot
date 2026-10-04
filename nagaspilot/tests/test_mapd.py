"""mapd: OSM parsing, curvature lookahead, speed limits, tile cache, and the daemon's data flow with a recorded-style Overpass response.

The fixture is a synthetic Overpass JSON (a primary road, 80 km/h, a 100 m radius right-hand bend 280-440 m ahead). The network is
never touched: the daemon's fetch function is injected.
"""
import math
from types import SimpleNamespace as NS

import pytest

from nagaspilot.mapd.curvature_calc import get_upcoming_curves, parse_osm_geometry
from nagaspilot.mapd.geohash_cache import OSMCache, encode_geohash, haversine_distance
from nagaspilot.mapd.osm_client import parse_speed_limit

LAT0, LON0 = 13.7500, 100.5000


def pt(dn, de):
  return {'lat': LAT0 + dn / 111320.0, 'lon': LON0 + de / (111320.0 * math.cos(math.radians(LAT0)))}


def overpass(maxspeed='80'):
  geom = [pt(n * 20.0, 0) for n in range(15)]
  r = 100.0
  for a in range(10, 91, 10):
    t = math.radians(a)
    geom.append(pt(280 + r * math.sin(t), r * (1 - math.cos(t))))
  geom += [pt(280 + r, r + k * 20.0) for k in range(1, 10)]
  return {'elements': [{'type': 'way', 'id': 1, 'tags': {'highway': 'primary', 'maxspeed': maxspeed, 'name': 'Test Rd'}, 'geometry': geom}]}


def test_geometry_curvature_and_speed_limit():
  roads = parse_osm_geometry(overpass())
  assert len(roads) == 1 and roads[0]['speed_limit'] == 80 and roads[0]['name'] == 'Test Rd' and len(roads[0]['nodes']) == 33
  curves = get_upcoming_curves(roads[0], LAT0, LON0, lookahead=500)
  assert curves and min(d for d, _ in curves) > 250 and abs(max(c for _, c in curves) - 0.01) < 0.002       # 100 m radius
  assert get_upcoming_curves(roads[0], LAT0, LON0, lookahead=100) == [] or all(d < 100 for d, _ in get_upcoming_curves(roads[0], LAT0, LON0, lookahead=100))
  assert parse_speed_limit(overpass('80')) == 80 and parse_speed_limit(overpass('50 mph')) == 80          # mph converted
  assert parse_speed_limit({'elements': []}) is None and parse_speed_limit(None) is None


def test_geohash_distance_and_the_tile_cache_round_trip(tmp_path):
  assert encode_geohash(LAT0, LON0, 6) == encode_geohash(LAT0 + 0.0001, LON0, 6)
  assert abs(haversine_distance(LAT0, LON0, LAT0 + 0.001, LON0) - 111.3) < 1.0
  cache = OSMCache(db_path=str(tmp_path / 'mapd.db'))
  roads = parse_osm_geometry(overpass())
  cache.store(LAT0, LON0, {'curvature': [], 'speed_limit': 80, 'roads': roads})
  hit = cache.query(LAT0, LON0, radius=500)
  assert hit and hit['speed_limit'] == 80 and hit['roads'][0]['name'] == 'Test Rd' and cache.is_fresh(hit['timestamp'])
  assert cache.query(LAT0 + 1.0, LON0, radius=500) is None


def daemon():
  try:
    from nagaspilot.mapd import mapd as mod
  except Exception as e:           # needs a built cereal (EOP10 clone / device); pure runners skip it
    pytest.skip(f"built cereal needed: {e}")
  sent = []
  d = mod.MapD.__new__(mod.MapD)
  d.pm = NS(send=lambda n, m: sent.append(m))
  d.enabled = True
  d.upcoming_curves, d.nearby_roads, d.current_road = [], parse_osm_geometry(overpass()), None
  d.current_speed_limit = d.next_speed_limit = d.next_speed_limit_distance = 0
  d.cache_hits = d.cache_misses = 0
  d.current_position = {'lat': LAT0, 'lon': LON0, 'bearing': 0.0, 'accuracy': 3.0}
  return d, sent, mod


def test_daemon_finds_the_road_and_publishes_curves_and_the_limit():
  d, sent, mod = daemon()
  d.current_road = d.find_current_road()
  assert d.current_road and d.current_road['name'] == 'Test Rd'
  d.update_curvature_data()
  d.update_speed_limits()
  assert d.upcoming_curves and d.current_speed_limit == 80
  d.publish_map_data()
  md = sent[-1].mapData
  assert md.hasMapData and md.curvatureValid and md.speedLimit == 80 and len(md.upcomingCurvatureDEPRECATED) == len(d.upcoming_curves)


def test_off_the_road_means_no_current_road():
  d, sent, mod = daemon()
  d.current_position = {'lat': LAT0 + 0.01, 'lon': LON0 + 0.01, 'bearing': 0.0, 'accuracy': 3.0}      # about 1.5 km away
  assert d.find_current_road() is None

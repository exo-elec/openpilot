"""coordinationd's OSM match on the offline mapd's output (mapdOut + mapdExtendedOut.path)."""
import math

import cereal.messaging as messaging
from cereal import custom

from exopilot.selfdrive.coordinationd.osm_localizer import EARTH_RADIUS, OSMLocalizerModule

LAT0, LON0 = 13.7563, 100.5018  # Bangkok


def offset(east_m: float, north_m: float) -> tuple[float, float]:
  return (LAT0 + math.degrees(north_m / EARTH_RADIUS),
          LON0 + math.degrees(east_m / (EARTH_RADIUS * math.cos(math.radians(LAT0)))))


def mapd_messages(points_en, way_id=4242, tile_loaded=True, one_way=True):
  out = messaging.new_message('mapdOut')
  out.mapdOut.wayId = way_id
  out.mapdOut.tileLoaded = tile_loaded
  out.mapdOut.oneWay = one_way
  out.mapdOut.roadName = "Rama IV"
  out.mapdOut.highwayClass = custom.HighwayClass.primary
  ext = messaging.new_message('mapdExtendedOut')
  path = ext.mapdExtendedOut.init('path', len(points_en))
  for i, (e, n) in enumerate(points_en):
    path[i].latitude, path[i].longitude = offset(e, n)
  return out.mapdOut, ext.mapdExtendedOut.path


def test_projects_onto_the_road_mapd_matched():
  # road runs north along x = 0; the car is 3 m east of it, heading north
  out, path = mapd_messages([(0, -100), (0, 0), (0, 100), (0, 200)])
  lat, lon = offset(3.0, 20.0)
  m = OSMLocalizerModule().match(lat, lon, 0.0, out, path)
  assert m is not None
  assert abs(m.distance_to_road - 3.0) < 0.05
  e_lat, e_lon = offset(0.0, 20.0)
  assert abs(m.matched_lat - e_lat) < 1e-7 and abs(m.matched_lon - e_lon) < 1e-7
  assert m.matched_heading < 0.5 or m.matched_heading > 359.5
  assert (m.way_id, m.road_name, m.road_type, m.is_oneway) == (4242, "Rama IV", "primary", True)
  assert m.confidence > 0.8


def test_heading_comes_from_the_segment_we_are_on():
  # north, then a corner to the east
  out, path = mapd_messages([(0, 0), (0, 100), (100, 100)])
  m = OSMLocalizerModule().match(*offset(50.0, 101.0), 90.0, out, path)
  assert abs(m.matched_heading - 90.0) < 0.5
  assert abs(m.distance_to_road - 1.0) < 0.05


def test_driving_against_the_path_lowers_confidence():
  out, path = mapd_messages([(0, -100), (0, 100)])
  loc = OSMLocalizerModule()
  ahead = loc.match(*offset(2.0, 0.0), 0.0, out, path)
  across = loc.match(*offset(2.0, 0.0), 80.0, out, path)
  assert across.confidence < 0.2 * ahead.confidence


def test_no_match_without_a_usable_mapd_result():
  loc = OSMLocalizerModule()
  pos = offset(1.0, 0.0)
  assert loc.match(*pos, 0.0, *mapd_messages([(0, -10), (0, 10)], tile_loaded=False)) is None
  assert loc.match(*pos, 0.0, *mapd_messages([(0, -10), (0, 10)], way_id=0)) is None
  assert loc.match(*pos, 0.0, *mapd_messages([(0, 0)])) is None
  assert loc.match(*offset(80.0, 0.0), 0.0, *mapd_messages([(0, -10), (0, 10)])) is None  # beyond 50 m
  assert loc.get_stats()['misses'] == 4

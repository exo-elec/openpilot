"""pathd's long-horizon planner reads the offline mapd: road segments from mapdOut + mapdExtendedOut."""
import math

import cereal.messaging as messaging
from cereal import custom

from exopilot.selfdrive.pathd.osm_hybrid_planner import MapDataParser

LAT0, LON0 = 13.7563, 100.5018
R = 6371000.0


def north(m: float) -> tuple[float, float]:
  return LAT0 + math.degrees(m / R), LON0


def messages(curvatures, spacing=20.0, speed_limit=22.0, next_limit=0.0, next_at=0.0, tile_loaded=True):
  out = messaging.new_message('mapdOut').mapdOut
  out.tileLoaded = tile_loaded
  out.speedLimit = speed_limit
  out.nextSpeedLimit = next_limit
  out.nextSpeedLimitDistance = next_at
  out.highwayClass = custom.HighwayClass.trunk
  out.lanes = 3
  ext = messaging.new_message('mapdExtendedOut').mapdExtendedOut
  ext.position.latitude, ext.position.longitude = north(0.0)
  path = ext.init('path', len(curvatures))
  for i, c in enumerate(curvatures):
    path[i].latitude, path[i].longitude = north(i * spacing)
    path[i].curvature = c
  return out, ext


def test_straight_road_is_cut_every_100_m_to_500():
  segs = MapDataParser().parse(*messages([0.0] * 40))
  assert segs[0].start_m == 0.0 and segs[-1].end_m == 500.0
  assert all(b.start_m == a.end_m for a, b in zip(segs, segs[1:], strict=False))
  assert all(s.end_m - s.start_m <= 120.0 for s in segs)
  assert {(s.road_type, s.num_lanes, s.speed_limit_ms) for s in segs} == {("trunk", 3, 22.0)}


def test_a_curve_on_the_path_becomes_a_curved_segment():
  curv = [0.0] * 10 + [0.01] * 5 + [0.0] * 25  # 200-280 m: 100 m radius
  segs = MapDataParser().parse(*messages(curv))
  curved = [s for s in segs if s.is_curve]
  assert curved and abs(curved[0].start_m - 200.0) < 1.0 and abs(curved[0].curvature_1pm - 0.01) < 1e-6
  assert abs(curved[-1].end_m - 300.0) < 1.0


def test_the_next_speed_limit_applies_from_its_distance():
  segs = MapDataParser().parse(*messages([0.0] * 40, speed_limit=22.0, next_limit=13.9, next_at=250.0))
  assert any(abs(s.end_m - 250.0) < 1e-6 for s in segs)
  assert all(math.isclose(s.speed_limit_ms, 13.9 if s.start_m >= 250.0 else 22.0, rel_tol=1e-6) for s in segs)  # Float32


def test_nothing_without_loaded_tiles():
  assert MapDataParser().parse(*messages([0.0] * 40, tile_loaded=False)) == []

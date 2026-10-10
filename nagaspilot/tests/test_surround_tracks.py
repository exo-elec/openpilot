import math
from types import SimpleNamespace as NS
from openpilot.nagaspilot.daemons.radar4d.surround_tracks import surround_tracks, tracked_obstacles


def track(corner=0, elevation=60):
  return NS(corner=corner, trackId=7, rangM=10, azimuthDeg=0, elevationDeg=elevation, vRel=-2, snrDb=20)


def test_ble_3d_track_retains_elevation_and_projects_ground_geometry():
  result = surround_tracks([track()], {0: (1, 2, 0)})
  assert len(result) == 1
  obj = result[0]
  assert math.isclose(obj['rangM'], math.hypot(6, 2))
  assert obj['elevation'] == 60 and obj['source'] == 1 and obj['pointCount'] == 0
  assert 'lengthM' not in obj and 'heightM' not in obj
  obj.update(dynProp=1, lengthM=0, widthM=0)
  obstacles = tracked_obstacles([NS(**obj)])
  assert math.isclose(obstacles[0]['dRel'], 6)
  assert math.isclose(obstacles[0]['yRel'], 2)
  assert not obstacles[0]['ttcValid']


def test_unplaced_and_invalid_tracks_are_dropped_and_corner_ids_do_not_collide():
  assert not surround_tracks([track()], None)
  assert not surround_tracks([track(elevation=math.nan)], {0: (0, 0, 0)})
  result = surround_tracks([track(0), track(1)], {0: (0, 0, 0), 1: (0, 0, 0)})
  assert result[0]['trackId'] != result[1]['trackId']

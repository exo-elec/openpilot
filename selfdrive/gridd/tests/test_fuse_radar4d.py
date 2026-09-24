import math

import cereal.messaging as messaging
from openpilot.selfdrive.controls.lib.radar4d_points import (
  FUSE_COST_MOVING, FUSE_COST_STATIC, FUSE_MAX_POINTS, FUSE_RADIUS_M,
)
from openpilot.selfdrive.gridd.gridd import GridD


def _radar4d_msg(points: list[dict]):
  msg = messaging.new_message('radar4d')
  out = msg.radar4d.init('points', len(points))
  for i, p in enumerate(points):
    out[i].rangM = p['rangM']
    out[i].azimuth = p.get('azimuth', 0.0)
    out[i].snrDb = p.get('snrDb', 20.0)
    out[i].isStatic = p.get('isStatic', False)
  return msg.radar4d


class _Costmap:
  def __init__(self):
    self.calls = []

  def add_obstacle(self, d, y, radius, cost):
    self.calls.append((d, y, radius, cost))


class _FuseHost:
  """Only what _fuse_radar4d reads -- avoids GridD's heavy __init__."""
  _active_costmap = None
  _fuse_radar4d = GridD._fuse_radar4d


def test_points_are_stamped_at_their_vehicle_frame_position():
  host = _FuseHost()
  host._active_costmap = _Costmap()
  host._fuse_radar4d(_radar4d_msg([
    {'rangM': 4.0, 'azimuth': 90.0, 'snrDb': 10.0, 'isStatic': True},   # 4 m to the left
    {'rangM': 5.0, 'azimuth': 0.0, 'snrDb': 25.0},                      # 5 m ahead, moving
  ]))
  calls = host._active_costmap.calls
  assert len(calls) == 2
  d, y, r, cost = calls[0]                     # strongest first
  assert (round(d, 6), round(y, 6), r, cost) == (5.0, 0.0, FUSE_RADIUS_M, FUSE_COST_MOVING)
  d, y, r, cost = calls[1]
  assert abs(d) < 1e-6 and abs(y - 4.0) < 1e-6 and cost == FUSE_COST_STATIC


def test_no_costmap_or_no_message_is_a_no_op():
  host = _FuseHost()
  host._fuse_radar4d(_radar4d_msg([{'rangM': 3.0}]))   # no costmap yet
  host._active_costmap = _Costmap()
  host._fuse_radar4d(None)
  assert host._active_costmap.calls == []


def test_out_of_range_dropped_and_count_capped():
  host = _FuseHost()
  host._active_costmap = _Costmap()
  pts = [{'rangM': 0.0}, {'rangM': 40.0}] + [{'rangM': 2.0, 'snrDb': float(i)} for i in range(FUSE_MAX_POINTS + 20)]
  host._fuse_radar4d(_radar4d_msg(pts))
  calls = host._active_costmap.calls
  assert len(calls) == FUSE_MAX_POINTS
  assert all(math.isclose(c[0], 2.0, abs_tol=1e-6) for c in calls)

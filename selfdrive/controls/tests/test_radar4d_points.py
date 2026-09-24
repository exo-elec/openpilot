import math
import struct
from dataclasses import dataclass

import pytest

from openpilot.selfdrive.controls.lib.radar4d_points import (
  FUSE_COST_MOVING, FUSE_COST_STATIC, FUSE_MAX_POINTS, MAX_POINTS, STALE_S,
  CornerCloudCache, detection_to_vehicle, expected_static_vrel, points_to_obstacles,
)
from openpilot.selfdrive.controls.radar_corner_geometry import CornerPose


@dataclass
class _Det:
  range_m: float
  vel_mps: float = 0.0
  azimuth_deg: float = 0.0
  elevation_deg: float = 0.0
  snr_db: float = 20.0


@dataclass
class _Frame:
  corner_id: int
  detections: list


POSES = {
  0: CornerPose(3.5, 0.8, 0.5, 0.0, 0.0, 45.0, True),
  1: CornerPose(3.5, -0.8, 0.5, 0.0, 0.0, -45.0, True),
}


def test_boresight_point_lands_along_mount_yaw():
  p = detection_to_vehicle(_Det(range_m=2.0), 0, POSES[0], v_ego=0.0)
  assert p.x_m == pytest.approx(3.5 + 2.0 * math.cos(math.radians(45)))
  assert p.y_m == pytest.approx(0.8 + 2.0 * math.sin(math.radians(45)))
  assert p.range_m == pytest.approx(math.hypot(p.x_m, p.y_m))
  assert p.azimuth_deg == pytest.approx(math.degrees(math.atan2(p.y_m, p.x_m)))


def test_elevation_uses_horizontal_range():
  p = detection_to_vehicle(_Det(range_m=2.0, elevation_deg=60.0), 1, POSES[1], v_ego=0.0)
  sensor_horizontal = 1.0
  assert math.hypot(p.x_m - 3.5, p.y_m + 0.8) == pytest.approx(sensor_horizontal)
  assert p.elevation_deg == 60.0


def test_static_check_uses_ego_speed_and_line_of_sight():
  # Right-front corner looking 45 deg right; a static target at the sensor's
  # boresight closes at v_ego*cos(-45 deg).
  v_ego = 10.0
  static_v = expected_static_vrel(-45.0, v_ego)
  assert static_v == pytest.approx(-10.0 * math.cos(math.radians(45)))
  assert detection_to_vehicle(_Det(3.0, vel_mps=static_v), 1, POSES[1], v_ego).is_static
  assert not detection_to_vehicle(_Det(3.0, vel_mps=static_v - 2.0), 1, POSES[1], v_ego).is_static
  assert detection_to_vehicle(_Det(3.0, vel_mps=0.0), 1, POSES[1], 0.0).is_static


def test_cache_skips_unknown_corner_missing_pose_and_stale():
  cache = CornerCloudCache()
  cache.update([_Frame(0, [_Det(2.0)]), _Frame(2, [_Det(2.0)]), _Frame(0xFF, [_Det(2.0)])], now=10.0)
  assert cache.fresh_corners(10.0) == [0, 2]
  pts = cache.points(POSES, 0.0, 10.0)
  assert [p.corner for p in pts] == [0]              # corner 2 has no confirmed pose
  assert cache.points(None, 0.0, 10.0) == []
  assert cache.points(POSES, 0.0, 10.0 + STALE_S + 0.01) == []


def test_cache_keeps_latest_frame_and_strongest_points():
  cache = CornerCloudCache()
  cache.update([_Frame(0, [_Det(1.0, snr_db=1.0)])], now=1.0)
  cache.update([_Frame(0, [_Det(2.0, snr_db=float(i)) for i in range(MAX_POINTS + 50)])], now=1.1)
  pts = cache.points(POSES, 0.0, 1.1)
  assert len(pts) == MAX_POINTS
  assert pts[0].snr_db == MAX_POINTS + 49
  assert all(a.snr_db >= b.snr_db for a, b in zip(pts, pts[1:], strict=False))


def test_points_to_obstacles():
  @dataclass
  class _P:
    rangM: float
    azimuth: float
    snrDb: float
    isStatic: bool

  stamps = points_to_obstacles([_P(5.0, 90.0, 3.0, True), _P(4.0, 0.0, 9.0, False),
                                _P(0.0, 0.0, 50.0, False), _P(40.0, 0.0, 50.0, False)])
  assert len(stamps) == 2                              # 0 m and beyond 30 m dropped
  d, y, _r, cost = stamps[0]                           # strongest first
  assert (d, y, cost) == (pytest.approx(4.0), pytest.approx(0.0), FUSE_COST_MOVING)
  d, y, _r, cost = stamps[1]
  assert (d, y, cost) == (pytest.approx(0.0, abs=1e-9), pytest.approx(5.0), FUSE_COST_STATIC)
  many = [_P(1.0, 0.0, float(i), False) for i in range(FUSE_MAX_POINTS + 10)]
  assert len(points_to_obstacles(many)) == FUSE_MAX_POINTS


def test_end_to_end_through_hal_receiver():
  radar4d = pytest.importorskip("hal.drivers.radar.radar4d")
  if not hasattr(radar4d, "Radar4DChunkAssembler"):
    pytest.skip("hal predates the Radar4D chunk decoder")

  # One Radar4D frame, one dynamic_high point 4 m ahead of corner 0's sensor.
  word1 = 50 | (1 << 10)
  hdr = struct.pack("<IIIIIII", 0xFFEEFFDC, word1, 1, 0, 0, 50 << 24, 0xFFEEFFD3)
  pc = (struct.pack("<I", 0xFFDDFECB) + struct.pack("<hhhHh", 0, 4000, 0, 100, 0)
        + b"\0\0\0\0" + struct.pack("<I", 0xFFDDFEC4))
  tr = struct.pack("<I", 0xFFCCFDBA) + b"\0\0\0\0" + struct.pack("<I", 0xFFCCFDB5)
  frame = hdr + pc + tr
  datagram = struct.pack("<IBBHBBH", 0x55443452, 1, 0, 5, 0, 1, len(frame)) + frame

  class _Sock:
    def __init__(self):
      self.q = [datagram]

    def settimeout(self, _t):
      pass

    def recvfrom(self, _n):
      if not self.q:
        raise TimeoutError
      return self.q.pop(0), ("10.42.0.11", 47000)

  rx = radar4d.RadarCornerReceiver()
  rx._sock = _Sock()
  cache = CornerCloudCache()
  cache.update(rx.recv_all(), now=0.0)
  (p,) = cache.points(POSES, 0.0, 0.0)
  assert p.x_m == pytest.approx(3.5 + 4.0 * math.cos(math.radians(45)), abs=1e-3)
  assert p.y_m == pytest.approx(0.8 + 4.0 * math.sin(math.radians(45)), abs=1e-3)
  assert p.snr_db == pytest.approx(20.0)

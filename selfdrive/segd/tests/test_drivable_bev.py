"""segd's card map -> gridd BEV projection (drivable_bev.py).

Grid: x forward, y LEFT (like every yRel). Front camera pose from
liveCalibration rpyCalib, modeld's device_from_calib euler in openpilot's
[Forward, Right, Down] frame, R = Rz(yaw) Ry(pitch) Rx(roll) (orientation.cc).
"""
import math

import numpy as np

from openpilot.selfdrive.gridd.lazy_bev import BEV_GRID
from openpilot.selfdrive.segd.card_segmenter import DRIVABLE as CARD_DRIVABLE, OTHER as CARD_OTHER
from openpilot.selfdrive.segd.drivable_bev import (
  DRIVABLE, OTHER, UNKNOWN, build_lut, front_geometry, project, side_rear_geometry)

MAP_HW = (540, 960)


def _upstream_device_from_calib(roll, pitch, yaw):
  """orientation.cc euler2rot: AngleAxis(yaw, Z) * AngleAxis(pitch, Y) * AngleAxis(roll, X)."""
  cr, sr, cp, sp, cy, sy = math.cos(roll), math.sin(roll), math.cos(pitch), math.sin(pitch), math.cos(yaw), math.sin(yaw)
  rz = np.array([[cy, -sy, 0], [sy, cy, 0], [0, 0, 1]])
  ry = np.array([[cp, 0, sp], [0, 1, 0], [-sp, 0, cp]])
  rx = np.array([[1, 0, 0], [0, cr, -sr], [0, sr, cr]])
  return rz @ ry @ rx


def test_front_pose_matches_upstream_rpycalib():
  for rpy in ([0.0, 0.05, 0.0], [0.0, 0.0, 0.04], [0.0, -0.03, -0.02], [0.02, 0.04, -0.03]):
    axis_frd = _upstream_device_from_calib(*rpy).T @ np.array([1.0, 0.0, 0.0])  # camera axis, car F,R,D
    expected = np.array([axis_frd[0], -axis_frd[1], -axis_frd[2]])              # -> x fwd, y left, z up
    geom = front_geometry('road', 1928, 1208, rpy, 1.22)
    assert np.allclose(geom.R_cv[2], expected, atol=1e-9), rpy
    right_frd = _upstream_device_from_calib(*rpy).T @ np.array([0.0, 1.0, 0.0])
    assert np.allclose(geom.R_cv[0], [right_frd[0], -right_frd[1], -right_frd[2]], atol=1e-9), rpy


def _half_map(left_class, right_class):
  m = np.full(MAP_HW, right_class, dtype=np.uint8)
  m[:, :MAP_HW[1] // 2] = left_class
  return m


def test_road_camera_left_of_image_lands_left_of_car():
  geom = front_geometry('road', 1928, 1208, [0.0, 0.0, 0.0], 1.22)
  cells = project(_half_map(CARD_OTHER, CARD_DRIVABLE), build_lut(geom, MAP_HW, 'road'))
  r, c, _ = BEV_GRID.cells(np.array([20.0, 20.0]), np.array([2.0, -2.0]))
  assert cells[r[0], c[0]] == OTHER      # 20 m ahead, 2 m LEFT: left half of the image
  assert cells[r[1], c[1]] == DRIVABLE   # 2 m right
  r, c, _ = BEV_GRID.cells(-5.0, 0.0)
  assert cells[r, c] == UNKNOWN          # behind the car: not seen


def test_side_and_rear_cameras_see_their_own_side():
  full = np.full(MAP_HW, CARD_DRIVABLE, dtype=np.uint8)
  seen = {}
  for cam in ('side_left', 'side_right', 'rear'):
    geom = side_rear_geometry(cam, 1280.0, 720.0)
    cells = project(full, build_lut(geom, MAP_HW, cam))
    rows, cols = np.nonzero(cells == DRIVABLE)
    forward, left = BEV_GRID.center(rows, cols)
    assert len(rows) > 0, cam
    seen[cam] = (float(np.mean(forward)), float(np.mean(left)))
  assert seen['side_left'][1] > 1.0 and seen['side_right'][1] < -1.0
  assert seen['side_left'][0] < 0.0 and seen['side_right'][0] < 0.0   # they look back
  assert seen['rear'][0] < -3.0 and abs(seen['rear'][1]) < 2.0


def test_segd_caches_the_lookup_table_per_pose():
  import sys
  from types import SimpleNamespace
  from unittest.mock import MagicMock, patch  # noqa: TID251
  with patch.dict(sys.modules, {m: sys.modules.get(m, MagicMock()) for m in ('cereal.messaging', 'msgq', 'msgq.visionipc')}):
    from openpilot.selfdrive.segd.segd import SegD
  sd = SegD.__new__(SegD)
  lc = SimpleNamespace(rpyCalib=[0.0, 0.0, 0.0], wideFromDeviceEuler=[], height=[1.3])

  class _SM:
    valid = {'liveCalibration': True}

    def __getitem__(self, name):
      return lc
  sd.sm = _SM()
  sd._luts = {}
  class_map = np.full(MAP_HW, CARD_DRIVABLE, dtype=np.uint8)
  first = sd._bev('road', class_map, (1208, 1928))
  lut = sd._luts['road']
  sd._bev('road', class_map, (1208, 1928))
  assert sd._luts['road'] is lut                 # same pose: table reused
  lc.rpyCalib = [0.0, 0.02, 0.0]
  sd._bev('road', class_map, (1208, 1928))
  assert sd._luts['road'] is not lut             # new calibration: rebuilt
  assert first.shape == (BEV_GRID.rows, BEV_GRID.cols) and (first == DRIVABLE).any()

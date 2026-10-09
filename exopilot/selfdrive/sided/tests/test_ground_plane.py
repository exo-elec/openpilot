#!/usr/bin/env python3
"""Ground-plane ranging: the rear camera (reard) and the shared side/rear reprojection."""

from __future__ import annotations

import math
import sys
import unittest  # noqa: TID251
from unittest.mock import MagicMock, patch  # noqa: TID251

import numpy as np

from openpilot.selfdrive.sided.bev_reprojector import CLASS_WIDTHS_M, make_default_geometry, make_rear_geometry, reproject_side_camera
from openpilot.selfdrive.sided.simple_tracker import SideObject


def project(geo, point_vehicle) -> tuple[float, float]:
  """Vehicle-frame point → image pixel through the camera's lens, the inverse of reproject_side_camera's ray."""
  uv, _ = geo.pixels_from_camera(geo.R_cv @ (np.asarray(point_vehicle, dtype=np.float64) - geo.t_cv), in_image=False)
  return float(uv[0, 0]), float(uv[0, 1])


class TestRearGeometry(unittest.TestCase):

  def setUp(self):
    self.geo = make_rear_geometry()   # 170 deg AHD fisheye, 1280x720

  def _box_on_ground(self, x, y, half_w_px=20, h_px=40):
    u, v = project(self.geo, (x, y, 0.0))
    return (u - half_w_px, v - h_px, u + half_w_px, v)

  def test_ground_point_comes_back(self):
    for x, y in ((-8.0, 1.5), (-4.0, -2.0), (-15.0, 0.0)):
      box = self._box_on_ground(x, y)
      rx, ry, rz, _, _ = reproject_side_camera(box, 'car', (720, 1280), self.geo)
      self.assertAlmostEqual(rx, x, delta=0.05)
      self.assertAlmostEqual(ry, y, delta=0.05)
      self.assertAlmostEqual(rz, 0.0, delta=1e-6)

  def test_left_of_the_car_is_on_the_right_of_the_rear_image(self):
    u_left, _ = project(self.geo, (-8.0, 1.5, 0.0))
    self.assertGreater(u_left, self.geo.cx)

  def test_box_bottom_above_the_horizon_stays_behind_the_car(self):
    """The ground ray never lands: used to put the object in front of the car."""
    far_behind = self.geo.t_cv + [-1000.0, 0.0, 0.0]
    _, horizon_v = project(self.geo, far_behind)
    w_px = 40.0
    box = (620.0, horizon_v - 60, 620.0 + w_px, horizon_v - 10)
    x, _, _, _, _ = reproject_side_camera(box, 'car', (720, 1280), self.geo)
    self.assertLess(x, 0.0)
    expected = CLASS_WIDTHS_M['car'] * self.geo.fx / w_px   # class-width range along the ray
    self.assertAlmostEqual(-x, expected, delta=0.25 * expected)


class TestSideGeometry(unittest.TestCase):
  """The side cameras' BEV positions: the old rotation pointed every ray up."""

  def test_optical_axis_is_the_yaw_heading(self):
    geo = make_rear_geometry()
    np.testing.assert_allclose(geo.R_cv.T @ [0, 0, 1],
                               [-math.cos(geo.pitch_rad), 0, math.sin(geo.pitch_rad)], atol=1e-9)
    self.assertLess(math.sin(geo.pitch_rad), 0)  # tilted down

  def test_fisheye_round_trip(self):
    geo = make_rear_geometry()
    uv = np.array([[640.0, 360.0], [10.0, 20.0], [1270.0, 700.0], [900.0, 100.0]])
    back, ok = geo.pixels_from_camera(geo.rays_from_pixels(uv) * 7.0)
    np.testing.assert_allclose(back, uv, atol=1e-6)
    self.assertTrue(ok.all())

  def test_car_in_the_blind_spot_comes_back(self):
    for side, point in (('side_left', (-4.0, 3.2, 0.0)), ('side_right', (-4.0, -3.2, 0.0))):
      geo = make_default_geometry(side)
      u, v = project(geo, point)
      self.assertTrue(0 <= u <= geo.img_w and 0 <= v <= geo.img_h, (side, u, v))
      x, y, _, _, _ = reproject_side_camera((u - 30, v - 60, u + 30, v), 'car', (720, 1280), geo)
      self.assertAlmostEqual(x, point[0], delta=0.05)
      self.assertAlmostEqual(y, point[1], delta=0.05)


class TestRearProcessor(unittest.TestCase):

  def test_publishes_metres_from_the_ground_plane(self):
    stubs = {m: sys.modules.get(m, MagicMock()) for m in ('cereal.messaging', 'msgq', 'msgq.visionipc')}
    with patch.dict(sys.modules, stubs):
      from openpilot.selfdrive.reard.reard import RknnRearProcessor
    geo = make_rear_geometry()
    u, v = project(geo, (-6.0, -1.0, 0.0))
    det = MagicMock(is_available=True)
    det.detect.return_value = [SideObject(label='car', confidence=0.9, bbox_2d=(u - 30, v - 50, u + 30, v))]
    proc = RknnRearProcessor(det, geometry=geo)
    proc.detect(np.zeros((720, 1280, 3), np.uint8))
    tracked = proc.detect(np.zeros((720, 1280, 3), np.uint8))
    self.assertEqual(len(tracked), 1)
    self.assertAlmostEqual(tracked[0].distance_m, -6.0, delta=0.3)
    self.assertAlmostEqual(tracked[0].lateral_m, -1.0, delta=0.3)   # metres now, not -1..1 of the image


if __name__ == '__main__':
  unittest.main()

"""camera_calibrationd's side/rear glue around exopilot's hal.calibration."""

import math
import os

import numpy as np
import pytest

HAL_SRC = os.path.join(os.path.dirname(__file__), '../../../../exopilot/hal')


@pytest.fixture
def hal(monkeypatch):
  if os.path.isdir(HAL_SRC):
    monkeypatch.syspath_prepend(os.path.abspath(HAL_SRC))
  pytest.importorskip("hal.calibration.extrinsics")
  from hal.platform import rk3588_camera_geometry
  return rk3588_camera_geometry


def make(geo, tmp_path, **kw):
  from openpilot.selfdrive.locationd.side_rear_calibration import SideRearCalibration
  return SideRearCalibration(geo, store_path=str(tmp_path / "sensors_tf.yaml"), **kw)


def test_disabled_without_geometry(tmp_path):
  from openpilot.selfdrive.locationd.side_rear_calibration import SideRearCalibration
  sr = SideRearCalibration(None, store_path=str(tmp_path / "x.yaml"))
  assert not sr.enabled and sr.results() == []
  sr.add_odometry([20, 0, 0], [0, 0, 0], 0.05, 20.0)
  sr.add_frame('side_left', np.zeros((720, 1280, 3), np.uint8))


def test_unit_height_is_clamped_to_the_board(hal, tmp_path):
  assert make(hal, tmp_path, unit_height_m=0.1).unit_height_m == hal.UNIT_HEIGHT_LIMITS_M[0]
  assert make(hal, tmp_path, unit_height_m=None).unit_height_m == hal.NOMINAL_UNIT_HEIGHT_M


def test_frames_start_a_calibrator_inside_the_mounting_limits(hal, tmp_path):
  from hal.calibration.mounting import mount_limits
  sr = make(hal, tmp_path)
  for cam in ('side_left', 'side_right', 'rear_camera'):
    sr.add_frame(cam, np.zeros((720, 1280, 3), np.uint8))
  assert [r.camera for r in sr.results()] == ['side_left', 'side_right', 'rear_camera']
  for r in sr.results():
    assert r.status == 'uncalibrated' and mount_limits(hal, r.camera).contains(r.link)
  sr.add_frame('road', np.zeros((720, 1280, 3), np.uint8))   # not a side/rear camera
  assert len(sr.results()) == 3


def test_odometry_reaches_every_calibrator(hal, tmp_path):
  sr = make(hal, tmp_path)
  sr.add_frame('rear_camera', np.zeros((720, 1280, 3), np.uint8))
  sr.add_odometry([10.0, 0.0, 0.0], [0.0, 0.0, 0.0], 0.05, v_ego=20.0)   # wheel speed wins
  np.testing.assert_allclose(sr.calibrators['rear_camera']._motion.t, [1.0, 0.0, 0.0], atol=1e-9)


def test_calibrated_link_is_stored_in_base_link_once(hal, tmp_path):
  from hal.calibration.extrinsics import CalibrationResult
  from hal.calibration.store import load_links
  from hal.calibration.tf_tree import Transform, link_frame
  sr = make(hal, tmp_path)
  link = Transform(-2.6, 0.0, 1.4, 0.0, math.radians(22), math.pi)   # in base_footprint
  sr._maybe_save('rear_camera', CalibrationResult('rear_camera', 'calibrating', 50, link))
  assert load_links(sr.store_path) == {}
  sr._maybe_save('rear_camera', CalibrationResult('rear_camera', 'calibrated', 100, link))
  stored = load_links(sr.store_path)[link_frame('rear_camera')]
  assert stored.z == pytest.approx(1.4 - sr.unit_height_m) and stored.pitch == pytest.approx(math.radians(22))
  os.remove(sr.store_path)
  sr._maybe_save('rear_camera', CalibrationResult('rear_camera', 'calibrated', 100, link))
  assert not os.path.exists(sr.store_path)     # unchanged: not stored again
  moved = Transform(-2.6, 0.0, 1.4, 0.0, math.radians(24), math.pi)
  sr._maybe_save('rear_camera', CalibrationResult('rear_camera', 'calibrated', 100, moved))
  assert os.path.exists(sr.store_path)


def test_reset_drops_the_calibrator_but_keeps_the_stored_link(hal, tmp_path):
  from hal.calibration.extrinsics import CalibrationResult
  from hal.calibration.store import load_links
  from hal.calibration.tf_tree import Transform, link_frame
  sr = make(hal, tmp_path)
  sr.add_frame('rear_camera', np.zeros((720, 1280, 3), np.uint8))
  assert 'rear_camera' in sr.calibrators
  link = Transform(-2.6, 0.0, 1.4, 0.0, math.radians(22), math.pi)
  sr._maybe_save('rear_camera', CalibrationResult('rear_camera', 'calibrated', 100, link))
  assert link_frame('rear_camera') in load_links(sr.store_path)

  sr.reset('rear_camera')
  assert 'rear_camera' not in sr.calibrators
  assert 'rear_camera' not in sr._saved
  # The persisted link from before the reset is untouched.
  assert link_frame('rear_camera') in load_links(sr.store_path)
  assert sr.results() == []


def test_reset_all_drops_every_camera(hal, tmp_path):
  sr = make(hal, tmp_path)
  for cam in ('side_left', 'side_right', 'rear_camera'):
    sr.add_frame(cam, np.zeros((720, 1280, 3), np.uint8))
  assert len(sr.calibrators) == 3
  sr.reset()
  assert sr.calibrators == {}
  assert sr.results() == []


def test_reset_on_disabled_instance_is_a_no_op(tmp_path):
  from openpilot.selfdrive.locationd.side_rear_calibration import SideRearCalibration
  sr = SideRearCalibration(None, store_path=str(tmp_path / "x.yaml"))
  sr.reset()   # must not raise despite no hal/geometry


def test_next_start_uses_the_stored_link(hal, tmp_path):
  from hal.calibration.extrinsics import CalibrationResult
  from hal.calibration.tf_tree import Transform
  sr = make(hal, tmp_path)
  link = Transform(-2.6, 0.0, 1.45, 0.0, math.radians(30), math.pi)
  sr._maybe_save('rear_camera', CalibrationResult('rear_camera', 'calibrated', 100, link))
  again = make(hal, tmp_path)
  again.add_frame('rear_camera', np.zeros((720, 1280, 3), np.uint8))
  start = again.results()[0].link
  assert start.z == pytest.approx(1.45) and start.pitch == pytest.approx(math.radians(30))


@pytest.mark.parametrize("camera,hal_name", [('side_left', 'side_left'), ('side_right', 'side_right'), ('rear', 'rear_camera')])
def test_sided_geometry_projects_like_hal(hal, camera, hal_name):
  """sided/reard's SideCameraGeometry from the tf tree sees the road where hal's CameraModel does."""
  from hal.calibration.camera_model import from_board
  from hal.calibration.tf_tree import Transform
  from openpilot.selfdrive.sided.bev_reprojector import hal_geometry
  from openpilot.selfdrive.sided.bev_reprojector import SideCameraGeometry
  geo = hal_geometry(camera, 1280, 720, geometry=hal)
  model = from_board(hal, hal_name, hal.NOMINAL_UNIT_HEIGHT_M)
  L = model.link
  # a rolled, pitched mounting: the sign conventions must agree too
  model = model.with_link(Transform(L.x, L.y, L.z, 0.05, L.pitch + 0.1, L.yaw - 0.05))
  L = model.link
  geo = SideCameraGeometry(**{**geo.__dict__, 'roll_rad': L.roll, 'pitch_rad': -L.pitch, 'yaw_rad': L.yaw,
                              'cam_x_m': L.x})
  pts = np.array([[L.x - 6.0, L.y + s, 0.0] for s in (-3.0, 0.0, 2.5)])
  uv_hal, ok = model.project(pts, in_image=False)
  uv_geo, _ = geo.pixels_from_camera((pts - geo.t_cv) @ geo.R_cv.T, in_image=False)
  assert ok.all()
  np.testing.assert_allclose(uv_geo, uv_hal, atol=1e-6)
  assert geo.lens == 'equidistant'

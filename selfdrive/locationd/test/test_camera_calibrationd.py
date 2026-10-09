"""camera_calibrationd's MultiCameraCalibrator: one calibrationd.Calibrator per camera."""

import numpy as np
import pytest

from cereal import log
from openpilot.selfdrive.locationd.calibrationd import BLOCK_SIZE, HEIGHT_INIT, INPUTS_NEEDED, INPUTS_WANTED, MIN_SPEED_FILTER
from openpilot.selfdrive.locationd.camera_calibrationd import MultiCameraCalibrator
from openpilot.selfdrive.locationd.test.test_calibrationd import process_messages


class _RoadOnly(MultiCameraCalibrator):
  """geometry-free stand-in: MultiCameraCalibrator without CameraArrayGeometry,
  the same shape a dev PC (no exopilot hal) sees -- only 'road' exists."""

  def __init__(self, param_put: bool = False):
    self.param_put = param_put
    self.params = None
    self.platform = 'test'
    self.geometry = None
    self.cameras = {}
    self._init_cameras()
    self.not_car = False
    self.cross_camera_consistency = 1.0
    self.side_rear = None

  def _init_cameras(self):
    from openpilot.selfdrive.locationd.calibrationd import Calibrator
    self.cameras['road'] = Calibrator(param_put=self.param_put)


class TestMultiCameraCalibrator:

  def test_road_camera_always_exists(self):
    """Even with an empty camera array (no exopilot hal), 'road' is there --
    cameraOdometry is always the road camera's, as in plain calibrationd."""
    cal = MultiCameraCalibrator(param_put=False)
    assert 'road' in cal.cameras
    assert cal.cameras['road'].valid_blocks == 0

  def test_road_calibrates_like_plain_calibrationd(self):
    """The road camera behaves exactly like calibrationd.Calibrator, because
    it is one -- straight, fast, low-uncertainty driving calibrates it."""
    cal = _RoadOnly(param_put=False)
    road = cal.cameras['road']
    process_messages(road, [0.0, 0.0, 0.0], BLOCK_SIZE * INPUTS_NEEDED)
    cal.update_status()
    assert road.valid_blocks == INPUTS_NEEDED
    assert road.cal_status == log.LiveCalibrationData.Status.calibrated
    np.testing.assert_allclose(road.rpy, np.zeros(3))

  def test_low_speed_never_calibrates(self):
    cal = _RoadOnly(param_put=False)
    road = cal.cameras['road']
    process_messages(road, [0.0, 0.0, 0.0], BLOCK_SIZE * INPUTS_WANTED, cam_odo_speed=MIN_SPEED_FILTER - 1)
    cal.update_status()
    assert road.valid_blocks == 0
    assert road.cal_status == log.LiveCalibrationData.Status.uncalibrated

  def test_get_msg_is_the_road_calibrators_own_message(self):
    """liveCalibration is exactly Calibrator.get_msg() -- upstream's own
    format, not a reinvented one."""
    cal = _RoadOnly(param_put=False)
    road = cal.cameras['road']
    process_messages(road, [0.0, 0.0, 0.0], BLOCK_SIZE * INPUTS_NEEDED)
    cal.update_status()
    got = cal.get_msg(True)
    want = road.get_msg(True)
    assert got.liveCalibration.calStatus == want.liveCalibration.calStatus
    assert got.liveCalibration.calPerc == want.liveCalibration.calPerc
    np.testing.assert_allclose(got.liveCalibration.rpyCalib, want.liveCalibration.rpyCalib)
    np.testing.assert_allclose(got.liveCalibration.height, want.liveCalibration.height)

  def test_calibration_state_reflects_the_road_calibrator(self):
    cal = _RoadOnly(param_put=False)
    road = cal.cameras['road']
    process_messages(road, [0.0, 0.0, 0.0], BLOCK_SIZE * INPUTS_NEEDED)
    cal.update_status()
    cs = cal.get_calibration_state_msg().calibrationState
    assert cs.status == log.CalibrationState.Status.calibrated
    assert cs.progress == 1.0
    assert len(cs.cameraCalibrations) == 1
    road_cal = cs.cameraCalibrations[0]
    assert road_cal.cameraId == 'road'
    assert road_cal.converged
    assert road_cal.validBlocks == INPUTS_NEEDED
    assert road_cal.height == pytest.approx(HEIGHT_INIT.item())

  def test_uncalibrated_state(self):
    cal = _RoadOnly(param_put=False)
    cal.update_status()
    cs = cal.get_calibration_state_msg().calibrationState
    assert cs.status == log.CalibrationState.Status.uncalibrated
    assert cs.progress == 0.0
    assert cal.get_msg(True).liveCalibration.calStatus == log.LiveCalibrationData.Status.uncalibrated

  def test_messages_serialize_round_trip(self):
    """What send_data hands the PubMaster: both messages must serialize."""
    cal = _RoadOnly(param_put=False)
    process_messages(cal.cameras['road'], [0.0, 0.0, 0.0], BLOCK_SIZE * INPUTS_NEEDED)
    cal.update_status()
    with log.Event.from_bytes(cal.get_msg(True).to_bytes()) as live:
      assert live.liveCalibration.calStatus == log.LiveCalibrationData.Status.calibrated
    with log.Event.from_bytes(cal.get_calibration_state_msg().to_bytes()) as state:
      assert state.calibrationState.status == log.CalibrationState.Status.calibrated

  def test_cross_camera_consistency_with_one_camera(self):
    """A single (road-only) array is trivially consistent; interCameraSpread
    must not crash on the now-guaranteed non-empty self.cameras."""
    cal = _RoadOnly(param_put=False)
    process_messages(cal.cameras['road'], [0.0, 0.0, 0.0], BLOCK_SIZE * INPUTS_NEEDED)
    cal.update_status()
    assert cal.cross_camera_consistency == 1.0
    msg = cal.get_calibration_state_msg()
    assert msg.calibrationState.interCameraSpread >= 0.0
    assert msg.calibrationState.consistencyCheckPassed

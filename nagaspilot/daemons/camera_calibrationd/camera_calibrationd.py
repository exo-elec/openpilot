#!/usr/bin/env python3
"""
Multi-Camera Calibration Daemon
================================

Wraps openpilot's own calibrationd.Calibrator -- unchanged, one instance per
camera -- instead of reimplementing its block averaging, moving-average
decay and validity checks. openpilot's own egomotion (cameraOdometry) is
single-camera, so only 'road' is ever actually fed real data; the daemon
still keeps a Calibrator per camera in the array for calibrationState's
telemetry / cross-camera-spread reporting, matching pre-refactor behaviour.

liveCalibration is exactly upstream's own message: the road camera's
Calibrator.get_msg(), unmodified. calibrationState (EOP's own multi-camera
summary) is published on top of it, and now also carries the side/rear
cameras that SideRearCalibration calibrates through exopilot's
hal.calibration -- a ground-plane fit against the road camera's motion,
since those cameras have no cameraOdometry of their own.

On-Road Calibration Conditions (road camera, calibrationd.Calibrator's):
- Speed > 15 MPH (steady straight driving)
- Low yaw rate (< 2 deg/s)
- Low velocity angle std (< 0.25 deg)

See Also:
    - calibrationd.py -- the Calibrator class this wraps, unmodified
    - side_rear_calibration.py -- side/rear cameras via exopilot's hal
    - camera_geometry.py (CameraArrayGeometry) -- the camera array
"""

from __future__ import annotations

import os
import capnp
import numpy as np
from typing import NoReturn

from cereal import log, car
import cereal.messaging as messaging
from openpilot.system.hardware import HAS_SIDE_CAMERAS, HARDWARE
from openpilot.common.params import Params
from openpilot.common.realtime import config_realtime_process, DT_MDL
from openpilot.common.core_config import set_daemon_affinity
from openpilot.common.transformations.orientation import rot_from_euler
from openpilot.common.swaglog import cloudlog

from openpilot.nagaspilot.daemons.gridd.camera_geometry import CameraArrayGeometry
from openpilot.selfdrive.locationd.calibrationd import BLOCK_SIZE, HEIGHT_INIT, INPUTS_NEEDED, INPUTS_WANTED, Calibrator
from openpilot.selfdrive.locationd.side_rear_calibration import SideRearCalibration

from msgq.visionipc import VisionIpcClient, VisionStreamType

# Cross-camera reporting only -- each camera's own validity/convergence is
# calibrationd.Calibrator's (is_calibration_valid, PITCH_LIMITS/YAW_LIMITS).
MAX_INTER_CAMERA_SPREAD = np.radians(1.5)  # Max RPY difference between cameras

DEBUG = os.getenv("DEBUG") is not None


class MultiCameraCalibrator:
  """One calibrationd.Calibrator per camera in the array.

  Only 'road' is ever fed cameraOdometry -- openpilot's egomotion estimate
  is single-camera -- so it is the only one that actually moves off its
  initial guess; the others report as uncalibrated, same as before this
  reused Calibrator. Side and rear cameras are not here at all: they have
  no cameraOdometry of their own and are calibrated by SideRearCalibration
  (ground-plane fit against the road camera's motion, via exopilot's
  hal.calibration) -- see side_rear_calibration.py.
  """

  def __init__(self, param_put: bool = False):
    self.param_put = param_put
    self.params = Params()

    self.platform = self._detect_platform()
    self.geometry = CameraArrayGeometry.for_platform(self.platform)

    self.cameras: dict[str, Calibrator] = {}
    self._init_cameras()

    self.not_car = False
    self.cross_camera_consistency = 1.0  # 0-1, higher is better

    # Side / rear cameras (SideRearCalibration), set by main()
    self.side_rear: SideRearCalibration | None = None

    cloudlog.info(f"MultiCameraCalibrator initialized for {self.platform}")
    cloudlog.info(f"Cameras: {list(self.cameras.keys())}")

  def _detect_platform(self) -> str:
    """The board this is running on.

    Returned a literal before, so a calibration produced on any other
    board was tagged as this one -- and the tag is what decides which
    geometry the calibration is read back against.
    """
    return HARDWARE.get_device_type()

  def _init_cameras(self) -> None:
    """One Calibrator per camera in the array.

    'road' persists to (and loads from) params on its own, exactly as
    upstream's calibrationd does -- Calibrator's own __init__ reads
    "CalibrationParams" when param_put is set. The rest start fresh from
    the array's own geometry as their height guess: multi-camera odometry
    is not wired up, so they never move off it (see class docstring).

    'road' always gets a Calibrator, even if the array's geometry does not
    list one: without exopilot's hal (a dev PC -- this project's current
    stage) CameraArrayGeometry knows no cameras at all, but cameraOdometry
    is always the road camera's, same as upstream's plain calibrationd.
    """
    names = self.geometry.get_camera_names()
    if 'road' not in names:
      names = ['road', *names]
    for cam_name in names:
      cam = Calibrator(param_put=self.param_put and cam_name == 'road')
      if cam_name != 'road':
        height = HEIGHT_INIT
        if cam_name in self.geometry.cameras:
          height = np.array([-self.geometry.cameras[cam_name].extrinsics.t[2] + 1.22])
        cam.reset(height_init=height)
      self.cameras[cam_name] = cam

  def reset(self, camera_id: str | None = None) -> None:
    """Reset calibration for one or all cameras."""
    names = [camera_id] if camera_id else list(self.cameras)
    for name in names:
      self.cameras[name] = Calibrator(param_put=self.param_put and name == 'road')
    cloudlog.info(f"Reset calibration for {names}")

  def _check_cross_camera_consistency(self) -> float:
    """Check consistency between cameras. Returns consistency score 0-1."""
    rpys = [cam.rpy for cam in self.cameras.values() if cam.valid_blocks > 0]
    if len(rpys) < 2:
      return 1.0

    spread = np.max(rpys, axis=0) - np.min(rpys, axis=0)
    pitch_consistent = spread[1] < MAX_INTER_CAMERA_SPREAD
    yaw_consistent = spread[2] < MAX_INTER_CAMERA_SPREAD
    if pitch_consistent and yaw_consistent:
      return 1.0
    score = 1.0
    if not pitch_consistent:
      score *= 0.5
    if not yaw_consistent:
      score *= 0.5
    return score

  # -- driving the Calibrators (upstream's own handle_v_ego / handle_cam_odom) --
  def handle_v_ego(self, camera_id: str, v_ego: float) -> None:
    if camera_id in self.cameras:
      self.cameras[camera_id].handle_v_ego(v_ego)

  def handle_cam_odometry(self, camera_id: str, trans: list[float], rot: list[float],
                          wide_from_device_euler: list[float], trans_std: list[float],
                          road_transform_trans: list[float],
                          road_transform_trans_std: list[float]) -> np.ndarray | None:
    """One camera's cameraOdometry -- straight through to its own
    Calibrator.handle_cam_odom (driving-condition gating, moving-average
    update, sanity clipping and block bookkeeping, all upstream's)."""
    cam = self.cameras.get(camera_id)
    if cam is None:
      return None
    return cam.handle_cam_odom(trans, rot, wide_from_device_euler, trans_std,
                               road_transform_trans, road_transform_trans_std)

  def update_status(self) -> None:
    """Cross-camera reporting only -- each Calibrator already updated its
    own status (and, for 'road', persisted it) inside handle_cam_odom."""
    self.cross_camera_consistency = self._check_cross_camera_consistency()

  # -- messages --------------------------------------------------------------
  def get_msg(self, valid: bool = True) -> capnp.lib.capnp._DynamicStructBuilder:
    """liveCalibration -- exactly the road camera's own Calibrator.get_msg()."""
    road = self.cameras['road']
    road.not_car = self.not_car
    return road.get_msg(valid)

  def get_calibration_state_msg(self) -> capnp.lib.capnp._DynamicStructBuilder:
    """Generate calibrationState message (EOP multi-camera + side/rear state)."""
    msg = messaging.new_message('calibrationState')
    cs = msg.calibrationState
    road = self.cameras['road']

    # Map calibrationd's LiveCalibrationData.Status onto our own richer
    # enum (this file's CalibrationState.Status adds "calibrating" and
    # "converging" as substates of upstream's plain "uncalibrated").
    status_map = {
      log.LiveCalibrationData.Status.uncalibrated: 0,    # uncalibrated
      log.LiveCalibrationData.Status.calibrated: 3,       # calibrated
      log.LiveCalibrationData.Status.invalid: 4,          # invalid
      log.LiveCalibrationData.Status.recalibrating: 1,    # calibrating
    }
    cs.status = status_map.get(road.cal_status, 0)

    blocks_score = min(1.0, road.valid_blocks / INPUTS_WANTED)
    cs.quality = blocks_score * self.cross_camera_consistency
    total_samples = road.valid_blocks * BLOCK_SIZE + road.idx
    cs.progress = min(100 * total_samples // (INPUTS_NEEDED * BLOCK_SIZE), 100) / 100.0

    # Per-camera calibrations (capnp List(Struct) requires init() + index assignment)
    cam_names = list(self.cameras.keys())
    side_rear = self.side_rear.results() if self.side_rear is not None and self.side_rear.enabled else []
    cals = cs.init('cameraCalibrations', len(cam_names) + len(side_rear))
    for i, cam_name in enumerate(cam_names):
      cam = self.cameras[cam_name]
      cals[i].cameraId = cam_name
      cals[i].rpy = cam.rpy.tolist()
      cals[i].height = float(cam.height[0])
      cals[i].validBlocks = int(cam.valid_blocks)
      cals[i].quality = float(min(1.0, cam.valid_blocks / INPUTS_WANTED))
      cals[i].converged = bool(cam.cal_status == log.LiveCalibrationData.Status.calibrated)
    # Side / rear: link in base_footprint, ROS roll/pitch/yaw (pitch + = down),
    # height above the road; quality is calibration progress
    for j, res in enumerate(side_rear, start=len(cam_names)):
      cals[j].cameraId = res.camera
      cals[j].rpy = [float(res.link.roll), float(res.link.pitch), float(res.link.yaw)]
      cals[j].height = float(res.link.z)
      cals[j].validBlocks = int(res.num_pairs)
      cals[j].quality = float(res.percent) / 100.0
      cals[j].converged = bool(res.converged)

    # Cross-camera consistency
    cs.interCameraSpread = float(np.degrees(
      max(cam.calib_spread[1:].max() for cam in self.cameras.values())
    ))
    cs.consistencyCheckPassed = self.cross_camera_consistency > 0.9

    return msg

  def send_data(self, pm: messaging.PubMaster, valid: bool = True) -> None:
    """Send calibration data."""
    pm.send('liveCalibration', self.get_msg(valid))
    pm.send('calibrationState', self.get_calibration_state_msg())


def main() -> NoReturn:
  """Main calibration daemon."""
  set_daemon_affinity("camera_calibrationd")
  config_realtime_process(DT_MDL, 5)

  pm = messaging.PubMaster(['liveCalibration', 'calibrationState'])
  sm = messaging.SubMaster(
    ['cameraOdometry', 'carState', 'roadCameraState', 'wideRoadCameraState',
     'leftCameraState', 'rightCameraState'],
    poll='cameraOdometry'
  )

  params_reader = Params()
  CP = messaging.log_from_bytes(params_reader.get("CarParams", block=True), car.CarParams)

  calibrator = MultiCameraCalibrator(param_put=True)
  calibrator.not_car = CP.notCar

  # Side / rear cameras: calibrated against the road camera's motion by
  # exopilot's hal.calibration (see side_rear_calibration.py)
  side_rear = SideRearCalibration(HARDWARE.hal_module("camera_geometry") if HAS_SIDE_CAMERAS else None,
                                  unit_height_m=float(calibrator.cameras['road'].height[0]))
  side_vipc: dict[str, VisionIpcClient] = {}
  side_connected: set[str] = set()
  if side_rear.enabled:
    for name, stream_type in [
      ('side_left', VisionStreamType.VISION_STREAM_SIDE_LEFT),
      ('side_right', VisionStreamType.VISION_STREAM_SIDE_RIGHT),
      ('rear_camera', VisionStreamType.VISION_STREAM_REAR),
    ]:
      side_vipc[name] = VisionIpcClient("uvcd", stream_type, False)
  calibrator.side_rear = side_rear

  cloudlog.info("Camera calibration daemon started")

  while True:
    timeout = 0 if sm.frame == -1 else 100
    sm.update(timeout)

    if sm.updated['cameraOdometry']:
      odom = sm['cameraOdometry']
      calibrator.handle_v_ego('road', sm['carState'].vEgo)
      calibrator.handle_cam_odometry('road', odom.trans, odom.rot, odom.wideFromDeviceEuler,
                                     odom.transStd, odom.roadTransformTrans, odom.roadTransformTransStd)
      calibrator.update_status()

      # Side / rear calibration: the road camera's motion, then each camera's frame
      if side_rear.enabled:
        road_cam = calibrator.cameras['road']
        device_from_calib = rot_from_euler(road_cam.rpy)
        side_rear.add_odometry(odom.trans, odom.rot, DT_MDL, sm['carState'].vEgo, device_from_calib)
        for name, vip in side_vipc.items():
          try:
            if name not in side_connected:
              if not vip.connect(False):
                continue
              side_connected.add(name)
            buf = vip.recv(timeout_ms=1)
            if buf is not None:
              frame = np.frombuffer(buf.data, dtype=np.uint8)[:vip.height * vip.width * 3]
              side_rear.add_frame(name, frame.reshape((vip.height, vip.width, 3)))
          except Exception as e:
            cloudlog.debug(f"side/rear calibration frame error ({name}): {e}")

      if DEBUG:
        road_cam = calibrator.cameras['road']
        print(f"RPY: {road_cam.rpy}, Valid: {road_cam.valid_blocks}, Status: {road_cam.cal_status}")

    # One-shot UI trigger: reset side/rear calibration and start reconverging.
    # Checked at the same 4Hz cadence as publish -- a one-shot action needs
    # no faster polling than that.
    if sm.frame % 5 == 0:
      if params_reader.get_bool("EOPSideRearCalibReset"):
        side_rear.reset()
        params_reader.put_bool("EOPSideRearCalibReset", False)

      calibrator.send_data(pm, sm.all_checks())


if __name__ == "__main__":
  main()

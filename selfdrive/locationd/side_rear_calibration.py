"""
Side / rear camera calibration while driving -- openpilot's side of it.

The calibration itself is exopilot's (hal.calibration: tf tree, lens models,
mounting limits, ExtrinsicCalibrator, the sensors_tf.yaml store). This module
only feeds it: the front camera's cameraOdometry (every other camera is
calibrated against the road camera's motion), wheel speed for scale, the road
camera's own calibration, and the side/rear frames from uvcd. It stores each
link once it reads "calibrated" and reports progress for calibrationState.

Without the hal package (a dev PC, or a board without side/rear cameras)
it is disabled and says so once.
"""

from __future__ import annotations

import numpy as np

from openpilot.common.swaglog import cloudlog
from openpilot.system.hardware import HARDWARE

CAMERAS = ('side_left', 'side_right', 'rear_camera')
RELINK_ANGLE_RAD = np.radians(0.5)   # store again when a calibrated link moves this much
RELINK_HEIGHT_M = 0.03


def _hal():
  """hal.calibration's pieces, or None when the package is not installed."""
  return HARDWARE.hal_calibration()


class SideRearCalibration:
  """One ExtrinsicCalibrator per side/rear camera, fed from camera_calibrationd."""

  def __init__(self, geometry, unit_height_m: float | None = None, store_path: str | None = None,
               cameras=CAMERAS, hal=None) -> None:
    hal = hal or _hal()
    self.enabled = hal is not None and geometry is not None
    self.calibrators: dict = {}
    self._saved: dict = {}
    if not self.enabled:
      cloudlog.warning("side/rear calibration disabled: hal.calibration not available")
      return
    self._cm, self._ex, self._mount, self._store, self._tf = hal
    self.geometry = geometry
    self.store_path = store_path
    self.unit_height_m = self._mount.unit_height(geometry, unit_height_m)
    self.base_link = self._tf.Transform(z=self.unit_height_m)   # base_link in base_footprint
    tree = self._store.calibrated_tree(geometry, self.unit_height_m, store_path)
    road = geometry.ROAD_CAMERA
    self.road_camera_in_base = tree.lookup(self._tf.BASE_LINK, self._tf.link_frame(road))
    self._tree = tree
    self._cameras = [c for c in cameras if c in geometry.CAMERAS]

  def _calibrator(self, camera: str, image_size: tuple[int, int]):
    cal = self.calibrators.get(camera)
    if cal is None or cal.model.base.img_w != image_size[0]:
      nominal = self._cm.from_board(self.geometry, camera, self.unit_height_m, self._tree, image_size)
      cal = self._ex.ExtrinsicCalibrator(camera, nominal, self.base_link,
                                         limits=self._mount.mount_limits(self.geometry, camera))
      self.calibrators[camera] = cal
      cloudlog.info(f"side/rear calibration: {camera} starts at {nominal.link.as_dict()}")
    return cal

  # -- inputs ------------------------------------------------------------------
  def add_odometry(self, trans, rot, dt_s: float, v_ego: float | None,
                   device_from_calib: np.ndarray | None = None) -> None:
    """One cameraOdometry message (device frame rates) -> one base_link step for every camera."""
    if not self.enabled:
      return
    R, t = self._ex.odometry_step(trans, rot, dt_s, v_ego, device_from_calib, self.road_camera_in_base)
    for cal in self.calibrators.values():
      cal.add_ego_motion(R, t)

  def add_frame(self, camera: str, frame: np.ndarray) -> None:
    if not self.enabled or camera not in self._cameras:
      return
    cal = self._calibrator(camera, (frame.shape[1], frame.shape[0]))
    cal.process_frame(frame)
    self._maybe_save(camera, cal.result)

  def set_drivable_mask(self, camera: str, mask: np.ndarray | None) -> None:
    if self.enabled and camera in self.calibrators:
      self.calibrators[camera].set_drivable_mask(mask)

  # -- outputs -----------------------------------------------------------------
  def _maybe_save(self, camera: str, result) -> None:
    if not result.converged:
      return
    link = result.link
    last = self._saved.get(camera)
    if last is not None:
      moved = max(abs(link.roll - last.roll), abs(link.pitch - last.pitch),
                  abs((link.yaw - last.yaw + np.pi) % (2 * np.pi) - np.pi))
      if moved < RELINK_ANGLE_RAD and abs(link.z - last.z) < RELINK_HEIGHT_M:
        return
    in_base_link = self.base_link.inverse().compose(link)
    try:
      path = self._store.save_link(self._tf.link_frame(camera), in_base_link, self.store_path)
      self._saved[camera] = link
      cloudlog.info(f"side/rear calibration: {camera} calibrated, stored in {path}: {in_base_link.as_dict()}")
    except OSError as e:
      cloudlog.error(f"side/rear calibration: cannot store {camera}: {e}")

  def results(self) -> list:
    """CalibrationResult per camera seen so far (link in base_footprint)."""
    return [cal.result for cal in self.calibrators.values()]

  def reset(self, camera: str | None = None) -> None:
    """Drop the named camera's (or every camera's) in-progress calibration
    so the next add_frame() starts a fresh convergence -- e.g. after a
    mount is physically re-adjusted. Does not touch the persisted
    sensors_tf.yaml link: _maybe_save() overwrites it once the camera
    reconverges, so a reset that never drives again keeps the last-good
    calibration rather than silently losing it.
    """
    if not self.enabled:
      return
    names = [camera] if camera else list(self.calibrators)
    for name in names:
      self.calibrators.pop(name, None)
      self._saved.pop(name, None)
    cloudlog.info(f"side/rear calibration: reset {names}")

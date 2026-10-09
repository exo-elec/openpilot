"""
Card segmentation -> gridd's BEV grid, one camera at a time (drivableBev).

Each grid cell centre (on the road, z = 0) is projected into the camera once,
into a lookup table rebuilt only when that camera's pose or frame size
changes; each frame is then one fancy-index into the card's class map.
Projecting cells, not pixels, gives every cell exactly one sample and no
holes, and cells the camera cannot see stay UNKNOWN.

Frames: vehicle x forward, y LEFT, z up (the grid's and every yRel's;
CLAUDE.md "Frame conventions", as sunnypilot/comma). Cameras are
sided.bev_reprojector.SideCameraGeometry in that frame: side/rear from
exopilot hal's tf tree (hal_geometry), road/wide/tele from liveCalibration.
"""
from __future__ import annotations

import dataclasses
import math
from dataclasses import dataclass

import numpy as np

from openpilot.nagaspilot.daemons.gridd.lazy_bev import BEV_GRID, BEVGridSpec
from openpilot.nagaspilot.daemons.sided.bev_reprojector import (
  SideCameraGeometry, hal_geometry, make_default_geometry, make_rear_geometry)

# drivableBev cell values (custom.capnp DrivableBev.CameraBev.data)
UNKNOWN, OTHER, DRIVABLE, LANE = 0, 1, 2, 3
# card class map (card_segmenter OTHER/DRIVABLE/LANE = 0/1/2) -> cell value
CARD_TO_CELL = np.array([OTHER, DRIVABLE, LANE], dtype=np.uint8)

# Ground-plane range each camera is trusted over (m from the camera)
RANGE_M: dict[str, tuple[float, float]] = {
  'road': (3.0, 55.0), 'wide': (1.5, 30.0), 'tele': (20.0, 80.0),
  'side_left': (0.5, 20.0), 'side_right': (0.5, 20.0), 'rear': (0.5, 20.0),
}

# Front lenses (horizontal FOV deg, equidistant fisheye), as monod.CameraLens
FRONT_LENS: dict[str, tuple[float, bool]] = {'road': (40.0, False), 'wide': (150.0, True), 'tele': (20.4, False)}
DEFAULT_CAMERA_HEIGHT_M = 1.22   # windshield unit above the road when liveCalibration has no height
REAR_X_M = -3.0                  # nominal rear camera x from the unit, dev PC only (hal has the real one)


def device_from_calib(rpy) -> np.ndarray:
  """Upstream orientation.cc euler2rot: Rz(yaw) * Ry(pitch) * Rx(roll), [Forward, Right, Down].

  Written out here because openpilot/common/transformations/transformations.py
  is a dev-PC stub with a different euler order.
  """
  roll, pitch, yaw = (float(v) for v in rpy)
  cr, sr, cp, sp, cy, sy = math.cos(roll), math.sin(roll), math.cos(pitch), math.sin(pitch), math.cos(yaw), math.sin(yaw)
  rz = np.array([[cy, -sy, 0.0], [sy, cy, 0.0], [0.0, 0.0, 1.0]])
  ry = np.array([[cp, 0.0, sp], [0.0, 1.0, 0.0], [-sp, 0.0, cp]])
  rx = np.array([[1.0, 0.0, 0.0], [0.0, cr, -sr], [0.0, sr, cr]])
  return rz @ ry @ rx


def front_geometry(camera: str, img_w: float, img_h: float, rpy_calib, height_m: float) -> SideCameraGeometry:
  """Road/wide/tele at the unit (base_link x = y = 0), pose from liveCalibration.

  rpyCalib is modeld's device_from_calib euler. The camera's axes in the car
  frame are the rows of device_from_calib (R^T applied to each device axis),
  in [Forward, Right, Down]; flipped once to this module's x-forward / y-left
  / z-up and read back as SideCameraGeometry's yaw / pitch / roll.
  """
  hfov_deg, equidistant = FRONT_LENS[camera]
  half = math.radians(hfov_deg) / 2.0
  f = (img_w / 2.0) / (half if equidistant else math.tan(half))
  rpy = (list(rpy_calib) + [0.0, 0.0, 0.0])[:3]
  flip = np.array([1.0, -1.0, -1.0])                  # F,R,D -> x fwd, y left, z up
  axes = device_from_calib(rpy)                        # rows: device axes in the car frame
  fwd, right = axes[0] * flip, axes[1] * flip
  yaw = math.atan2(fwd[1], fwd[0])
  pitch = math.asin(float(np.clip(fwd[2], -1.0, 1.0)))
  right0 = np.array([math.sin(yaw), -math.cos(yaw), 0.0])
  down0 = np.cross(fwd, right0)
  roll = math.atan2(float(right @ down0), float(right @ right0))
  return SideCameraGeometry(
    fx=f, fy=f, cx=img_w / 2.0, cy=img_h / 2.0, img_w=img_w, img_h=img_h,
    cam_x_m=0.0, cam_y_m=0.0, cam_z_m=height_m,
    yaw_rad=yaw, pitch_rad=pitch, roll_rad=roll,
    lens="equidistant" if equidistant else "pinhole",
  )


def side_rear_geometry(camera: str, img_w: float, img_h: float) -> SideCameraGeometry:
  """Side/rear camera at its real place on the car: hal's tf tree, else nominal."""
  geom = hal_geometry(camera, img_w, img_h, rear_at_origin=False)
  if geom is not None:
    return geom
  if camera == 'rear':
    return dataclasses.replace(make_rear_geometry(img_w, img_h), cam_x_m=REAR_X_M)
  return make_default_geometry(camera, img_w, img_h)


@dataclass(frozen=True)
class BevLut:
  cells: np.ndarray    # flat indices of grid cells this camera sees
  pixels: np.ndarray   # flat indices into the class map, one per cell


def build_lut(geom: SideCameraGeometry, map_hw: tuple[int, int], camera: str,
              spec: BEVGridSpec = BEV_GRID) -> BevLut:
  """Project every grid cell centre on the road into the camera's class map."""
  map_h, map_w = map_hw
  rows, cols = np.indices((spec.rows, spec.cols))
  forward, left = spec.center(rows.ravel(), cols.ravel())
  p = np.c_[forward, left, np.zeros_like(forward)]
  p_cam = (geom.R_cv @ (p - geom.t_cv).T).T
  uv, valid = geom.pixels_from_camera(p_cam)
  lo, hi = RANGE_M.get(camera, (0.5, 55.0))
  dist = np.hypot(forward - geom.cam_x_m, left - geom.cam_y_m)
  valid &= (dist >= lo) & (dist <= hi)
  u = np.clip((uv[valid, 0] * map_w / geom.img_w).astype(np.int64), 0, map_w - 1)
  v = np.clip((uv[valid, 1] * map_h / geom.img_h).astype(np.int64), 0, map_h - 1)
  return BevLut(cells=np.flatnonzero(valid), pixels=v * map_w + u)


def project(class_map: np.ndarray, lut: BevLut, spec: BEVGridSpec = BEV_GRID) -> np.ndarray:
  """Card class map -> drivableBev cells (rows x cols uint8), UNKNOWN where unseen."""
  out = np.zeros(spec.rows * spec.cols, dtype=np.uint8)
  out[lut.cells] = CARD_TO_CELL[np.clip(class_map.ravel()[lut.pixels], 0, 2)]
  return out.reshape(spec.rows, spec.cols)

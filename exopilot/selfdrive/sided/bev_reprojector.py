#!/usr/bin/env python3
"""
Lazy BEV reprojection for side (blind-spot) cameras.

Side cameras are rear-facing AHD sensors mounted on the sides of the vehicle,
looking backward at ~90° from the longitudinal axis.  Because there is no
stereo depth, we use a ground-plane assumption + known camera extrinsics to
project 2D box bottom-centre points into the vehicle frame.

The resulting positions are **advisory only** — suitable for BSD/RCTA
warnings but NOT for trajectory planning.
"""

from __future__ import annotations

import math
from dataclasses import dataclass

import numpy as np

# ──────────────────────────────────────────────────────────────────────────────
# A ray must point at least this far down (unit z, ~1.1°) to be intersected
# with the ground; flatter rays are ranged by class width instead.
MIN_GROUND_RAY_Z = 0.02

# Side-camera class width priors (metres)
# ──────────────────────────────────────────────────────────────────────────────
CLASS_WIDTHS_M: dict[str, float] = {
  'person':     0.5,
  'bicycle':    0.5,
  'motorcycle': 0.7,
  'car':        1.8,
  'van':        2.0,
  'bus':        2.5,
  'truck':      2.5,
  'unknown':    1.8,
}

CLASS_LENGTHS_M: dict[str, float] = {
  'person':     0.5,
  'bicycle':    1.7,
  'motorcycle': 2.0,
  'car':        4.5,
  'van':        5.0,
  'bus':        12.0,
  'truck':      10.0,
  'unknown':    4.5,
}


@dataclass(frozen=True)
class SideCameraGeometry:
  """Extrinsic + intrinsic geometry for a side camera.

  Vehicle frame: +x = forward, +y = left, +z = up.
  Side cameras look roughly backward (+x is into the image, but the camera
  is pointing toward -x in the vehicle frame).
  """

  # Intrinsics
  fx: float
  fy: float
  cx: float
  cy: float
  img_w: float
  img_h: float

  # Extrinsics (camera centre in vehicle frame, metres)
  cam_x_m: float   # usually near 0 (at side of vehicle)
  cam_y_m: float   # + = left side, - = right side
  cam_z_m: float   # usually ~1.0 m (mount height)

  # Orientation
  yaw_rad: float = math.pi        # default: looking straight backward
  pitch_rad: float = -0.17        # ~10° down tilt
  ground_plane_z_m: float = 0.0
  roll_rad: float = 0.0           # rotation about the optical axis
  # Lens: "pinhole", or "equidistant" (r = f * theta) for the wide AHD
  # fisheyes -- 170 deg rear and 120 deg sides cannot be modelled as pinhole.
  lens: str = "pinhole"

  # Rays beyond this angle from the optical axis are not in the image
  MAX_THETA_RAD = math.radians(100.0)   # 170 deg HFOV at 16:9 reaches ~97.5 deg in the corners

  def rays_from_pixels(self, uv: np.ndarray) -> np.ndarray:
    """Pixels (N, 2) → camera-frame ray directions (N, 3), OpenCV axes."""
    uv = np.asarray(uv, dtype=np.float64).reshape(-1, 2)
    x = (uv[:, 0] - self.cx) / self.fx
    y = (uv[:, 1] - self.cy) / self.fy
    if self.lens == "equidistant":
      theta = np.hypot(x, y)
      scale = np.where(theta > 1e-9, np.sin(theta) / np.maximum(theta, 1e-9), 1.0)
      return np.c_[x * scale, y * scale, np.cos(theta)]
    return np.c_[x, y, np.ones(len(x))]

  def pixels_from_camera(self, p_cam: np.ndarray, in_image: bool = True) -> tuple[np.ndarray, np.ndarray]:
    """Camera-frame points (N, 3) → pixels (N, 2) and which are valid.

    Valid: in front of the lens and, with in_image, inside the image.
    """
    p = np.asarray(p_cam, dtype=np.float64).reshape(-1, 3)
    if self.lens == "equidistant":
      rho = np.hypot(p[:, 0], p[:, 1])
      theta = np.arctan2(rho, p[:, 2])
      k = np.where(rho > 1e-12, theta / np.maximum(rho, 1e-12), 0.0)
      uv = np.c_[self.fx * p[:, 0] * k + self.cx, self.fy * p[:, 1] * k + self.cy]
      valid = theta < self.MAX_THETA_RAD
    else:
      valid = p[:, 2] > 1e-3
      z = np.where(valid, p[:, 2], 1.0)
      uv = np.c_[self.fx * p[:, 0] / z + self.cx, self.fy * p[:, 1] / z + self.cy]
    if in_image:
      valid &= (uv[:, 0] >= 0) & (uv[:, 0] < self.img_w) & (uv[:, 1] >= 0) & (uv[:, 1] < self.img_h)
    return uv, valid

  @property
  def K(self) -> np.ndarray:
    """3×3 intrinsic matrix."""
    return np.array([
      [self.fx, 0.0,      self.cx],
      [0.0,     self.fy,  self.cy],
      [0.0,     0.0,      1.0],
    ], dtype=np.float64)

  @property
  def K_inv(self) -> np.ndarray:
    """Inverse of K."""
    return np.linalg.inv(self.K)

  @property
  def R_cv(self) -> np.ndarray:
    """Rotation matrix from vehicle frame to camera frame (OpenCV convention).

    OpenCV camera frame: +z forward (optical axis), +x right, +y down.
    Vehicle frame: +x forward, +y left, +z up. yaw is the optical axis's
    heading (0 = forward, pi = backward); pitch < 0 tilts it down; roll
    turns the image about the optical axis.

    Rows are the camera axes in vehicle coordinates, so p_cam = R (p - t).
    (The matrix this replaces was not a vehicle->camera rotation: at yaw 0,
    pitch 0 it was the identity, putting the optical axis on vehicle +z, so
    every ray pointed up and no box ever reached the ground.)
    """
    cy, sy = math.cos(self.yaw_rad), math.sin(self.yaw_rad)
    cp, sp = math.cos(self.pitch_rad), math.sin(self.pitch_rad)
    forward = np.array([cp * cy, cp * sy, sp])
    right = np.array([sy, -cy, 0.0])
    down = np.cross(forward, right)
    cr, sr = math.cos(self.roll_rad), math.sin(self.roll_rad)
    right, down = cr * right + sr * down, -sr * right + cr * down
    return np.stack([right, down, forward]).astype(np.float64)

  @property
  def t_cv(self) -> np.ndarray:
    """Translation vector (camera position in vehicle frame)."""
    return np.array([self.cam_x_m, self.cam_y_m, self.cam_z_m], dtype=np.float64)


def _estimate_distance_from_bbox(
  bbox: tuple[float, float, float, float],
  label: str,
  intrinsics: SideCameraGeometry,
) -> float:
  """Estimate object distance using physical width prior and box width."""
  width_m = CLASS_WIDTHS_M.get(label, CLASS_WIDTHS_M['unknown'])
  x1, y1, x2, y2 = bbox
  box_w_px = max(x2 - x1, 1.0)
  distance = (width_m * intrinsics.fx) / box_w_px
  return distance


def reproject_side_camera(
  bbox: tuple[float, float, float, float],
  label: str,
  img_shape: tuple[int, int],
  geo: SideCameraGeometry,
) -> tuple[float, float, float, float, float]:
  """Reproject a 2D bounding box from a side camera into vehicle frame.

  Uses ground-plane intersection of the ray through the box bottom-centre.
  Returns advisory 3D position + estimated width/length.

  Args:
    bbox: (x1, y1, x2, y2) in image pixels
    label: COCO class name
    img_shape: (h, w) of source image
    geo: SideCameraGeometry for this camera

  Returns:
    (x_m, y_m, z_m, width_m, length_m) in vehicle frame.
    x_m = longitudinal (positive = forward, typically small negative for side cams)
    y_m = lateral      (positive = left)
    z_m = height       (ground plane = 0)
  """
  x1, y1, x2, y2 = bbox
  u = (x1 + x2) / 2.0
  v = y2

  distance_cam = _estimate_distance_from_bbox(bbox, label, geo)

  d_cam = geo.rays_from_pixels(np.array([[u, v]]))[0]
  d_cam /= np.linalg.norm(d_cam)

  R = geo.R_cv.T
  d_vehicle = R @ d_cam

  if d_vehicle[2] > -MIN_GROUND_RAY_Z:
    # The ray through the box bottom is level or points up (box bottom at or
    # above the horizon): it never meets the ground. Dividing by it used to
    # put the object on the wrong side of the car, or kilometres away. Range
    # it by its class width along the ray instead.
    scale = distance_cam
  else:
    scale = (geo.ground_plane_z_m - geo.cam_z_m) / d_vehicle[2]

  point = geo.t_cv + scale * d_vehicle

  width_m = CLASS_WIDTHS_M.get(label, CLASS_WIDTHS_M['unknown'])
  length_m = CLASS_LENGTHS_M.get(label, CLASS_LENGTHS_M['unknown'])

  return float(point[0]), float(point[1]), float(point[2]), width_m, length_m


def fisheye_focal_px(img_w: float, hfov_deg: float) -> float:
  """Equidistant focal length (px per radian) for a horizontal field of view."""
  return (img_w / 2.0) / math.radians(hfov_deg / 2.0)


def make_rear_geometry(
  img_w: float = 1280.0,
  img_h: float = 720.0,
  hfov_deg: float = 153.0,
  height_m: float = 1.3,
  pitch_deg: float = -20.0,
) -> SideCameraGeometry:
  """Rear camera when exopilot's hal is not installed (a dev PC): its nominal mounting.

  Inside the car behind the back glass (rear windscreen), top centre under
  the high-mounted stop lamp, looking straight back and 20 deg down. 1.8 mm
  M12 fisheye on the 1 MP AHD sensor: 153 deg horizontal (the vendor's
  "170 deg" is diagonal), equidistant. Placed at the vehicle frame's x = 0 so
  distances stay "metres behind the rear camera", as reard publishes them.
  On the device, hal_geometry() places it from the tf tree and calibration.
  """
  fx = fisheye_focal_px(img_w, hfov_deg)
  return SideCameraGeometry(fx=fx, fy=fx, cx=img_w / 2.0, cy=img_h / 2.0, img_w=img_w, img_h=img_h,
                            cam_x_m=0.0, cam_y_m=0.0, cam_z_m=height_m,
                            yaw_rad=math.pi, pitch_rad=math.radians(pitch_deg), lens="equidistant")


# Side cameras' nominal mounting (exopilot hal's camera geometry, which wins
# when installed): the side turn-lamp / side-badge spot on each front fender,
# looking back and turned 15 deg outward, 10 deg down, ~0.85 m above the road.
SIDE_YAW_RAD = {'side_left': math.radians(165.0), 'side_right': math.radians(195.0)}
SIDE_Y_M = {'side_left': 0.90, 'side_right': -0.90}
SIDE_X_M = 0.7
SIDE_HEIGHT_M = 0.85
SIDE_PITCH_RAD = math.radians(-10.0)


def make_default_geometry(
  side: str,
  img_w: float = 1280.0,
  img_h: float = 720.0,
  hfov_deg: float = 98.0,
) -> SideCameraGeometry:
  """Side camera when exopilot's hal is not installed (a dev PC): its nominal mounting.

  2.8 mm M12 fisheye on the 1 MP AHD sensor: 98 deg horizontal (the
  vendor's "120 deg" is diagonal), equidistant. Position and angles:
  SIDE_* above. On the device, hal_geometry() places it from the tf tree
  and calibration.
  """
  fx = fisheye_focal_px(img_w, hfov_deg)
  return SideCameraGeometry(
    fx=fx, fy=fx, cx=img_w / 2.0, cy=img_h / 2.0, img_w=img_w, img_h=img_h,
    cam_x_m=SIDE_X_M, cam_y_m=SIDE_Y_M[side], cam_z_m=SIDE_HEIGHT_M,
    yaw_rad=SIDE_YAW_RAD[side], pitch_rad=SIDE_PITCH_RAD, lens="equidistant",
  )


HAL_CAMERA = {'side_left': 'side_left', 'side_right': 'side_right', 'rear': 'rear_camera'}


def hal_geometry(camera: str, img_w: float = 1280.0, img_h: float = 720.0,
                 geometry=None, rear_at_origin: bool = True) -> SideCameraGeometry | None:
  """A side/rear camera from exopilot's hal: lens from the board's camera
  geometry, pose from the tf tree with any stored calibration
  (hal.calibration, written by camera_calibrationd). None without hal.

  Vehicle frame is base_footprint (road under the ExoPilot unit centre); the
  rear camera is moved to x = 0 (reard's "metres behind the rear camera")
  unless rear_at_origin is False (segd's drivable BEV needs its real place).
  """
  from openpilot.system.hardware import HARDWARE
  if geometry is None:
    geometry = HARDWARE.hal_module("camera_geometry")
  calibration = HARDWARE.hal_calibration()
  if geometry is None or calibration is None:
    return None
  camera_model, _, mounting, store, _ = calibration
  from_board, unit_height, calibrated_tree = camera_model.from_board, mounting.unit_height, store.calibrated_tree
  name = HAL_CAMERA.get(camera, camera)
  if name not in geometry.CAMERAS:
    return None
  h = unit_height(geometry, None)
  model = from_board(geometry, name, h, calibrated_tree(geometry, h), (int(img_w), int(img_h)))
  L = model.link
  return SideCameraGeometry(
    fx=model.fx, fy=model.fy, cx=model.cx, cy=model.cy, img_w=model.img_w, img_h=model.img_h,
    cam_x_m=0.0 if camera == 'rear' and rear_at_origin else L.x, cam_y_m=L.y, cam_z_m=L.z,
    yaw_rad=L.yaw, pitch_rad=-L.pitch, roll_rad=L.roll,   # ROS pitch is positive down
    lens=model.projection,
  )


def geometry_from_calibration(
  side: str,
  calib,
  default_img_w: float = 1280.0,
  default_img_h: float = 720.0,
) -> SideCameraGeometry:
  """Create SideCameraGeometry from SingleCameraCalibration.

  Args:
    side: 'side_left' or 'side_right'
    calib: SingleCameraCalibration object (from CalibrationStorage)
    default_img_w: fallback image width if not in calibration
    default_img_h: fallback image height if not in calibration

  Returns:
    SideCameraGeometry with intrinsics + extrinsics from calibration,
    lateral position from platform defaults.
  """
  # Intrinsics from calibration
  fx = calib.focal_x if calib.focal_x > 0 else default_img_w / (2.0 * math.tan(math.radians(60.0)))
  fy = calib.focal_y if calib.focal_y > 0 else fx
  cx = calib.center_x if calib.center_x > 0 else default_img_w / 2.0
  cy = calib.center_y if calib.center_y > 0 else default_img_h / 2.0
  img_w = calib.image_width if calib.image_width > 0 else default_img_w
  img_h = calib.image_height if calib.image_height > 0 else default_img_h

  # Extrinsics from calibration (RPY = [roll, pitch, yaw])
  yaw = float(calib.rpy[2]) if len(calib.rpy) > 2 else SIDE_YAW_RAD.get(side, math.pi)
  pitch = float(calib.rpy[1]) if len(calib.rpy) > 1 else SIDE_PITCH_RAD
  height = calib.height if calib.height > 0 else SIDE_HEIGHT_M

  # Position from platform defaults; the rear camera sits on the centreline
  # at x = 0 (see make_rear_geometry)
  cam_y = SIDE_Y_M.get(side, 0.0)
  cam_x = 0.0 if side == 'rear' else SIDE_X_M

  return SideCameraGeometry(
    fx=fx,
    fy=fy,
    cx=cx,
    cy=cy,
    img_w=img_w,
    img_h=img_h,
    cam_x_m=cam_x,
    cam_y_m=cam_y,
    cam_z_m=height,
    yaw_rad=yaw,
    pitch_rad=pitch,
  )

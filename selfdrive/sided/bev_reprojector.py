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
    """
    cy = math.cos(self.yaw_rad)
    sy = math.sin(self.yaw_rad)
    cp = math.cos(self.pitch_rad)
    sp = math.sin(self.pitch_rad)

    R = np.array([
      [cp * cy,  cp * (-sy), -sp],
      [sy,       cy,          0.0],
      [sp * cy,  sp * (-sy),  cp],
    ], dtype=np.float64)
    return R

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

  p_img = np.array([u, v, 1.0], dtype=np.float64)
  p_norm = geo.K_inv @ p_img

  d_cam = np.array([p_norm[0], p_norm[1], 1.0], dtype=np.float64)
  d_cam /= np.linalg.norm(d_cam)

  R = geo.R_cv.T
  d_vehicle = R @ d_cam

  if abs(d_vehicle[2]) < 1e-6:
    scale = distance_cam
  else:
    scale = (geo.ground_plane_z_m - geo.cam_z_m) / d_vehicle[2]

  point = geo.t_cv + scale * d_vehicle

  width_m = CLASS_WIDTHS_M.get(label, CLASS_WIDTHS_M['unknown'])
  length_m = CLASS_LENGTHS_M.get(label, CLASS_LENGTHS_M['unknown'])

  return float(point[0]), float(point[1]), float(point[2]), width_m, length_m


def make_default_geometry(
  side: str,
  img_w: float = 1280.0,
  img_h: float = 720.0,
  hfov_deg: float = 120.0,
) -> SideCameraGeometry:
  """Create default SideCameraGeometry for left or right side camera.

  Cameras are mounted on the hood fender under side mirrors,
  rear-pointing and yawed 30° outward (parallel to ground).

    left:  yaw = 150°  (back + 30° left),   y = +0.85 m
    right: yaw = 210°  (back + 30° right),  y = −0.85 m
  """
  fx = img_w / (2.0 * math.tan(math.radians(hfov_deg / 2.0)))
  fy = fx
  cx = img_w / 2.0
  cy = img_h / 2.0

  if side == 'side_left':
    cam_y = 0.85
    yaw = math.pi - math.pi / 6.0   # 150°
  else:
    cam_y = -0.85
    yaw = math.pi + math.pi / 6.0   # 210° (-150°)

  return SideCameraGeometry(
    fx=fx,
    fy=fy,
    cx=cx,
    cy=cy,
    img_w=img_w,
    img_h=img_h,
    cam_x_m=0.7,
    cam_y_m=cam_y,
    cam_z_m=0.75,
    yaw_rad=yaw,
    pitch_rad=0.0,        # parallel to ground
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
  yaw = float(calib.rpy[2]) if len(calib.rpy) > 2 else (math.pi - math.pi / 6.0 if side == 'side_left' else math.pi + math.pi / 6.0)
  pitch = float(calib.rpy[1]) if len(calib.rpy) > 1 else 0.0
  height = calib.height if calib.height > 0 else 0.75

  # Lateral position from platform defaults
  if side == 'side_left':
    cam_y = 0.85
  else:
    cam_y = -0.85

  return SideCameraGeometry(
    fx=fx,
    fy=fy,
    cx=cx,
    cy=cy,
    img_w=img_w,
    img_h=img_h,
    cam_x_m=0.7,
    cam_y_m=cam_y,
    cam_z_m=height,
    yaw_rad=yaw,
    pitch_rad=pitch,
  )

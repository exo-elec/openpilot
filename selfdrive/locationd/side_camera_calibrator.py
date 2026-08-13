#!/usr/bin/env python3
"""
Side Camera Extrinsic Calibrator — Pure Geometry, Zero Semantic Detection

Calibrates side camera extrinsics (yaw, pitch, roll, x, y, z) relative to the
vehicle frame using ONLY:
  1. Known ego motion from forward-camera odometry (cameraOdometry)
  2. KLT feature tracking in side camera frames
  3. Epipolar geometry constraint

NO lane detection, NO object detection, NO road segmentation.

Theory:
  T_side  = side camera extrinsics relative to vehicle  [R_side | t_side]
  T_ego   = vehicle ego motion between frames            [R_ego  | t_ego]
  T_rel   = T_side · T_ego · T_side^{-1}               (side cam motion)
  E       = [t_rel]_× · R_rel                           (essential matrix)

For a tracked feature p_i → p_j between consecutive side frames:
  p_j^T · K^{-T} · E · K^{-1} · p_i  ≈  0

We optimize T_side (6-DOF) to minimize this error over many frames.
"""

from __future__ import annotations

import time
from dataclasses import dataclass

import cv2
import numpy as np

from openpilot.common.swaglog import cloudlog


# ---------------------------------------------------------------------------
# Parameters
# ---------------------------------------------------------------------------
MIN_TRANSLATION_M = 0.3        # Minimum ego motion between frames to use pair
MAX_FEATURES = 200             # KLT max corners tracked
KLT_WIN_SIZE = (21, 21)
KLT_MAX_LEVEL = 3
FEATURE_QUALITY = 0.01
FEATURE_MIN_DIST = 10.0

OPT_ITERATIONS = 30
OPT_CONVERGENCE_THRESH = 1e-5

# Side camera intrinsics defaults (720p UVC)
DEFAULT_FX = 640.0
DEFAULT_FY = 640.0
DEFAULT_CX = 640.0
DEFAULT_CY = 360.0


def _load_side_intrinsics(camera_name: str) -> tuple[float, float, float, float]:
  """Load side camera intrinsics from exopilot HAL; fall back to defaults."""
  try:
    from hal.platform.rk3588_camera_geometry import FOCAL_PX, IMAGE_SIZE_PX
    w, h = IMAGE_SIZE_PX[camera_name]
    fx, fy = FOCAL_PX[camera_name]
    return fx, fy, w / 2.0, h / 2.0
  except Exception:
    return DEFAULT_FX, DEFAULT_FY, DEFAULT_CX, DEFAULT_CY


@dataclass
class CalibrationResult:
  """Calibrated extrinsics for one side camera."""
  converged: bool = False
  roll_rad: float = 0.0
  pitch_rad: float = 0.0
  yaw_rad: float = 0.0
  tx_m: float = 0.0
  ty_m: float = 0.0
  tz_m: float = 0.0
  rmse_px: float = 999.0
  num_pairs: int = 0

  def to_rpy_vec(self) -> np.ndarray:
    return np.array([self.roll_rad, self.pitch_rad, self.yaw_rad])

  def to_t_vec(self) -> np.ndarray:
    return np.array([self.tx_m, self.ty_m, self.tz_m])


class SideCameraCalibrator:
  """
  Pure-geometric side camera calibrator.

  Usage (inside camera_calibrationd main loop):
      cal = SideCameraCalibrator(camera_name='side_left')
      while driving:
          cal.process_frame(gray_frame, R_ego, t_ego)
          if cal.is_converged():
              result = cal.get_result()
  """

  def __init__(
    self,
    camera_name: str,
    fx: float | None = None,
    fy: float | None = None,
    cx: float | None = None,
    cy: float | None = None,
    init_yaw: float = np.pi - np.pi / 6.0,   # 150° left default
    init_pitch: float = 0.0,
    init_roll: float = 0.0,
    init_t: tuple[float, float, float] = (0.7, 0.85, 0.75),
  ) -> None:
    self.camera_name = camera_name
    hal_fx, hal_fy, hal_cx, hal_cy = _load_side_intrinsics(camera_name)
    fx = fx if fx is not None else hal_fx
    fy = fy if fy is not None else hal_fy
    cx = cx if cx is not None else hal_cx
    cy = cy if cy is not None else hal_cy
    self.K = np.array([[fx, 0.0, cx], [0.0, fy, cy], [0.0, 0.0, 1.0]])
    self.K_inv = np.linalg.inv(self.K)

    # Current estimate: 6-DOF [roll, pitch, yaw, tx, ty, tz]
    self.params = np.array([
      init_roll, init_pitch, init_yaw,
      init_t[0], init_t[1], init_t[2],
    ], dtype=np.float64)

    # KLT state
    self.prev_gray: np.ndarray | None = None
    self.prev_pts: np.ndarray | None = None

    # Frame-pair buffer for optimization
    self.pairs: list[tuple[np.ndarray, np.ndarray, np.ndarray, np.ndarray]] = []
    self.max_pairs = 150

    self.result = CalibrationResult()
    self._last_opt_t = 0.0
    self._opt_interval_s = 2.0
    self._total_pairs_processed = 0

    cloudlog.info(
      f"SideCameraCalibrator({camera_name}): init params={self.params.round(3)}"
    )

  # -----------------------------------------------------------------------
  # Public API
  # -----------------------------------------------------------------------
  def process_frame(
    self,
    frame_bgr: np.ndarray,
    R_ego: np.ndarray,
    t_ego: np.ndarray,
  ) -> None:
    """Process one side camera frame + ego motion since last frame.

    Args:
      frame_bgr: BGR frame from side camera
      R_ego: 3×3 rotation matrix (vehicle frame at t → t+1)
      t_ego: 3-vector translation in meters (vehicle frame at t → t+1)
    """
    gray = cv2.cvtColor(frame_bgr, cv2.COLOR_BGR2GRAY)

    # Initialize on first frame
    if self.prev_gray is None:
      self.prev_gray = gray
      self.prev_pts = self._detect_corners(gray)
      return

    # Skip if motion too small
    if np.linalg.norm(t_ego) < MIN_TRANSLATION_M:
      self.prev_gray = gray
      self.prev_pts = self._redetect_if_needed(gray, self.prev_pts)
      return

    # KLT track
    tracked_prev, tracked_cur = self._track_klt(self.prev_gray, gray, self.prev_pts)
    if len(tracked_prev) < 8:
      self.prev_gray = gray
      self.prev_pts = self._detect_corners(gray)
      return

    # Store pair (p_i, p_j, R_ego, t_ego)
    self.pairs.append((tracked_prev, tracked_cur, R_ego.copy(), t_ego.copy()))
    if len(self.pairs) > self.max_pairs:
      self.pairs.pop(0)

    self.prev_gray = gray
    self.prev_pts = tracked_cur.reshape(-1, 1, 2)
    self._total_pairs_processed += 1

    # Run optimization periodically
    now = time.monotonic()
    if now - self._last_opt_t >= self._opt_interval_s and len(self.pairs) >= 10:
      self._optimize()
      self._last_opt_t = now

  def is_converged(self) -> bool:
    return self.result.converged

  def get_result(self) -> CalibrationResult:
    return self.result

  # -----------------------------------------------------------------------
  # KLT tracking (no semantic detection)
  # -----------------------------------------------------------------------
  def _detect_corners(self, gray: np.ndarray) -> np.ndarray:
    pts = cv2.goodFeaturesToTrack(
      gray, MAX_FEATURES, FEATURE_QUALITY, FEATURE_MIN_DIST
    )
    return pts if pts is not None else np.zeros((0, 1, 2), dtype=np.float32)

  def _redetect_if_needed(
    self, gray: np.ndarray, pts: np.ndarray
  ) -> np.ndarray:
    if len(pts) < MAX_FEATURES // 2:
      return self._detect_corners(gray)
    return pts

  def _track_klt(
    self,
    prev_gray: np.ndarray,
    cur_gray: np.ndarray,
    prev_pts: np.ndarray,
  ) -> tuple[np.ndarray, np.ndarray]:
    if len(prev_pts) == 0:
      return np.zeros((0, 2)), np.zeros((0, 2))

    next_pts, status, _ = cv2.calcOpticalFlowPyrLK(
      prev_gray, cur_gray, prev_pts, None,
      winSize=KLT_WIN_SIZE, maxLevel=KLT_MAX_LEVEL,
      criteria=(cv2.TERM_CRITERIA_EPS | cv2.TERM_CRITERIA_COUNT, 30, 0.01)
    )

    if next_pts is None or status is None:
      return np.zeros((0, 2)), np.zeros((0, 2))

    good = status.ravel().astype(bool)
    # Additional filter: reject points that moved too far (> 60 px)
    if len(next_pts) > 0 and len(prev_pts) > 0:
      motion = np.linalg.norm(next_pts.reshape(-1, 2) - prev_pts.reshape(-1, 2), axis=1)
      good &= motion < 60.0

    prev_good = prev_pts.reshape(-1, 2)[good]
    next_good = next_pts.reshape(-1, 2)[good]
    return prev_good, next_good

  # -----------------------------------------------------------------------
  # Epipolar error computation
  # -----------------------------------------------------------------------
  def _epipolar_errors(
    self,
    params: np.ndarray,
    pairs: list,
  ) -> np.ndarray:
    """Compute epipolar errors for all pairs. Returns 1D array of errors."""
    R_side = self._euler_to_rot(params[0], params[1], params[2])
    t_side = params[3:6]

    errors: list[float] = []
    for pts_i, pts_j, R_ego, t_ego in pairs:
      T_rel = self._compute_relative_transform(R_side, t_side, R_ego, t_ego)
      R_rel = T_rel[:3, :3]
      t_rel = T_rel[:3, 3]

      E = self._skew_symmetric(t_rel) @ R_rel
      F = self.K_inv.T @ E @ self.K_inv  # Fundamental-like matrix

      # Error = p_j^T · F · p_i  (Sampson distance approx = algebraic error / denom)
      for pi, pj in zip(pts_i, pts_j, strict=False):
        pi_h = np.array([pi[0], pi[1], 1.0])
        pj_h = np.array([pj[0], pj[1], 1.0])
        num = abs(pj_h @ F @ pi_h)
        # Denominator for Sampson distance
        F_pi = F @ pi_h
        Ft_pj = F.T @ pj_h
        denom = F_pi[0]**2 + F_pi[1]**2 + Ft_pj[0]**2 + Ft_pj[1]**2
        if denom > 1e-12:
          err = num / np.sqrt(denom)
        else:
          err = num
        errors.append(err)

    return np.array(errors, dtype=np.float64)

  def _compute_relative_transform(
    self,
    R_side: np.ndarray,
    t_side: np.ndarray,
    R_ego: np.ndarray,
    t_ego: np.ndarray,
  ) -> np.ndarray:
    """T_rel = T_side · T_ego · T_side^{-1}"""
    T_side = np.eye(4)
    T_side[:3, :3] = R_side
    T_side[:3, 3] = t_side

    T_ego_4 = np.eye(4)
    T_ego_4[:3, :3] = R_ego
    T_ego_4[:3, 3] = t_ego

    T_side_inv = np.linalg.inv(T_side)
    T_rel = T_side @ T_ego_4 @ T_side_inv
    return T_rel

  @staticmethod
  def _skew_symmetric(v: np.ndarray) -> np.ndarray:
    return np.array([
      [0.0, -v[2], v[1]],
      [v[2], 0.0, -v[0]],
      [-v[1], v[0], 0.0],
    ])

  @staticmethod
  def _euler_to_rot(roll: float, pitch: float, yaw: float) -> np.ndarray:
    """XYZ rotation order (roll about X, pitch about Y, yaw about Z)."""
    cr, sr = np.cos(roll), np.sin(roll)
    cp, sp = np.cos(pitch), np.sin(pitch)
    cy, sy = np.cos(yaw), np.sin(yaw)

    R_x = np.array([[1, 0, 0], [0, cr, -sr], [0, sr, cr]])
    R_y = np.array([[cp, 0, sp], [0, 1, 0], [-sp, 0, cp]])
    R_z = np.array([[cy, -sy, 0], [sy, cy, 0], [0, 0, 1]])
    return R_z @ R_y @ R_x

  # -----------------------------------------------------------------------
  # Optimization — coordinate descent (no scipy dependency)
  # -----------------------------------------------------------------------
  def _optimize(self) -> None:
    """Optimize extrinsics to minimize mean Sampson distance."""
    if len(self.pairs) < 5:
      return

    best_params = self.params.copy()
    best_cost = np.mean(self._epipolar_errors(best_params, self.pairs) ** 2)

    # Coordinate descent with shrinking step size
    step_sizes = np.array([0.02, 0.02, 0.02, 0.05, 0.05, 0.05])  # rpy, xyz
    for _iteration in range(OPT_ITERATIONS):
      improved = False
      for dim in range(6):
        for sign in (-1, 1):
          cand = best_params.copy()
          cand[dim] += sign * step_sizes[dim]
          cost = np.mean(self._epipolar_errors(cand, self.pairs) ** 2)
          if cost < best_cost:
            best_cost = cost
            best_params = cand
            improved = True
      if not improved:
        step_sizes *= 0.5
        if np.max(step_sizes) < OPT_CONVERGENCE_THRESH:
          break

    self.params = best_params
    rmse = np.sqrt(best_cost)

    # Update result
    self.result = CalibrationResult(
      converged=rmse < 2.0 and len(self.pairs) >= 30,
      roll_rad=float(best_params[0]),
      pitch_rad=float(best_params[1]),
      yaw_rad=float(best_params[2]),
      tx_m=float(best_params[3]),
      ty_m=float(best_params[4]),
      tz_m=float(best_params[5]),
      rmse_px=float(rmse),
      num_pairs=len(self.pairs),
    )

    cloudlog.debug(
      f"SideCal({self.camera_name}): rmse={rmse:.3f}px pairs={len(self.pairs)} " +
      f"rpy=({np.degrees(best_params[:3]).round(2)}) " +
      f"t=({best_params[3:6].round(3)})"
    )

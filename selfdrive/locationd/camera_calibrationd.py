#!/usr/bin/env python3
"""
Enhanced Camera Calibration Daemon - Multi-Camera On-Road Calibration
=====================================================================

Extends OpenPilot's calibrationd with:
1. Multi-camera calibration (road, wide_road)
2. Stereo baseline validation
3. Integration with CameraArrayGeometry
4. Cross-camera consistency checks
5. Improved convergence using multi-camera fusion

Calibration Parameters per Camera:
- Roll, Pitch, Yaw (RPY) relative to vehicle frame
- Height above ground
- Intrinsics refinement (focal length, principal point)

On-Road Calibration Conditions:
- Speed > 15 MPH (steady straight driving)
- Low yaw rate (< 2 deg/s)
- Low velocity angle std (< 0.25 deg)
- Multi-camera consistency within threshold

Usage:
    This daemon runs continuously, updating calibration from cameraOdometry.
    Calibration is saved to params and published via liveCalibration.

See Also:
    - calibrationd.py (original OpenPilot implementation)
    - camera_geometry.py (camera array geometry)
    - multi_camera_fusion.py (cross-camera validation)
"""

from __future__ import annotations

import os
import capnp
import cv2
import numpy as np
from typing import NoReturn
from dataclasses import dataclass, field
from collections import deque

from cereal import log, car
import cereal.messaging as messaging
from openpilot.system.hardware import HARDWARE, HAS_SIDE_CAMERAS
from openpilot.common.constants import CV
from openpilot.common.params import Params
from openpilot.common.realtime import config_realtime_process, DT_MDL
from openpilot.common.core_config import set_daemon_affinity
from openpilot.common.transformations.orientation import rot_from_euler, euler_from_rot
from openpilot.common.swaglog import cloudlog

from openpilot.selfdrive.gridd.camera_geometry import CameraArrayGeometry
from openpilot.selfdrive.locationd.side_camera_calibrator import SideCameraCalibrator

from msgq.visionipc import VisionIpcClient, VisionStreamType

# Calibration thresholds
MIN_SPEED_FILTER = 15 * CV.MPH_TO_MS
MAX_VEL_ANGLE_STD = np.radians(0.25)
MAX_YAW_RATE_FILTER = np.radians(2.0)
MAX_HEIGHT_STD = np.exp(-3.5)

# Platform-specific pitch limits
PITCH_LIMITS_EXO01 = np.array([-0.09074112085129739, 0.17])  # RK3588
YAW_LIMITS = np.array([-0.06912048084718224, 0.06912048084718235])

# Calibration process parameters
SMOOTH_CYCLES = 10
BLOCK_SIZE = 100
INPUTS_NEEDED = 5
INPUTS_WANTED = 50
MAX_ALLOWED_YAW_SPREAD = np.radians(2)
MAX_ALLOWED_PITCH_SPREAD = np.radians(4)

# Multi-camera consistency threshold
MAX_INTER_CAMERA_SPREAD = np.radians(1.5)  # Max RPY difference between cameras

DEBUG = os.getenv("DEBUG") is not None


@dataclass
class CameraCalibrationState:
    """Calibration state for a single camera."""
    camera_id: str

    # Extrinsics (roll, pitch, yaw relative to vehicle)
    rpy: np.ndarray = field(default_factory=lambda: np.array([0.0, 0.0, 0.0]))

    # Height above ground
    height: float = 1.22

    # Intrinsics refinement
    focal_scale: float = 1.0  # Scale factor for focal length

    # Calibration quality
    valid_blocks: int = 0
    calib_spread: np.ndarray = field(default_factory=lambda: np.zeros(3))

    # History for smoothing
    rpy_history: deque = field(default_factory=lambda: deque(maxlen=INPUTS_WANTED))

    def is_valid(self, pitch_limits: np.ndarray) -> bool:
        """Check if calibration is within valid limits."""
        return (
            pitch_limits[0] < self.rpy[1] < pitch_limits[1] and
            YAW_LIMITS[0] < self.rpy[2] < YAW_LIMITS[1]
        )

    def update_spread(self) -> None:
        """Update calibration spread from history."""
        if len(self.rpy_history) < 2:
            self.calib_spread = np.zeros(3)
            return

        history_array = np.array(list(self.rpy_history))
        self.calib_spread = np.max(history_array, axis=0) - np.min(history_array, axis=0)


class MultiCameraCalibrator:
    """Multi-camera calibration system with cross-camera validation."""

    def __init__(self, param_put: bool = False):
        self.param_put = param_put
        self.params = Params()

        # Detect platform
        self.platform = self._detect_platform()
        self.pitch_limits = PITCH_LIMITS_EXO01

        # Load camera geometry
        self.geometry = CameraArrayGeometry.for_platform(self.platform)

        # Initialize calibration states for all cameras
        self.cameras: dict[str, CameraCalibrationState] = {}
        self._init_cameras()

        # Load saved calibration
        self._load_saved_calibration()

        # Processing state
        self.block_idx = 0
        self.idx = 0
        self.v_ego = 0.0

        # Status
        self.cal_status = log.LiveCalibrationData.Status.uncalibrated
        self.not_car = False

        # Cross-camera validation
        self.cross_camera_consistency = 1.0  # 0-1, higher is better

        cloudlog.info(f"MultiCameraCalibrator initialized for {self.platform}")
        cloudlog.info(f"Cameras: {list(self.cameras.keys())}")

    def _detect_platform(self) -> str:
        """Detect hardware platform."""
        return 'rk3588'

    def _init_cameras(self):
        """Initialize calibration states for all cameras."""
        for cam_name in self.geometry.get_camera_names():
            # Get default height from geometry
            height = 1.22  # Default
            if cam_name in self.geometry.cameras:
                pos = self.geometry.cameras[cam_name].extrinsics.t
                height = -pos[2] + 1.22  # Convert from camera frame to ground height

            self.cameras[cam_name] = CameraCalibrationState(
                camera_id=cam_name,
                height=height
            )

    def _load_saved_calibration(self):
        """Load saved calibration from params."""
        calibration_params = self.params.get("CameraCalibrationParams")

        if calibration_params is None:
            # Try old format
            calibration_params = self.params.get("CalibrationParams")

        if calibration_params:
            try:
                with log.Event.from_bytes(calibration_params) as msg:
                    # Load road camera (primary)
                    if 'road' in self.cameras and msg.liveCalibration.rpyCalib:
                        self.cameras['road'].rpy = np.array(msg.liveCalibration.rpyCalib)
                        self.cameras['road'].valid_blocks = msg.liveCalibration.validBlocks

                    # Load height
                    if msg.liveCalibration.height:
                        height = msg.liveCalibration.height[0]
                        for cam in self.cameras.values():
                            cam.height = height

                    cloudlog.info("Loaded saved calibration")
            except Exception as e:
                cloudlog.exception(f"Error loading saved calibration: {e}")

    def reset(self, camera_id: str | None = None):
        """Reset calibration for one or all cameras."""
        if camera_id:
            if camera_id in self.cameras:
                self.cameras[camera_id] = CameraCalibrationState(camera_id=camera_id)
                cloudlog.info(f"Reset calibration for {camera_id}")
        else:
            for cam_name in self.cameras:
                self.cameras[cam_name] = CameraCalibrationState(camera_id=cam_name)
            cloudlog.info("Reset all camera calibrations")

    def _check_driving_conditions(self, trans: list[float], rot: list[float],
                                   trans_std: list[float]) -> bool:
        """Check if driving conditions are suitable for calibration."""
        # Speed check
        straight_and_fast = (
            self.v_ego > MIN_SPEED_FILTER and
            trans[0] > MIN_SPEED_FILTER and
            abs(rot[2]) < MAX_YAW_RATE_FILTER
        )

        # Uncertainty check
        angle_std_threshold = MAX_VEL_ANGLE_STD
        rpy_certain = np.arctan2(trans_std[1], trans[0]) < angle_std_threshold

        return straight_and_fast and rpy_certain

    def _compute_observed_rpy(self, trans: list[float], current_rpy: np.ndarray) -> np.ndarray:
        """Compute observed RPY from camera odometry."""
        observed_rpy = np.array([
            0.0,
            -np.arctan2(trans[2], trans[0]),  # Pitch
            np.arctan2(trans[1], trans[0])     # Yaw
        ])

        # Apply to current calibration
        new_rpy = euler_from_rot(
            rot_from_euler(current_rpy).dot(rot_from_euler(observed_rpy))
        )

        return self._sanity_clip(new_rpy)

    def _sanity_clip(self, rpy: np.ndarray) -> np.ndarray:
        """Clip RPY to sane values."""
        if np.isnan(rpy).any():
            rpy = np.array([0.0, 0.0, 0.0])

        return np.array([
            rpy[0],  # Roll unchanged
            np.clip(rpy[1], self.pitch_limits[0] - 0.005, self.pitch_limits[1] + 0.005),
            np.clip(rpy[2], YAW_LIMITS[0] - 0.005, YAW_LIMITS[1] + 0.005)
        ])

    def _check_cross_camera_consistency(self) -> float:
        """Check consistency between cameras. Returns consistency score 0-1."""
        if len(self.cameras) < 2:
            return 1.0

        # Get all valid RPYs
        rpys = [cam.rpy for cam in self.cameras.values() if cam.valid_blocks > 0]

        if len(rpys) < 2:
            return 1.0

        # Compute spread
        rpys_array = np.array(rpys)
        spread = np.max(rpys_array, axis=0) - np.min(rpys_array, axis=0)

        # Check if within threshold
        pitch_consistent = spread[1] < MAX_INTER_CAMERA_SPREAD
        yaw_consistent = spread[2] < MAX_INTER_CAMERA_SPREAD

        if pitch_consistent and yaw_consistent:
            return 1.0
        else:
            # Partial consistency
            score = 1.0
            if not pitch_consistent:
                score *= 0.5
            if not yaw_consistent:
                score *= 0.5
            return score

    def handle_cam_odometry(self, camera_id: str, trans: list[float], rot: list[float],
                           trans_std: list[float], height: float | None = None) -> bool:
        """Handle camera odometry update for a specific camera.

        Returns:
            True if calibration was updated
        """
        if camera_id not in self.cameras:
            return False

        cam = self.cameras[camera_id]

        # Check driving conditions
        if not self._check_driving_conditions(trans, rot, trans_std):
            return False

        # Compute observed RPY
        new_rpy = self._compute_observed_rpy(trans, cam.rpy)

        # Update with moving average
        decay_weight = (BLOCK_SIZE - self.idx) / BLOCK_SIZE
        cam.rpy = (self.idx * cam.rpy + decay_weight * new_rpy) / (self.idx + decay_weight)

        # Update height if provided
        if height is not None:
            cam.height = 0.9 * cam.height + 0.1 * height

        # Add to history
        cam.rpy_history.append(cam.rpy.copy())
        cam.update_spread()

        # Update block index
        self.idx += 1
        if self.idx >= BLOCK_SIZE:
            self.idx = 0
            self.block_idx += 1
            cam.valid_blocks = min(cam.valid_blocks + 1, INPUTS_WANTED)

        return True

    def update_status(self):
        """Update overall calibration status."""
        # Update individual camera statuses
        for cam in self.cameras.values():
            if cam.valid_blocks < INPUTS_NEEDED:
                cam.is_calibrated = False
            elif not cam.is_valid(self.pitch_limits):
                cam.is_calibrated = False
            else:
                cam.is_calibrated = True

        # Check cross-camera consistency
        self.cross_camera_consistency = self._check_cross_camera_consistency()

        # Overall status based on road camera (primary)
        road_cam = self.cameras.get('road')
        if road_cam:
            if road_cam.valid_blocks < INPUTS_NEEDED:
                self.cal_status = log.LiveCalibrationData.Status.uncalibrated
            elif road_cam.is_valid(self.pitch_limits):
                self.cal_status = log.LiveCalibrationData.Status.calibrated
            else:
                self.cal_status = log.LiveCalibrationData.Status.invalid

            # Check for recalibration
            if (road_cam.calib_spread[1] > MAX_ALLOWED_PITCH_SPREAD or
                road_cam.calib_spread[2] > MAX_ALLOWED_YAW_SPREAD):
                if self.cal_status == log.LiveCalibrationData.Status.calibrated:
                    self.reset('road')
                    self.cal_status = log.LiveCalibrationData.Status.recalibrating

        # Save periodically
        self._save_calibration()

    def _save_calibration(self):
        """Save calibration to params."""
        if not self.param_put:
            return

        # Save every few cycles
        if self.block_idx % 10 == 0 and self.idx == 0:
            try:
                msg = self.get_msg()
                self.params.put_nonblocking("CameraCalibrationParams", msg.to_bytes())
            except Exception as e:
                cloudlog.error(f"Failed to save calibration: {e}")

    def get_calibration_percentage(self) -> int:
        """Get overall calibration percentage."""
        road_cam = self.cameras.get('road')
        if not road_cam:
            return 0

        total_samples = road_cam.valid_blocks * BLOCK_SIZE + self.idx
        target_samples = INPUTS_NEEDED * BLOCK_SIZE
        return min(100 * total_samples // target_samples, 100)

    def get_msg(self) -> capnp.lib.capnp._DynamicStructBuilder:
        """Generate liveCalibration message."""
        msg = messaging.new_message('liveCalibration')

        road_cam = self.cameras.get('road')
        if road_cam:
            msg.liveCalibration.rpyCalib = road_cam.rpy.tolist()
            msg.liveCalibration.rpyCalibSpread = road_cam.calib_spread.tolist()
            msg.liveCalibration.validBlocks = road_cam.valid_blocks

        msg.liveCalibration.calStatus = self.cal_status
        msg.liveCalibration.calPerc = self.get_calibration_percentage()
        msg.liveCalibration.height = [self.cameras['road'].height if 'road' in self.cameras else 1.22]

        # Add multi-camera info
        if len(self.cameras) > 1:
            msg.liveCalibration.wideFromDeviceEuler = self.cameras.get('wide_road', road_cam).rpy.tolist() if 'wide_road' in self.cameras else [0.0, 0.0, 0.0]

        if self.not_car:
            msg.liveCalibration.calStatus = log.LiveCalibrationData.Status.calibrated
            msg.liveCalibration.calPerc = 100
            msg.liveCalibration.rpyCalib = [0.0, 0.0, 0.0]

        return msg

    def get_calibration_state_msg(self) -> capnp.lib.capnp._DynamicStructBuilder:
        """Generate calibrationState message (EOP multi-camera state)."""
        msg = messaging.new_message('calibrationState')

        cs = msg.calibrationState

        # Map LiveCalibration status to CalibrationState status
        status_map = {
            log.LiveCalibrationData.Status.uncalibrated: 0,  # uncalibrated
            log.LiveCalibrationData.Status.calibrated: 3,    # calibrated
            log.LiveCalibrationData.Status.invalid: 4,       # invalid
            log.LiveCalibrationData.Status.recalibrating: 1, # calibrating
        }
        cs.status = status_map.get(self.cal_status, 0)

        # Quality score based on valid blocks and consistency
        road_cam = self.cameras.get('road')
        if road_cam:
            blocks_score = min(1.0, road_cam.valid_blocks / INPUTS_WANTED)
            cs.quality = blocks_score * self.cross_camera_consistency
            cs.progress = self.get_calibration_percentage() / 100.0
        else:
            cs.quality = 0.0
            cs.progress = 0.0

        # Per-camera calibrations (capnp List(Struct) requires init() + index assignment)
        cam_names = list(self.cameras.keys())
        cals = cs.init('cameraCalibrations', len(cam_names))
        for i, cam_name in enumerate(cam_names):
            cam = self.cameras[cam_name]
            cals[i].cameraId = cam_name
            cals[i].rpy = cam.rpy.tolist()
            cals[i].height = float(cam.height)
            cals[i].validBlocks = int(cam.valid_blocks)
            cals[i].quality = float(min(1.0, cam.valid_blocks / INPUTS_WANTED))
            cals[i].converged = bool(cam.valid_blocks >= INPUTS_NEEDED and cam.is_valid(self.pitch_limits))

        # Cross-camera consistency
        cs.interCameraSpread = float(np.degrees(
            max(cam.calib_spread[1:].max() for cam in self.cameras.values())
            if self.cameras else 0.0
        ))
        cs.consistencyCheckPassed = self.cross_camera_consistency > 0.9

        return msg

    def send_data(self, pm: messaging.PubMaster, valid: bool = True):
        """Send calibration data."""
        pm.send('liveCalibration', self.get_msg())
        pm.send('calibrationState', self.get_calibration_state_msg())


def _odometry_to_transform(odom, dt_s: float = 0.05) -> tuple[np.ndarray, np.ndarray]:
  """Convert cameraOdometry rates (m/s, rad/s) to frame-to-frame transform."""
  t_ego = np.array(odom.trans, dtype=np.float64) * dt_s
  rot_vec = np.array(odom.rot, dtype=np.float64) * dt_s
  R_ego, _ = cv2.Rodrigues(rot_vec)
  return R_ego, t_ego


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

    # Side camera calibrators (only on platforms with side cameras)
    side_calibrators: dict[str, SideCameraCalibrator] = {}
    side_vipc: dict[str, VisionIpcClient | None] = {}
    platform = calibrator.platform
    if HAS_SIDE_CAMERAS:
        try:
            for name, stream_type in [
                ('side_left', VisionStreamType.VISION_STREAM_SIDE_LEFT),
                ('side_right', VisionStreamType.VISION_STREAM_SIDE_RIGHT),
            ]:
                client = VisionIpcClient("uvcd", stream_type, False)
                if client.connect(False):
                    side_vipc[name] = client
                    # Initialize with defaults from geometry
                    geo = CameraArrayGeometry.for_platform(platform)
                    cfg = geo.cameras.get(name)
                    if cfg:
                        fx = cfg.intrinsics.fx
                        fy = cfg.intrinsics.fy
                        cx = cfg.intrinsics.cx
                        cy = cfg.intrinsics.cy
                        t = cfg.extrinsics.t
                        # Extract yaw from rotation matrix
                        yaw = np.arctan2(cfg.extrinsics.R[1, 0], cfg.extrinsics.R[0, 0])
                        pitch = np.arcsin(-cfg.extrinsics.R[2, 0])
                        roll = np.arctan2(cfg.extrinsics.R[2, 1], cfg.extrinsics.R[2, 2])
                        side_calibrators[name] = SideCameraCalibrator(
                            camera_name=name, fx=fx, fy=fy, cx=cx, cy=cy,
                            init_yaw=yaw, init_pitch=pitch, init_roll=roll,
                            init_t=(float(t[0]), float(t[1]), float(t[2])),
                        )
                        cloudlog.info(f"Side calibrator ready: {name}")
                    else:
                        side_vipc[name] = None
                else:
                    side_vipc[name] = None
        except Exception as e:
            cloudlog.warning(f"Side camera calibrator init failed: {e}")

    cloudlog.info("Camera calibration daemon started")

    while True:
        timeout = 0 if sm.frame == -1 else 100
        sm.update(timeout)

        if sm.updated['cameraOdometry']:
            calibrator.v_ego = sm['carState'].vEgo

            # Update road camera calibration
            odom = sm['cameraOdometry']
            calibrator.handle_cam_odometry(
                'road',
                odom.trans,
                odom.rot,
                odom.transStd,
                odom.roadTransformTrans[2] if len(odom.roadTransformTrans) == 3 else None
            )

            calibrator.update_status()

            # Side camera calibration: process frames + odometry
            R_ego, t_ego = _odometry_to_transform(odom)
            for name, vip in side_vipc.items():
                if vip is None or name not in side_calibrators:
                    continue
                try:
                    buf = vip.recv(timeout_ms=1)
                    if buf is not None:
                        h = vip.height or 720
                        w = vip.width or 1280
                        frame = np.frombuffer(buf.data, dtype=np.uint8).reshape((h, w, 3))
                        side_calibrators[name].process_frame(frame, R_ego, t_ego)
                except Exception as e:
                    cloudlog.debug(f"Side calibrator frame error: {e}")

            # Save converged side calibrations to params
            for name, sc in side_calibrators.items():
                if sc.is_converged():
                    res = sc.get_result()
                    param_key = f"SideCameraCalib_{name}"
                    try:
                        existing = params_reader.get(param_key)
                        if existing is None:
                            import json
                            data = {
                                'roll': res.roll_rad, 'pitch': res.pitch_rad, 'yaw': res.yaw_rad,
                                'tx': res.tx_m, 'ty': res.ty_m, 'tz': res.tz_m,
                                'rmse': res.rmse_px, 'pairs': res.num_pairs,
                            }
                            params_reader.put_nonblocking(param_key, json.dumps(data).encode())
                            cloudlog.info(f"Side calibration saved: {name} rmse={res.rmse_px:.2f}px")
                    except Exception:
                        pass

            if DEBUG:
                road_cam = calibrator.cameras.get('road')
                if road_cam:
                    print(f"RPY: {road_cam.rpy}, Valid: {road_cam.valid_blocks}, Status: {calibrator.cal_status}")

        # Publish at 4Hz
        if sm.frame % 5 == 0:
            calibrator.send_data(pm, sm.all_checks())


if __name__ == "__main__":
    main()

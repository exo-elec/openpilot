#!/usr/bin/env python3
"""
Calibration Monitor - Integration with stated

Monitors calibration quality and publishes state for stated integration.
This is a lightweight module that can be used by:
- camera_calibrationd (continuous monitoring)
- stated (state machine integration)
- UI (quality display)

Publishes:
  - calibrationState: Quality metrics, convergence status
"""

from __future__ import annotations

import numpy as np
from dataclasses import dataclass, field
from enum import Enum

from cereal import log
from openpilot.common.swaglog import cloudlog


class CalibrationQuality(Enum):
    """Calibration quality levels."""
    UNKNOWN = 0
    POOR = 1      # High spread, needs recalibration
    FAIR = 2      # Acceptable but not optimal
    GOOD = 3      # Well calibrated
    EXCELLENT = 4 # Fully converged


class CalibrationStatus(Enum):
    """Calibration state machine status."""
    UNCALIBRATED = 0
    CALIBRATING = 1      # Collecting data
    CONVERGING = 2       # Initial convergence
    CALIBRATED = 3       # Good calibration
    DEGRADED = 4         # Was good, now poor
    RECALIBRATE = 5      # Needs manual recalibration


@dataclass
class CameraCalibState:
    """Calibration state for a single camera."""
    camera_id: str

    # Current calibration
    rpy: np.ndarray = field(default_factory=lambda: np.array([0.0, 0.0, 0.0]))
    height: float = 1.22

    # Quality metrics
    valid_blocks: int = 0
    rpy_spread: np.ndarray = field(default_factory=lambda: np.array([0.0, 0.0, 0.0]))

    # Convergence tracking
    pitch_std: float = 0.0
    yaw_std: float = 0.0

    # Status
    quality: CalibrationQuality = CalibrationQuality.UNKNOWN

    def update_quality(self):
        """Update quality based on spread."""
        pitch_spread = np.degrees(self.rpy_spread[1])
        yaw_spread = np.degrees(self.rpy_spread[2])

        if self.valid_blocks < 5:
            self.quality = CalibrationQuality.UNKNOWN
        elif pitch_spread > 4.0 or yaw_spread > 2.0:
            self.quality = CalibrationQuality.POOR
        elif pitch_spread > 2.0 or yaw_spread > 1.0:
            self.quality = CalibrationQuality.FAIR
        elif pitch_spread > 1.0 or yaw_spread > 0.5:
            self.quality = CalibrationQuality.GOOD
        else:
            self.quality = CalibrationQuality.EXCELLENT


class CalibrationMonitor:
    """Monitors calibration quality across all cameras.

    Can be used by:
    - camera_calibrationd: For continuous monitoring
    - stated: For state machine integration
    - UI: For quality display
    """

    # Quality thresholds
    MAX_PITCH_SPREAD_DEG = 4.0
    MAX_YAW_SPREAD_DEG = 2.0
    MAX_INTER_CAMERA_SPREAD_DEG = 1.5

    # Convergence thresholds
    MIN_VALID_BLOCKS = 10
    EXCELLENT_BLOCKS = 50

    def __init__(self, camera_ids: list[str] = None):
        """Initialize monitor.

        Args:
            camera_ids: List of camera IDs to monitor (None = auto-detect)
        """
        self.camera_ids = camera_ids or ['road', 'wide_road']
        self.cameras: dict[str, CameraCalibState] = {
            cid: CameraCalibState(camera_id=cid)
            for cid in self.camera_ids
        }

        self.status = CalibrationStatus.UNCALIBRATED
        self.overall_quality = CalibrationQuality.UNKNOWN

        # Cross-camera consistency
        self.max_inter_camera_spread = 0.0
        self.consistent_cameras = False

        # History for trend analysis
        self.quality_history: list[CalibrationQuality] = []
        self.max_history = 100

        cloudlog.info(f"CalibrationMonitor: Initialized for {len(self.camera_ids)} cameras")

    def update_camera(self, camera_id: str, rpy: np.ndarray,
                      rpy_spread: np.ndarray, valid_blocks: int,
                      height: float = 1.22) -> CalibrationQuality:
        """Update calibration state for a camera.

        Args:
            camera_id: Camera identifier
            rpy: Current roll-pitch-yaw
            rpy_spread: Spread of calibration values
            valid_blocks: Number of valid calibration blocks
            height: Camera height

        Returns:
            Updated quality level
        """
        if camera_id not in self.cameras:
            cloudlog.warning(f"CalibrationMonitor: Unknown camera {camera_id}")
            return CalibrationQuality.UNKNOWN

        cam = self.cameras[camera_id]
        cam.rpy = rpy.copy()
        cam.rpy_spread = rpy_spread.copy()
        cam.valid_blocks = valid_blocks
        cam.height = height

        # Calculate std from spread
        cam.pitch_std = rpy_spread[1] / np.sqrt(max(1, valid_blocks))
        cam.yaw_std = rpy_spread[2] / np.sqrt(max(1, valid_blocks))

        # Update quality
        cam.update_quality()

        # Update overall state
        self._update_overall_state()

        return cam.quality

    def _update_overall_state(self):
        """Update overall calibration status."""
        # Check cross-camera consistency
        self._check_consistency()

        # Calculate overall quality (worst camera)
        qualities = [cam.quality for cam in self.cameras.values()]
        self.overall_quality = min(qualities, key=lambda q: q.value)

        # Update status
        total_blocks = sum(cam.valid_blocks for cam in self.cameras.values())
        avg_blocks = total_blocks / len(self.cameras)

        # State machine
        if avg_blocks < 5:
            self.status = CalibrationStatus.UNCALIBRATED
        elif self.overall_quality in (CalibrationQuality.POOR,):
            if self.status in (CalibrationStatus.CALIBRATED, CalibrationStatus.CONVERGING):
                self.status = CalibrationStatus.DEGRADED
            else:
                self.status = CalibrationStatus.CALIBRATING
        elif avg_blocks < self.MIN_VALID_BLOCKS:
            self.status = CalibrationStatus.CALIBRATING
        elif self.overall_quality == CalibrationQuality.EXCELLENT and self.consistent_cameras:
            self.status = CalibrationStatus.CALIBRATED
        elif self.overall_quality in (CalibrationQuality.GOOD, CalibrationQuality.EXCELLENT):
            self.status = CalibrationStatus.CONVERGING
        else:
            self.status = CalibrationStatus.CALIBRATING

        # Track history
        self.quality_history.append(self.overall_quality)
        if len(self.quality_history) > self.max_history:
            self.quality_history.pop(0)

    def _check_consistency(self):
        """Check cross-camera calibration consistency."""
        if len(self.cameras) < 2:
            self.consistent_cameras = True
            return

        # Compare all camera pairs
        max_spread = 0.0
        cam_list = list(self.cameras.values())

        for i, cam1 in enumerate(cam_list):
            for cam2 in cam_list[i+1:]:
                rpy_diff = np.abs(cam1.rpy - cam2.rpy)
                spread = np.max(np.degrees(rpy_diff[1:]))  # pitch, yaw only
                max_spread = max(max_spread, spread)

        self.max_inter_camera_spread = max_spread
        self.consistent_cameras = max_spread < self.MAX_INTER_CAMERA_SPREAD_DEG

    def get_state_message(self) -> log.CalibrationState:
        """Generate calibrationState message for publishing."""
        msg = log.CalibrationState.new_message()

        # Overall status
        msg.status = self.status.value
        msg.quality = self.overall_quality.value

        # Per-camera states
        for cam_id, cam in self.cameras.items():
            cam_msg = msg.cameras.add()
            cam_msg.cameraId = cam_id
            cam_msg.rpy = cam.rpy.tolist()
            cam_msg.rpySpread = cam.rpy_spread.tolist()
            cam_msg.validBlocks = cam.valid_blocks
            cam_msg.quality = cam.quality.value
            cam_msg.pitchStd = cam.pitch_std
            cam_msg.yawStd = cam.yaw_std

        # Consistency
        msg.interCameraSpread = self.max_inter_camera_spread
        msg.consistentCameras = self.consistent_cameras

        # Convergence progress
        total_blocks = sum(cam.valid_blocks for cam in self.cameras.values())
        msg.convergenceProgress = min(1.0, total_blocks / (self.EXCELLENT_BLOCKS * len(self.cameras)))

        return msg

    def should_recalibrate(self) -> bool:
        """Check if recalibration is recommended."""
        return self.status in (CalibrationStatus.DEGRADED, CalibrationStatus.RECALIBRATE)

    def is_calibrated(self) -> bool:
        """Check if system is sufficiently calibrated."""
        return self.status in (CalibrationStatus.CALIBRATED, CalibrationStatus.CONVERGING)

    def get_recommendation(self) -> str:
        """Get human-readable recommendation."""
        if self.status == CalibrationStatus.UNCALIBRATED:
            return "Calibration starting - drive above 15 MPH on straight roads"
        elif self.status == CalibrationStatus.CALIBRATING:
            return f"Calibrating... ({self._get_progress()}%)"
        elif self.status == CalibrationStatus.CONVERGING:
            return "Calibration converging - continue driving"
        elif self.status == CalibrationStatus.CALIBRATED:
            return "Calibration complete ✓"
        elif self.status == CalibrationStatus.DEGRADED:
            return "Calibration degraded - check camera mounting"
        elif self.status == CalibrationStatus.RECALIBRATE:
            return "Recalibration required - reset in settings"
        return "Unknown status"

    def _get_progress(self) -> int:
        """Get calibration progress percentage."""
        total_blocks = sum(cam.valid_blocks for cam in self.cameras.values())
        target = self.MIN_VALID_BLOCKS * len(self.cameras)
        return int(min(100, (total_blocks / target) * 100))

    def reset(self):
        """Reset calibration monitoring."""
        for cam in self.cameras.values():
            cam.valid_blocks = 0
            cam.rpy_spread = np.zeros(3)
            cam.quality = CalibrationQuality.UNKNOWN

        self.status = CalibrationStatus.UNCALIBRATED
        self.quality_history.clear()
        cloudlog.info("CalibrationMonitor: Reset")


# Convenience function for stated integration
def get_calibration_status_for_state() -> CalibrationStatus | None:
    """Get calibration status for stated state machine.

    This function can be called by stated to check if calibration
    is complete before allowing engagement.

    Returns:
        CalibrationStatus or None if monitor not available
    """
    # This would typically read from params or subscribe to calibrationState
    # For now, return None - stated should subscribe to calibrationState
    return None

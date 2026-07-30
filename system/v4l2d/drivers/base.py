#!/usr/bin/env python3
"""Base camera driver — V4L2 ioctl wrapper.

Provides common V4L2 operations for all camera drivers.
Platform-agnostic: works with any V4L2-compliant device.
"""

from __future__ import annotations

import fcntl
import os
import struct
import time
from dataclasses import dataclass
from collections.abc import Generator

import cv2
import numpy as np

from openpilot.common.swaglog import cloudlog


# V4L2 ioctl codes — _IOWR('V', nr, struct v4l2_control)
# struct v4l2_control = { __u32 id; __s32 value; } = 8 bytes
# VIDIOC_S_CTRL = _IOWR('V', 28, ...) → 0xC008561C
# VIDIOC_G_CTRL = _IOWR('V', 27, ...) → 0xC008561B
VIDIOC_S_CTRL = 0xc008561c
VIDIOC_G_CTRL = 0xc008561b
V4L2_CID_PRIVATE_BASE = 0x08000000

# V4L2_CTRL_CLASS_USER controls reachable via VIDIOC_S_CTRL
V4L2_CID_EXPOSURE     = 0x00980911  # __u32 exposure (sensor lines)
V4L2_CID_GAIN         = 0x00980913  # __u32 analog gain

# Pixel formats
V4L2_PIX_FMT_NV12 = 0x3231564e
V4L2_PIX_FMT_YUYV = 0x56595559

# struct v4l2_control wire format: u32 id + s32 value = 8 bytes
_CTRL_FMT = "Ii"
_CTRL_SIZE = struct.calcsize(_CTRL_FMT)  # 8


@dataclass
class CameraFrame:
    """Captured frame metadata + data."""

    data: np.ndarray
    timestamp_ns: int
    exposure_time_us: float
    analog_gain: float
    frame_id: int
    width: int = 0
    height: int = 0
    pixel_format: str = "nv12"

    @property
    def light_level(self) -> float:
        """Estimate scene brightness [0.0, 1.0]."""
        if self.data.size == 0:
            return 0.5
        gray = self.data.mean()
        return float(gray) / 255.0


class BaseCameraDriver:
    """Base class for V4L2 camera drivers.

    Subclasses implement sensor-specific features (HDR, sync, etc.).
    This base handles common V4L2 capture, format conversion, and
    frame metadata extraction.
    """

    SENSOR_NAME: str = "unknown"
    DEFAULT_WIDTH: int = 1920
    DEFAULT_HEIGHT: int = 1080
    DEFAULT_FPS: int = 30

    def __init__(self, device_path: str, width: int = 0, height: int = 0,
                 fps: int = 0, pixel_format: int = V4L2_PIX_FMT_NV12,
                 fourcc: str | None = None):
        self.device_path = device_path
        self.width = width or self.DEFAULT_WIDTH
        self.height = height or self.DEFAULT_HEIGHT
        self.fps = fps or self.DEFAULT_FPS
        self.pixel_format = pixel_format
        self.fourcc = fourcc

        self._cap: cv2.VideoCapture | None = None
        self._v4l2_fd: int | None = None
        self._is_open = False
        self._frame_id = 0

    # ------------------------------------------------------------------
    # V4L2 ioctl helpers
    # ------------------------------------------------------------------

    def _ensure_v4l2_fd(self) -> int:
        """Open device for ioctl if not already open."""
        if self._v4l2_fd is None:
            self._v4l2_fd = os.open(self.device_path, os.O_RDWR)
        return self._v4l2_fd

    def _v4l2_set_ctrl(self, ctrl_id: int, value: int) -> bool:
        """Set V4L2 control via VIDIOC_S_CTRL ioctl.

        struct v4l2_control = { __u32 id; __s32 value; } = 8 bytes.
        fcntl.ioctl needs a mutable bytearray for read+write ioctls.
        """
        try:
            fd = self._ensure_v4l2_fd()
            buf = bytearray(struct.pack(_CTRL_FMT, ctrl_id, value))
            fcntl.ioctl(fd, VIDIOC_S_CTRL, buf, True)
            return True
        except (OSError, IOError) as e:
            cloudlog.debug(f"V4L2 set_ctrl 0x{ctrl_id:08x}={value} failed: {e}")
            return False

    def _v4l2_get_ctrl(self, ctrl_id: int) -> int | None:
        """Get V4L2 control via VIDIOC_G_CTRL ioctl."""
        try:
            fd = self._ensure_v4l2_fd()
            buf = bytearray(struct.pack(_CTRL_FMT, ctrl_id, 0))
            fcntl.ioctl(fd, VIDIOC_G_CTRL, buf, True)
            _, value = struct.unpack(_CTRL_FMT, buf)
            return value
        except (OSError, IOError) as e:
            cloudlog.debug(f"V4L2 get_ctrl 0x{ctrl_id:08x} failed: {e}")
            return None

    def _v4l2_set_ctrl_subprocess(self, ctrl_name: str, value: int) -> bool:
        """Fallback: set control via v4l2-ctl subprocess."""
        try:
            import subprocess
            cmd = [
                "v4l2-ctl",
                "-d", self.device_path,
                "--set-ctrl", f"{ctrl_name}={value}",
            ]
            result = subprocess.run(cmd, capture_output=True, text=True, timeout=5.0)
            return result.returncode == 0
        except Exception as e:
            cloudlog.debug(f"v4l2-ctl fallback failed: {e}")
            return False

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    def open(self) -> bool:
        """Open V4L2 device and configure capture."""
        self._cap = cv2.VideoCapture(self.device_path, cv2.CAP_V4L2)
        if not self._cap.isOpened():
            cloudlog.error(f"Failed to open {self.device_path}")
            return False

        if self.fourcc:
            self._cap.set(cv2.CAP_PROP_FOURCC, cv2.VideoWriter_fourcc(*self.fourcc))
        self._cap.set(cv2.CAP_PROP_FRAME_WIDTH, self.width)
        self._cap.set(cv2.CAP_PROP_FRAME_HEIGHT, self.height)
        self._cap.set(cv2.CAP_PROP_FPS, self.fps)

        # Update to actual negotiated values
        self.width = int(self._cap.get(cv2.CAP_PROP_FRAME_WIDTH))
        self.height = int(self._cap.get(cv2.CAP_PROP_FRAME_HEIGHT))
        actual_fps = self._cap.get(cv2.CAP_PROP_FPS)
        cloudlog.info(
            f"{self.SENSOR_NAME} opened: {self.width}x{self.height} @ {actual_fps:.1f}fps "
            f"({self.device_path})"
        )

        self._is_open = True
        return True

    def close(self) -> None:
        """Close V4L2 device and release resources."""
        if self._cap is not None:
            self._cap.release()
            self._cap = None
        if self._v4l2_fd is not None:
            try:
                os.close(self._v4l2_fd)
            except OSError:
                pass
            self._v4l2_fd = None
        self._is_open = False

    # ------------------------------------------------------------------
    # Capture
    # ------------------------------------------------------------------

    def read_frames(self) -> Generator[CameraFrame, None, None]:
        """Generator yielding captured frames."""
        if not self._is_open:
            raise RuntimeError("Camera not open")

        while True:
            frame = self._capture_frame()
            if frame is not None:
                yield frame

    def _capture_frame(self) -> CameraFrame | None:
        """Capture single frame from V4L2."""
        if self._cap is None:
            return None

        ret, frame = self._cap.read()
        if not ret:
            return None

        self._frame_id += 1
        ts = int(time.monotonic() * 1e9)

        # Handle NV12 format (common for Rockchip ISP)
        if len(frame.shape) == 2:
            h, w = frame.shape
            expected_h = int(self.height * 1.5)
            if h >= expected_h:
                # Contains UV data — already NV12
                pass
            else:
                # Raw grayscale
                frame = cv2.cvtColor(frame, cv2.COLOR_GRAY2BGR)

        return CameraFrame(
            data=frame,
            timestamp_ns=ts,
            exposure_time_us=self._get_exposure_us(),
            analog_gain=self._get_gain(),
            frame_id=self._frame_id,
            width=self.width,
            height=self.height,
        )

    # ------------------------------------------------------------------
    # Exposure / Gain (overridable by subclasses)
    # ------------------------------------------------------------------

    def set_exposure_us(self, exposure_us: float) -> bool:
        """Set exposure time in microseconds."""
        if self._cap is None:
            return False
        # OpenCV exposure is in units of 10us for some backends
        return self._cap.set(cv2.CAP_PROP_EXPOSURE, exposure_us / 10000.0)

    def set_gain(self, gain_db: float) -> bool:
        """Set analog gain in dB."""
        if self._cap is None:
            return False
        return self._cap.set(cv2.CAP_PROP_GAIN, gain_db)

    def _get_exposure_us(self) -> float:
        """Get current exposure in microseconds."""
        if self._cap is None:
            return 0.0
        return self._cap.get(cv2.CAP_PROP_EXPOSURE) * 10000.0

    def _get_gain(self) -> float:
        """Get current analog gain."""
        if self._cap is None:
            return 0.0
        return self._cap.get(cv2.CAP_PROP_GAIN)

    # ------------------------------------------------------------------
    # Properties
    # ------------------------------------------------------------------

    @property
    def is_open(self) -> bool:
        return self._is_open

    @property
    def frame_id(self) -> int:
        return self._frame_id

    @property
    def W(self) -> int:
        """Alias for width (compatibility with camera HAL consumers)."""
        return self.width

    @property
    def H(self) -> int:
        """Alias for height (compatibility with camera HAL consumers)."""
        return self.height

    def get_info(self) -> dict:
        """Return driver info dictionary."""
        return {
            "sensor": self.SENSOR_NAME,
            "device": self.device_path,
            "resolution": f"{self.width}x{self.height}",
            "fps": self.fps,
            "open": self._is_open,
        }

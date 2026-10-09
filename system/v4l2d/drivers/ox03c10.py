#!/usr/bin/env python3
"""OX03C10 camera driver — on-chip HDR4 for road/wide/tele mono cameras.

The OX03C10 performs HDR combination ON-CHIP (at sensor level), not at the ISP.
This driver configures the sensor's HDR mode via V4L2 private controls.
The ISP (RKIAQ/RKISP) should run in NORMAL (linear) mode — it receives
already-combined HDR output.

HDR modes (sensor on-chip):
- LINEAR (0): ~60dB — uniform lighting only
- HDR2   (1): ~80dB — moderate contrast
- HDR3   (2): ~120dB — high contrast
- HDR4   (3): ~140dB — default for ADAS (tunnel exit, night/headlights)

IMPORTANT — common mistake:
  Do NOT use RK_AIQ_WORKING_MODE_ISP_HDR2/3 for OX03C10.
  The sensor already outputs merged HDR. ISP HDR modes are for sensors
  that stream separate exposures (e.g. IMX415). Using ISP HDR on OX03C10
  causes washed-out / double-exposed images.

Hardware target: ExoPilot 01M (RK3588).  CSI wiring and V4L2 private
control IDs are imported from the closed HAL package.
"""

from __future__ import annotations

import subprocess
from enum import IntEnum

from openpilot.system.v4l2d.drivers.base import BaseCameraDriver
from openpilot.common.swaglog import cloudlog

try:
  from hal.drivers.camera.sensor_registers import OX03C10 as _OX03C10
except ImportError:
  _OX03C10 = {}  # type: ignore[assignment]


# OX03C10 V4L2 private controls are imported from the closed HAL package.
V4L2_CID_OX03C10_HDR_MODE = _OX03C10.get("V4L2_CID_HDR_MODE", 0)
V4L2_CID_OX03C10_LFM = _OX03C10.get("V4L2_CID_LFM", 0)


class OX03C10HDRMode(IntEnum):
    """OX03C10 on-chip HDR modes — sensor-level, ISP always in linear mode.

    Values are populated from the closed HAL package.  When the HAL is not
    installed they default to 0, so the module imports cleanly for PC testing
    but cannot configure a real sensor.
    """
    LINEAR = _OX03C10.get("HDR_MODE_LINEAR", 0)
    HDR2 = _OX03C10.get("HDR_MODE_HDR2", 0)
    HDR3 = _OX03C10.get("HDR_MODE_HDR3", 0)
    HDR4 = _OX03C10.get("HDR_MODE_HDR4", 0)


class OX03C10Driver(BaseCameraDriver):
    """OX03C10 HDR camera driver for road/wide/tele mono cameras.

    Features:
    - 1920×1280 @ 20fps (2-lane MIPI limit with HDR4 PWL output)
    - On-chip HDR4 (140dB) — 4-exposure merge, ISP receives linear output
    - LED Flicker Mitigation (LFM) for traffic lights

    Architecture:
        Scene → OX03C10 [HDR4 on-chip merge] → PWL16 → RKISP [linear mode] → NV12

    Register map, private controls and default resolution are imported from the
    closed HAL package.
    """

    SENSOR_NAME = _OX03C10.get("SENSOR_NAME", "ox03c10")
    DEFAULT_WIDTH = _OX03C10.get("DEFAULT_WIDTH", 1920)
    DEFAULT_HEIGHT = _OX03C10.get("DEFAULT_HEIGHT", 1280)
    DEFAULT_FPS = _OX03C10.get("DEFAULT_FPS", 20)

    def __init__(self, device_path: str, width: int = 0, height: int = 0,
                 fps: int = 0, hdr_mode: OX03C10HDRMode = OX03C10HDRMode.HDR4,
                 lfm_enabled: bool = True):
        """Initialize OX03C10 driver.

        Args:
            device_path: V4L2 device path (e.g., /dev/video0)
            width: Capture width (default 1920)
            height: Capture height (default 1280)
            fps: Target framerate (default 30, max 60)
            hdr_mode: On-chip HDR mode (default HDR3 for 120dB)
            lfm_enabled: LED Flicker Mitigation (default True)
        """
        super().__init__(device_path, width, height, fps)
        self._hdr_mode = hdr_mode
        self._lfm_enabled = lfm_enabled
        self._hdr_applied = False

    def open(self) -> bool:
        """Open V4L2 device and configure HDR mode."""
        success = super().open()
        if not success:
            return False

        # Set HDR mode via V4L2 control
        if self._set_hdr_mode(self._hdr_mode):
            self._hdr_applied = True
            cloudlog.info(
                f"OX03C10 ({self.device_path}): HDR mode = {self._hdr_mode.name} " +
                f"({self._get_dynamic_range_db()}dB), ISP = linear"
            )
        else:
            cloudlog.warning(
                f"OX03C10 ({self.device_path}): HDR mode set failed — " +
                "continuing in sensor default mode"
            )

        # Set LED Flicker Mitigation
        if self._lfm_enabled:
            self._set_lfm(True)

        return True

    # ------------------------------------------------------------------
    # HDR Mode Control
    # ------------------------------------------------------------------

    def _set_hdr_mode(self, mode: OX03C10HDRMode) -> bool:
        """Set OX03C10 HDR mode via V4L2 private control.

        This configures the SENSOR's on-chip HDR, NOT the ISP.
        ISP should remain in NORMAL (linear) mode.

        Args:
            mode: HDR mode (LINEAR, HDR2, HDR3, HDR4)

        Returns:
            True if successful
        """
        # Try V4L2 ioctl first
        if self._v4l2_set_ctrl(V4L2_CID_OX03C10_HDR_MODE, mode.value):
            self._hdr_mode = mode
            return True

        # Fallback: try v4l2-ctl subprocess
        try:
            cmd = [
                "v4l2-ctl",
                "-d", self.device_path,
                "--set-ctrl", f"hdr_mode={mode.value}",
            ]
            result = subprocess.run(cmd, capture_output=True, text=True, timeout=5.0)
            if result.returncode == 0:
                self._hdr_mode = mode
                return True
        except Exception as e:
            cloudlog.debug(f"OX03C10 v4l2-ctl fallback failed: {e}")

        return False

    def set_hdr_mode(self, mode: OX03C10HDRMode) -> bool:
        """Set HDR mode (requires stream restart).

        Args:
            mode: New HDR mode

        Returns:
            True if successful
        """
        if mode == self._hdr_mode:
            return True

        # Need to restart stream for HDR mode change
        was_open = self._is_open
        if was_open:
            self.close()

        self._hdr_mode = mode

        if was_open:
            return self.open()
        return True

    def get_hdr_mode(self) -> OX03C10HDRMode:
        """Get current HDR mode."""
        return self._hdr_mode

    def _get_dynamic_range_db(self) -> str:
        """Get dynamic range string for current HDR mode."""
        dr = _OX03C10.get("DYNAMIC_RANGE_DB", {})
        return dr.get(self._hdr_mode, "Unknown")

    # ------------------------------------------------------------------
    # LED Flicker Mitigation
    # ------------------------------------------------------------------

    def _set_lfm(self, enabled: bool) -> bool:
        """Enable/disable LED Flicker Mitigation.

        LFM reduces flicker from LED traffic lights, signs, and taillights
        by adjusting exposure timing.

        Args:
            enabled: True to enable LFM

        Returns:
            True if successful
        """
        value = 1 if enabled else 0
        if self._v4l2_set_ctrl(V4L2_CID_OX03C10_LFM, value):
            cloudlog.info(f"OX03C10 LFM: {'enabled' if enabled else 'disabled'}")
            return True
        return False

    # ------------------------------------------------------------------
    # Auto HDR Selection
    # ------------------------------------------------------------------

    @staticmethod
    def select_hdr_mode(max_lux: float, min_lux: float) -> OX03C10HDRMode:
        """Auto-select HDR mode based on scene lighting conditions.

        Args:
            max_lux: Maximum scene luminance (lux)
            min_lux: Minimum scene luminance (lux)

        Returns:
            Recommended HDR mode
        """
        contrast_ratio = max_lux / max(min_lux, 1.0)

        if contrast_ratio > 10000:  # Extreme: tunnel exit, night + oncoming headlights
            return OX03C10HDRMode.HDR4
        elif contrast_ratio > 1000:  # High: daytime shadows, bright sky
            return OX03C10HDRMode.HDR3
        elif contrast_ratio > 100:   # Moderate
            return OX03C10HDRMode.HDR2
        else:  # Low contrast: overcast, uniform lighting
            return OX03C10HDRMode.LINEAR

    # ------------------------------------------------------------------
    # Overrides
    # ------------------------------------------------------------------

    def get_info(self) -> dict:
        """Return driver info."""
        info = super().get_info()
        info.update({
            "hdr_mode": self._hdr_mode.name,
            "dynamic_range_db": self._get_dynamic_range_db(),
            "lfm_enabled": self._lfm_enabled,
            "hdr_applied": self._hdr_applied,
            "architecture": "On-chip HDR → ISP linear",
            "note": "HDR performed on-chip by OX03C10. ISP runs NORMAL mode.",
        })
        return info

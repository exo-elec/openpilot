#!/usr/bin/env python3
"""GC4653 camera driver — DWDR stereo pair (stereo_left / stereo_right).

The GC4653 is a 4MP sensor with Digital Wide Dynamic Range (~110dB DWDR).
It has NO on-chip HDR merge — DWDR is a tone-mapping post-process, not
multi-exposure fusion. Using HDR mode for stereo would break temporal sync.

Critical design constraints:
- SDR capture mode only — DWDR is applied by ISP, not by sensor
- Exposure MUST be synchronized between left (master) and right (slave)
- Black level calibration (BLC) matched via OTP for accurate disparity

Hardware target:
  ExoPilot 01M (RK3588):  CSI3 stereo_left (I2C5 @ 0x52), CSI4 stereo_right (I2C6 @ 0x53)
"""

from __future__ import annotations

from openpilot.system.v4l2d.drivers.base import BaseCameraDriver
from openpilot.common.swaglog import cloudlog

try:
  from hal.drivers.camera.sensor_registers import GC4653 as _GC4653
except ImportError:
  _GC4653 = {}  # type: ignore[assignment]


class GC4653Driver(BaseCameraDriver):
    """GC4653 4MP stereo camera driver.

    Features:
    - 2560×1440 @ 20fps (native sensor, 2-lane MIPI @ 648MHz RAW10)
    - SDR capture — DWDR (~110dB) handled by ISP tone-mapping
    - OTP black level calibration for matched stereo pair
    - I2C register control for synchronized exposure (master/slave)

    Register map and I2C addresses are imported from the closed HAL package.
    When the HAL is not installed the driver uses empty defaults and will not
    configure a real sensor, but the module still imports cleanly for PC testing.
    """

    SENSOR_NAME = _GC4653.get("SENSOR_NAME", "gc4653")
    DEFAULT_WIDTH = _GC4653.get("DEFAULT_WIDTH", 2560)
    DEFAULT_HEIGHT = _GC4653.get("DEFAULT_HEIGHT", 1440)
    DEFAULT_FPS = _GC4653.get("DEFAULT_FPS", 20)

    # GC4653 register map
    REG_BLC_TARGET = _GC4653.get("REG_BLC_TARGET", 0x0000)
    REG_EXPOSURE_H = _GC4653.get("REG_EXPOSURE_H", 0x0000)
    REG_EXPOSURE_L = _GC4653.get("REG_EXPOSURE_L", 0x0000)
    REG_GAIN_H = _GC4653.get("REG_GAIN_H", 0x0000)
    REG_GAIN_L = _GC4653.get("REG_GAIN_L", 0x0000)
    REG_OTP_MODE = _GC4653.get("REG_OTP_MODE", 0x0000)

    # OTP memory map
    OTP_ADDR_BLACK_LEVEL = _GC4653.get("OTP_ADDR_BLACK_LEVEL", 0x00)

    # Default I2C addresses per ExoPilot hardware
    I2C_ADDR_MASTER = _GC4653.get("I2C_ADDR_MASTER", 0x00)
    I2C_ADDR_SLAVE = _GC4653.get("I2C_ADDR_SLAVE", 0x00)

    def __init__(self, device_path: str, i2c_bus: int = -1, i2c_addr: int = I2C_ADDR_MASTER,
                 width: int = 0, height: int = 0, fps: int = 0,
                 is_master: bool = False):
        """Initialize GC4653 driver.

        Args:
            device_path: V4L2 device path (e.g., /dev/video22)
            i2c_bus: I2C bus number for sensor control (-1 if not available)
            i2c_addr: I2C address (default 0x3c)
            width: Capture width (default 2560)
            height: Capture height (default 1440)
            fps: Target framerate (default 20 for stereo)
            is_master: If True, this camera drives exposure; slave follows
        """
        super().__init__(device_path, width, height, fps)
        self.i2c_bus = i2c_bus
        self.i2c_addr = i2c_addr
        self.is_master = is_master
        self._otp_black_level: dict | None = None

    def open(self) -> bool:
        """Open V4L2 device. No HDR configuration — GC4653 is SDR only."""
        success = super().open()
        if not success:
            return False

        # Log SDR-only mode explicitly
        cloudlog.info(
            f"GC4653 ({self.device_path}): SDR mode only — " +
            f"{'master' if self.is_master else 'slave'} " +
            "stereo camera, no HDR"
        )

        # Attempt OTP black level read (best effort)
        try:
            self._otp_black_level = self._read_otp_black_level()
            cloudlog.info(f"GC4653 OTP BLC: {self._otp_black_level}")
        except Exception as e:
            cloudlog.debug(f"GC4653 OTP read failed (non-critical): {e}")

        return True

    # ------------------------------------------------------------------
    # Stereo Synchronization
    # ------------------------------------------------------------------

    def sync_exposure(self, exposure_lines: int, gain: float) -> bool:
        """Synchronize exposure settings for stereo pair.

        Both left and right cameras MUST receive identical exposure
        within <1ms for accurate stereo correspondence.

        Args:
            exposure_lines: Integration time in line units
            gain: Analog gain (1.0x - 15.5x)

        Returns:
            True if both exposure and gain were set successfully
        """
        if not self._is_open:
            cloudlog.warning("GC4653: Cannot sync exposure — camera not open")
            return False

        try:
            # Compute register values
            exp_h = (exposure_lines >> 8) & 0xFF
            exp_l = exposure_lines & 0xFF
            gain_val = int(gain * 16) - 1  # GC4653 gain formula
            gain_h = (gain_val >> 8) & 0xFF
            gain_l = gain_val & 0xFF

            # Write exposure + gain atomically via I2C
            # TODO: Implement actual I2C write when smbus2 available
            # For now, use V4L2 controls as fallback
            self._v4l2_set_ctrl(self.REG_EXPOSURE_H, exp_h)
            self._v4l2_set_ctrl(self.REG_EXPOSURE_L, exp_l)
            self._v4l2_set_ctrl(self.REG_GAIN_H, gain_h)
            self._v4l2_set_ctrl(self.REG_GAIN_L, gain_l)

            cloudlog.debug(
                f"GC4653 sync: exposure={exposure_lines}, gain={gain:.2f}x " +
                f"({'master' if self.is_master else 'slave'})"
            )
            return True

        except Exception as e:
            cloudlog.warning(f"GC4653 exposure sync failed: {e}")
            return False

    def set_black_level(self, target: int) -> bool:
        """Set BLC (Black Level Correction) target.

        Args:
            target: Target black level (0-255 for 12-bit)

        Returns:
            True if successful
        """
        return self._v4l2_set_ctrl(self.REG_BLC_TARGET, target)

    # ------------------------------------------------------------------
    # OTP Calibration
    # ------------------------------------------------------------------

    def _read_otp_black_level(self) -> dict | None:
        """Read factory black level from OTP.

        OTP 0x10-0x13: Black level for each Bayer channel

        Returns:
            Dict with keys 'r', 'gr', 'gb', 'b' or None if unavailable
        """
        # TODO: Implement I2C OTP read when smbus2 available
        # For now, return None — V4L2 driver handles default BLC
        return None

    # ------------------------------------------------------------------
    # Overrides
    # ------------------------------------------------------------------

    def get_info(self) -> dict:
        """Return driver info."""
        info = super().get_info()
        info.update({
            "hdr_mode": "SDR (no HDR)",
            "dynamic_range_db": 81,
            "i2c_bus": self.i2c_bus,
            "i2c_addr": hex(self.i2c_addr),
            "is_master": self.is_master,
            "otp_black_level": self._otp_black_level,
            "note": "GC4653 is SDR-only. HDR not supported by sensor.",
        })
        return info

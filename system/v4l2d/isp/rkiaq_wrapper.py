#!/usr/bin/env python3
"""High-level RKIAQ ISP wrapper for camera 3A control.

Provides hardware auto-exposure, auto-white-balance, and metadata publishing.
Designed for OX03C10 (HDR on-chip, ISP linear) and GC4653 (SDR, ISP linear).

Usage:
    isp = RKIAQWrapper("/dev/video0", "OX03C10")
    if isp.initialize():
        isp.start()
        isp.set_ae(target_brightness=128)
        metadata = isp.get_metadata()
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field

import ctypes

from openpilot.system.v4l2d.isp.rkaiq_ctypes import (
    RKIAQLibrary,
    RkAiqAeSwAttr,
    RkAiqExpInfo,
    RkAiqCctInfo,
    RkAiqWbGain,
    RkAiqHdrExpRatio,
    RK_AIQ_WORKING_MODE_NORMAL,
    RK_AIQ_WORKING_MODE_ISP_HDR2,
    RK_AIQ_WORKING_MODE_ISP_HDR3,
)
from openpilot.common.swaglog import cloudlog


@dataclass
class ISPMetadata:
    """ISP 3A metadata for publishing."""

    exposure_time_us: float = 0.0
    analog_gain: float = 0.0
    digital_gain: float = 0.0
    iso: int = 0
    color_temperature_k: int = 0
    awb_r_gain: float = 1.0
    awb_g_gain: float = 1.0
    awb_b_gain: float = 1.0
    ae_converged: bool = False
    awb_converged: bool = False
    hdr_mode: str = "normal"  # "normal", "hdr2", "hdr3"
    platform_data: dict = field(default_factory=dict)


class RKIAQWrapper:
    """Rockchip ISP 3A wrapper using RKIAQ library.

    Features:
    - Hardware AE/AWB control
    - Metadata extraction for publishing
    - HDR mode switching (for multi-exposure sensors, NOT OX03C10)
    - IQ tuning file support

    Important:
    - For OX03C10: ALWAYS use NORMAL mode. Sensor does HDR on-chip.
    - For GC4653: Use NORMAL mode (SDR only).
    - ISP HDR2/HDR3 modes are for sensors that output separate exposures.
    """

    # IQ file search paths
    IQ_PATHS = [
        "/etc/iqfiles",
        "/usr/share/iqfiles",
        "/data/iqfiles",
    ]

    def __init__(self, device_path: str, sensor_name: str,
                 iq_file_dir: str | None = None):
        """Initialize RKIAQ wrapper.

        Args:
            device_path: Camera device path (e.g., /dev/video0)
            sensor_name: Sensor name for IQ tuning ("OX03C10" or "GC4653")
            iq_file_dir: Directory containing IQ tuning files
        """
        self.device_path = device_path
        self.sensor_name = sensor_name
        self.iq_file_dir = iq_file_dir or self._find_iq_dir()

        self._rkaiq = RKIAQLibrary()
        self._ctx: int | None = None
        self._initialized = False
        self._running = False

        self._current_hdr_mode = RK_AIQ_WORKING_MODE_NORMAL

    def _find_iq_dir(self) -> str | None:
        """Find IQ tuning file directory."""
        for path in self.IQ_PATHS:
            if os.path.isdir(path):
                cloudlog.info(f"RKIAQ: Found IQ directory: {path}")
                return path
        cloudlog.warning("RKIAQ: No IQ directory found")
        return None

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    def initialize(self) -> bool:
        """Initialize RKIAQ context.

        Returns:
            True if initialized (or False if library unavailable and strict mode)
        """
        if not self._rkaiq.is_available:
            cloudlog.error(
                "RKIAQ: librkaiq.so not found — ISP 3A is unavailable. "
                "Camera image quality will be poor. "
                "Install Rockchip BSP with librkaiq.so to enable AE/AWB."
            )
            # Do NOT enable stub mode — this is a safety-critical component.
            # Without ISP 3A, camera exposure will drift and calibration will fail.
            self._initialized = False
            return False

        try:
            lib = self._rkaiq.library

            iq_dir = (self.iq_file_dir or "/etc/iqfiles").encode('utf-8')
            sensor = self.sensor_name.encode('utf-8')

            self._ctx = lib.rk_aiq_uapi2_sysctl_init(iq_dir, sensor, None)

            if not self._ctx:
                cloudlog.error("RKIAQ: Context initialization failed")
                return False

            self._initialized = True
            cloudlog.info(
                f"RKIAQ initialized: {self.sensor_name} @ {self.device_path} "
                f"(IQ: {self.iq_file_dir})"
            )
            return True

        except Exception as e:
            cloudlog.error(f"RKIAQ initialization failed: {e}")
            self._initialized = False
            return False

    def start(self) -> bool:
        """Start ISP processing."""
        if not self._initialized:
            return False

        if not self._rkaiq.is_available or self._ctx is None:
            # RKIAQ is required for proper camera operation
            cloudlog.error("RKIAQ: Cannot start — library not available")
            return False

        try:
            lib = self._rkaiq.library
            ret = lib.rk_aiq_uapi2_sysctl_start(self._ctx)
            if ret == 0:
                self._running = True
                cloudlog.info("RKIAQ: ISP started")
                return True
            else:
                cloudlog.error(f"RKIAQ: Start failed with code {ret}")
                return False
        except Exception as e:
            cloudlog.error(f"RKIAQ start failed: {e}")
            return False

    def stop(self) -> bool:
        """Stop ISP processing."""
        if not self._running:
            return True

        if self._rkaiq.is_available and self._ctx:
            try:
                lib = self._rkaiq.library
                lib.rk_aiq_uapi2_sysctl_stop(self._ctx)
            except Exception as e:
                cloudlog.warning(f"RKIAQ stop error: {e}")

        self._running = False
        return True

    def shutdown(self) -> None:
        """Release all resources."""
        self.stop()

        if self._rkaiq.is_available and self._ctx:
            try:
                lib = self._rkaiq.library
                lib.rk_aiq_uapi2_sysctl_deinit(self._ctx)
            except Exception as e:
                cloudlog.warning(f"RKIAQ deinit error: {e}")

        self._ctx = None
        self._initialized = False
        cloudlog.info("RKIAQ: Shutdown complete")

    # ------------------------------------------------------------------
    # Auto Exposure
    # ------------------------------------------------------------------

    def set_ae(self, target_brightness: int = 128,
               exposure_range_us: tuple[int, int] = (100, 33000),
               gain_range_db: tuple[float, float] = (0.0, 24.0),
               mode: str = "auto") -> bool:
        """Configure auto-exposure.

        Args:
            target_brightness: Target brightness (0-255)
            exposure_range_us: Min/max exposure time in microseconds
            gain_range_db: Min/max analog gain in dB
            mode: "auto" or "manual"

        Returns:
            True if successful
        """
        if not self._initialized:
            return False

        if not self._rkaiq.is_available or self._ctx is None:
            cloudlog.error("RKIAQ: Cannot configure AE — library not available")
            return False

        try:
            lib = self._rkaiq.library
            attr = RkAiqAeSwAttr()
            attr.mode = 0 if mode == "auto" else 1
            attr.target_brightness = target_brightness
            attr.min_exposure_time_us = exposure_range_us[0]
            attr.max_exposure_time_us = exposure_range_us[1]
            attr.min_gain = gain_range_db[0]
            attr.max_gain = gain_range_db[1]

            ret = lib.rk_aiq_uapi2_setExpSwAttr(self._ctx, ctypes.byref(attr))
            if ret == 0:
                cloudlog.debug(f"AE config applied: target={target_brightness}")
                return True
            else:
                cloudlog.warning(f"AE config returned: {ret}")
                return False

        except Exception as e:
            cloudlog.error(f"AE config failed: {e}")
            return False

    # ------------------------------------------------------------------
    # Auto White Balance
    # ------------------------------------------------------------------

    def set_awb(self, mode: str = "auto") -> bool:
        """Configure auto white balance.

        Args:
            mode: "auto", "manual", "daylight", "cloudy"

        Returns:
            True if successful
        """
        if not self._initialized:
            return False

        if not self._rkaiq.is_available or self._ctx is None:
            cloudlog.error("RKIAQ: Cannot configure AWB — library not available")
            return False

        try:
            lib = self._rkaiq.library
            mode_map = {
                "auto": 0,
                "manual": 1,
                "daylight": 2,
                "cloudy": 3,
            }
            rk_mode = mode_map.get(mode.lower(), 0)

            ret = lib.rk_aiq_uapi2_setWbMode(self._ctx, rk_mode)
            if ret == 0:
                cloudlog.debug(f"AWB mode set: {mode}")
                return True
            else:
                cloudlog.warning(f"AWB set returned: {ret}")
                return False

        except Exception as e:
            cloudlog.error(f"AWB config failed: {e}")
            return False

    # ------------------------------------------------------------------
    # HDR Mode (for sensors that need ISP-level HDR, NOT OX03C10)
    # ------------------------------------------------------------------

    def set_hdr_mode(self, mode: str) -> bool:
        """Set ISP HDR mode.

        WARNING: For OX03C10, this should ALWAYS be "normal".
        The sensor does HDR on-chip. ISP HDR modes are for sensors
        that output separate exposures requiring ISP combination.

        Args:
            mode: "normal", "hdr2", "hdr3"

        Returns:
            True if successful
        """
        if not self._initialized:
            return False

        mode_map = {
            "normal": RK_AIQ_WORKING_MODE_NORMAL,
            "hdr2": RK_AIQ_WORKING_MODE_ISP_HDR2,
            "hdr3": RK_AIQ_WORKING_MODE_ISP_HDR3,
        }
        rk_mode = mode_map.get(mode.lower(), RK_AIQ_WORKING_MODE_NORMAL)

        # Safety check for OX03C10
        if self.sensor_name.upper() == "OX03C10" and rk_mode != RK_AIQ_WORKING_MODE_NORMAL:
            cloudlog.error(
                f"REFUSING to set ISP HDR mode for OX03C10. "
                f"Sensor does HDR on-chip. Use 'normal' mode."
            )
            return False

        if not self._rkaiq.is_available or self._ctx is None:
            cloudlog.error("RKIAQ: Cannot set HDR mode — library not available")
            return False

        try:
            lib = self._rkaiq.library
            ret = lib.rk_aiq_uapi2_setHdrMode(self._ctx, rk_mode)
            if ret == 0:
                self._current_hdr_mode = rk_mode
                cloudlog.info(f"HDR mode set: {mode}")
                return True
            else:
                cloudlog.warning(f"HDR mode set returned: {ret}")
                return False

        except Exception as e:
            cloudlog.error(f"HDR mode set failed: {e}")
            return False

    # ------------------------------------------------------------------
    # Metadata
    # ------------------------------------------------------------------

    def get_metadata(self) -> ISPMetadata:
        """Get current ISP 3A metadata."""
        metadata = ISPMetadata()

        if not self._initialized:
            return metadata

        if not self._rkaiq.is_available or self._ctx is None:
            # RKIAQ unavailable — return empty metadata with error flag
            metadata.platform_data = {"backend": "rkiaq_unavailable", "error": "librkaiq.so not found"}
            return metadata

        try:
            lib = self._rkaiq.library

            # Get exposure info
            exp_info = RkAiqExpInfo()
            ret = lib.rk_aiq_uapi2_getCurExpInfo(self._ctx, ctypes.byref(exp_info))
            if ret == 0:
                metadata.exposure_time_us = exp_info.exposure_time_us
                metadata.analog_gain = exp_info.analog_gain
                metadata.digital_gain = exp_info.digital_gain
                metadata.iso = exp_info.iso
                metadata.ae_converged = bool(exp_info.ae_converged)

            # Get color temperature
            cct_info = RkAiqCctInfo()
            ret = lib.rk_aiq_uapi2_getCCT(self._ctx, ctypes.byref(cct_info))
            if ret == 0:
                metadata.color_temperature_k = cct_info.cct
                metadata.awb_converged = bool(cct_info.awb_converged)

            # Get WB gains
            wb_gain = RkAiqWbGain()
            ret = lib.rk_aiq_uapi2_getWBGain(self._ctx, ctypes.byref(wb_gain))
            if ret == 0:
                metadata.awb_r_gain = wb_gain.r_gain
                metadata.awb_g_gain = wb_gain.g_gain
                metadata.awb_b_gain = wb_gain.b_gain

        except Exception as e:
            cloudlog.debug(f"Metadata query failed: {e}")

        metadata.hdr_mode = {
            RK_AIQ_WORKING_MODE_NORMAL: "normal",
            RK_AIQ_WORKING_MODE_ISP_HDR2: "hdr2",
            RK_AIQ_WORKING_MODE_ISP_HDR3: "hdr3",
        }.get(self._current_hdr_mode, "unknown")

        metadata.platform_data = {
            "backend": "rkiaq",
            "version": self._rkaiq.version,
            "sensor": self.sensor_name,
        }

        return metadata

    # ------------------------------------------------------------------
    # Properties
    # ------------------------------------------------------------------

    @property
    def is_available(self) -> bool:
        return self._initialized

    @property
    def is_running(self) -> bool:
        return self._running

    @property
    def has_rkaiq(self) -> bool:
        return self._rkaiq.is_available

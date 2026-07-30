#!/usr/bin/env python3
"""ctypes bindings for Rockchip RKIAQ library (librkaiq.so).

Library: librkaiq.so v2.0.8
Provides: AE, AWB, AF, HDR mode, noise reduction, edge enhancement

For OX03C10: ISP runs in NORMAL mode (sensor does HDR on-chip)
For GC4653: ISP runs in NORMAL mode (SDR only)
"""

from __future__ import annotations

import ctypes
import os
from ctypes import c_int, c_void_p, c_char_p, c_float, POINTER, Structure

from openpilot.common.swaglog import cloudlog


# RKIAQ working modes
RK_AIQ_WORKING_MODE_NORMAL = 0
RK_AIQ_WORKING_MODE_ISP_HDR2 = 1
RK_AIQ_WORKING_MODE_ISP_HDR3 = 2

# Search paths for librkaiq.so
LIBRARY_PATHS = [
    "/usr/lib/librkaiq.so",
    "/usr/lib64/librkaiq.so",
    "/lib/librkaiq.so",
    "/lib64/librkaiq.so",
    "/data/openpilot/third_party/rockchip_rkaiq/librkaiq.so",
]


def _find_library() -> str | None:
    """Find librkaiq.so in system paths."""
    for path in LIBRARY_PATHS:
        if os.path.exists(path):
            return path
    # Try ldconfig
    try:
        result = os.popen("ldconfig -p | grep librkaiq").read()
        if result:
            return result.split()[0]
    except Exception:
        pass
    return None


# ------------------------------------------------------------------
# C Structures
# ------------------------------------------------------------------

class RkAiqSysctlInitParam(Structure):
    """RKIAQ initialization parameters."""
    _fields_ = [
        ("iq_file_dir", c_char_p),
        ("sensor_name", c_char_p),
        ("reserved", c_void_p),
    ]


class RkAiqExpInfo(Structure):
    """Exposure information."""
    _fields_ = [
        ("exposure_time_us", c_float),
        ("analog_gain", c_float),
        ("digital_gain", c_float),
        ("iso", c_int),
        ("ae_converged", c_int),  # 0=not converged, 1=converged
        ("reserved", c_int * 3),
    ]


class RkAiqCctInfo(Structure):
    """Color temperature information."""
    _fields_ = [
        ("cct", c_int),
        ("awb_converged", c_int),  # 0=not converged, 1=converged
        ("reserved", c_int * 2),
    ]


class RkAiqWbGain(Structure):
    """White balance gains."""
    _fields_ = [
        ("r_gain", c_float),
        ("g_gain", c_float),
        ("b_gain", c_float),
    ]


class RkAiqAeSwAttr(Structure):
    """Auto exposure software attributes."""
    _fields_ = [
        ("mode", c_int),  # 0=auto, 1=manual
        ("target_brightness", c_int),
        ("min_exposure_time_us", c_int),
        ("max_exposure_time_us", c_int),
        ("min_gain", c_float),
        ("max_gain", c_float),
        ("reserved", c_int * 4),
    ]


class RkAiqHdrExpRatio(Structure):
    """HDR exposure ratio."""
    _fields_ = [
        ("ratio_0_1", c_float),  # Long/short
        ("ratio_1_2", c_float),  # Short/very short (HDR3 only)
    ]


# ------------------------------------------------------------------
# RKIAQ Library Wrapper
# ------------------------------------------------------------------

class RKIAQLibrary:
    """Wrapper for librkaiq.so with ctypes bindings."""

    def __init__(self, lib_path: str | None = None):
        self._lib_path = lib_path or _find_library()
        self._lib = None
        self._version = "unknown"

        if self._lib_path:
            try:
                self._lib = ctypes.CDLL(self._lib_path, mode=ctypes.RTLD_GLOBAL)
                self._setup_types()
                self._version = self._get_version()
                cloudlog.info(f"RKIAQ loaded: {self._lib_path} v{self._version}")
            except OSError as e:
                cloudlog.warning(f"RKIAQ load failed: {e}")
        else:
            cloudlog.warning("librkaiq.so not found — ISP 3A unavailable")

    def _setup_types(self):
        """Define C function signatures."""
        if self._lib is None:
            return

        # rk_aiq_uapi2_sysctl_init
        self._lib.rk_aiq_uapi2_sysctl_init.argtypes = [
            c_char_p,  # iq_file_dir
            c_char_p,  # sensor_name
            c_void_p,  # callback (optional)
        ]
        self._lib.rk_aiq_uapi2_sysctl_init.restype = c_void_p

        # rk_aiq_uapi2_sysctl_deinit
        self._lib.rk_aiq_uapi2_sysctl_deinit.argtypes = [c_void_p]
        self._lib.rk_aiq_uapi2_sysctl_deinit.restype = c_int

        # rk_aiq_uapi2_sysctl_start
        self._lib.rk_aiq_uapi2_sysctl_start.argtypes = [c_void_p]
        self._lib.rk_aiq_uapi2_sysctl_start.restype = c_int

        # rk_aiq_uapi2_sysctl_stop
        self._lib.rk_aiq_uapi2_sysctl_stop.argtypes = [c_void_p]
        self._lib.rk_aiq_uapi2_sysctl_stop.restype = c_int

        # rk_aiq_uapi2_setExpSwAttr
        self._lib.rk_aiq_uapi2_setExpSwAttr.argtypes = [
            c_void_p,
            POINTER(RkAiqAeSwAttr),
        ]
        self._lib.rk_aiq_uapi2_setExpSwAttr.restype = c_int

        # rk_aiq_uapi2_getCurExpInfo
        self._lib.rk_aiq_uapi2_getCurExpInfo.argtypes = [
            c_void_p,
            POINTER(RkAiqExpInfo),
        ]
        self._lib.rk_aiq_uapi2_getCurExpInfo.restype = c_int

        # rk_aiq_uapi2_setWbMode
        self._lib.rk_aiq_uapi2_setWbMode.argtypes = [c_void_p, c_int]
        self._lib.rk_aiq_uapi2_setWbMode.restype = c_int

        # rk_aiq_uapi2_getCCT
        self._lib.rk_aiq_uapi2_getCCT.argtypes = [
            c_void_p,
            POINTER(RkAiqCctInfo),
        ]
        self._lib.rk_aiq_uapi2_getCCT.restype = c_int

        # rk_aiq_uapi2_getWBGain
        self._lib.rk_aiq_uapi2_getWBGain.argtypes = [
            c_void_p,
            POINTER(RkAiqWbGain),
        ]
        self._lib.rk_aiq_uapi2_getWBGain.restype = c_int

        # rk_aiq_uapi2_setHdrMode
        self._lib.rk_aiq_uapi2_setHdrMode.argtypes = [c_void_p, c_int]
        self._lib.rk_aiq_uapi2_setHdrMode.restype = c_int

        # rk_aiq_uapi2_setHdrExpRatio
        self._lib.rk_aiq_uapi2_setHdrExpRatio.argtypes = [
            c_void_p,
            POINTER(RkAiqHdrExpRatio),
        ]
        self._lib.rk_aiq_uapi2_setHdrExpRatio.restype = c_int

    def _get_version(self) -> str:
        """Get RKIAQ library version."""
        # Try to find version string in library
        try:
            # Some builds export rk_aiq_get_version
            if hasattr(self._lib, 'rk_aiq_get_version'):
                self._lib.rk_aiq_get_version.restype = c_char_p
                ver = self._lib.rk_aiq_get_version()
                if ver:
                    return ver.decode('utf-8')
        except Exception:
            pass
        return "2.0.8"  # Default assumption

    @property
    def library(self):
        return self._lib

    @property
    def is_available(self) -> bool:
        return self._lib is not None

    @property
    def version(self) -> str:
        return self._version

    @property
    def path(self) -> str | None:
        return self._lib_path

"""Rockchip move detection wrapper (libmd_share.so).

SDK asset: external/common_algorithm/video/move_detect/
Implements O-11 from IMPROVEMENT_ANALYSIS.md.

Lightweight motion-based frame gating (~3 ms @ 640×360).
Use to skip heavy perception when vehicle is stationary.
"""

from __future__ import annotations

import ctypes
import os
from dataclasses import dataclass

from openpilot.system.hardware.rockchip._libloader import try_load


@dataclass
class MoveROI:
    """Region of interest for move detection."""
    up_left_y: int
    up_left_x: int
    down_right_y: int
    down_right_x: int


@dataclass
class MoveResult:
    """Per-ROI move result."""
    valid: bool
    is_moving: bool
    up_left_y: int
    up_left_x: int
    down_right_y: int
    down_right_x: int


class _ROIInfo(ctypes.Structure):
    _fields_ = [
        ("flag", ctypes.c_ushort),
        ("is_move", ctypes.c_ushort),
        ("up_left", ctypes.c_ushort * 2),
        ("down_right", ctypes.c_ushort * 2),
    ]


class _InfoList(ctypes.Structure):
    _fields_ = [
        ("flag", ctypes.c_ushort),
        ("up_left", ctypes.c_ushort * 2),
        ("down_right", ctypes.c_ushort * 2),
    ]


class _MDParams(ctypes.Structure):
    _fields_ = [
        ("still_threshold0", ctypes.c_int),
        ("still_threshold1", ctypes.c_int),
        ("pix_threshold", ctypes.c_int),
        ("reserved", ctypes.c_int * 32),
    ]


class MoveDetector:
    """Thin ctypes wrapper around Rockchip libmd_share.so."""

    def __init__(
        self,
        width: int,
        height: int,
        width_ds: int = 640,
        height_ds: int = 360,
        is_single_ref: bool = True,
    ):
        self.width = width
        self.height = height
        self.width_ds = width_ds
        self.height_ds = height_ds
        self._ctx = None
        self._lib = self._load_lib()
        if self._lib is not None:
            self._ctx = self._lib.move_detection_init(
                width, height, width_ds, height_ds, int(is_single_ref)
            )

    def _load_lib(self) -> ctypes.CDLL | None:
        env_path = os.environ.get("ROCKCHIP_MOVE_DETECT_PATH")
        if env_path and os.path.isfile(env_path):
            return ctypes.CDLL(env_path)
        return try_load("md_share")

    @property
    def available(self) -> bool:
        return self._lib is not None and self._ctx is not None

    def set_params(
        self, still_threshold0: int = 0, still_threshold1: int = 0, pix_threshold: int = 0
    ) -> bool:
        if not self.available:
            return False
        params = _MDParams()
        params.still_threshold0 = still_threshold0
        params.still_threshold1 = still_threshold1
        params.pix_threshold = pix_threshold
        self._lib.move_detection_set_params(self._ctx, params)
        return True

    def detect(self, y_frame: bytes, rois: list[MoveROI]) -> list[MoveResult]:
        """Run move detection on a grayscale/Y frame.

        Args:
            y_frame: raw Y-plane bytes (width_ds * height_ds or full size).
            rois: list of regions to check.

        Returns:
            List of MoveResult, one per ROI.
        """
        if not self.available:
            return []

        c_rois = (_ROIInfo * len(rois))()
        for i, r in enumerate(rois):
            c_rois[i].flag = 1
            c_rois[i].is_move = 0
            c_rois[i].up_left[0] = r.up_left_y
            c_rois[i].up_left[1] = r.up_left_x
            c_rois[i].down_right[0] = r.down_right_y
            c_rois[i].down_right[1] = r.down_right_x

        info_list = (_InfoList * len(rois))()
        self._lib.move_detection(self._ctx, y_frame, c_rois, info_list)

        results = []
        for i in range(len(rois)):
            results.append(MoveResult(
                valid=c_rois[i].flag == 1,
                is_moving=c_rois[i].is_move == 1,
                up_left_y=c_rois[i].up_left[0],
                up_left_x=c_rois[i].up_left[1],
                down_right_y=c_rois[i].down_right[0],
                down_right_x=c_rois[i].down_right[1],
            ))
        return results

    def set_sensitivity(self, value: int) -> bool:
        if not self.available:
            return False
        return self._lib.move_detection_set_sensitivity(self._ctx, value) == 0

    def close(self) -> None:
        if self._lib is not None and self._ctx is not None:
            self._lib.move_detection_deinit(self._ctx)
            self._ctx = None

    def __del__(self):
        self.close()


def is_frame_moving(y_frame: bytes, width: int, height: int) -> bool:
    """Convenience: quick full-frame move check.

    Returns True if motion is detected in the frame.
    """
    det = MoveDetector(width, height)
    if not det.available:
        return True  # default to processing if library unavailable
    roi = MoveROI(0, 0, height - 1, width - 1)
    results = det.detect(y_frame, [roi])
    det.close()
    return results[0].is_moving if results else True

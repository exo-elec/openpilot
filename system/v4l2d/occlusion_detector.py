"""Rockchip occlusion detection wrapper (libod_share.so).

SDK asset: external/common_algorithm/video/occlusion_detect/
Implements O-10 from IMPROVEMENT_ANALYSIS.md.

Replaces heuristic cameraMalfunction logic with production-grade
image-content occlusion detection.
"""

from __future__ import annotations

import ctypes
import os
from dataclasses import dataclass

from openpilot.system.hardware.rockchip._libloader import try_load


@dataclass
class OcclusionROI:
    """Region of interest for occlusion detection."""
    up_left_y: int
    up_left_x: int
    down_right_y: int
    down_right_x: int


@dataclass
class OcclusionResult:
    """Per-ROI occlusion result."""
    valid: bool
    up_left_y: int
    up_left_x: int
    down_right_y: int
    down_right_x: int
    occluded: bool


class _ODROIInfo(ctypes.Structure):
    _fields_ = [
        ("flag", ctypes.c_ushort),
        ("up_left", ctypes.c_ushort * 2),
        ("down_right", ctypes.c_ushort * 2),
        ("occlusion", ctypes.c_ushort),
    ]


class OcclusionDetector:
    """Thin ctypes wrapper around Rockchip libod_share.so."""

    def __init__(self, width: int, height: int):
        self.width = width
        self.height = height
        self._ctx = None
        self._lib = self._load_lib()
        if self._lib is not None:
            self._ctx = self._lib.occlusion_detection_init(width, height)

    def _load_lib(self) -> ctypes.CDLL | None:
        env_path = os.environ.get("ROCKCHIP_OCCLUSION_DETECT_PATH")
        if env_path and os.path.isfile(env_path):
            return ctypes.CDLL(env_path)
        return try_load("od_share")

    @property
    def available(self) -> bool:
        return self._lib is not None and self._ctx is not None

    def detect(self, y_frame: bytes, rois: list[OcclusionROI]) -> list[OcclusionResult]:
        """Run occlusion detection on a grayscale/Y frame.

        Args:
            y_frame: raw Y-plane bytes (width * height).
            rois: list of regions to check.

        Returns:
            List of OcclusionResult, one per ROI.
        """
        if not self.available:
            return []

        c_rois = (_ODROIInfo * len(rois))()
        for i, r in enumerate(rois):
            c_rois[i].flag = 1
            c_rois[i].up_left[0] = r.up_left_y
            c_rois[i].up_left[1] = r.up_left_x
            c_rois[i].down_right[0] = r.down_right_y
            c_rois[i].down_right[1] = r.down_right_x
            c_rois[i].occlusion = 0

        ret = self._lib.occlusion_detection(
            self._ctx, y_frame, c_rois, len(rois)
        )
        if ret != 0:
            return []

        results = []
        for i in range(len(rois)):
            results.append(OcclusionResult(
                valid=c_rois[i].flag == 1,
                up_left_y=c_rois[i].up_left[0],
                up_left_x=c_rois[i].up_left[1],
                down_right_y=c_rois[i].down_right[0],
                down_right_x=c_rois[i].down_right[1],
                occluded=c_rois[i].occlusion == 1,
            ))
        return results

    def refresh_background(self) -> bool:
        """Refresh the background model."""
        if not self.available:
            return False
        return self._lib.occlusion_refresh_bg(self._ctx) == 0

    def set_sensitivity(self, value: int) -> bool:
        """Set detection sensitivity."""
        if not self.available:
            return False
        return self._lib.occlusion_set_sensitivity(self._ctx, value) == 0

    def enable(self, enable: bool, value: int = 0) -> bool:
        """Enable/disable detection switch."""
        if not self.available:
            return False
        return self._lib.occlusion_detection_enable_switch(
            self._ctx, int(enable), value
        ) == 0

    def close(self) -> None:
        """Release resources."""
        if self._lib is not None and self._ctx is not None:
            self._lib.occlusion_detection_deinit(self._ctx)
            self._ctx = None

    def __del__(self):
        self.close()


def detect_camera_occlusion(y_frame: bytes, width: int, height: int) -> bool:
    """Convenience: quick full-frame occlusion check.

    Returns True if the frame appears occluded.
    """
    det = OcclusionDetector(width, height)
    if not det.available:
        return False
    roi = OcclusionROI(0, 0, height - 1, width - 1)
    results = det.detect(y_frame, [roi])
    det.close()
    return results[0].occluded if results else False

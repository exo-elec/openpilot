"""
Stereo Correction — Wide-Baseline to Human-IPD Adaptation

ExoPilot 01M (RK3588) has an 80 mm stereo baseline.
ExoPilot 02M (RK3576) has a 160 mm stereo baseline.
Both are wider than the average human interpupillary distance (~63 mm).
Viewing raw rectified stereo in a headset causes:
  • Excessive near-field disparity → eye divergence, nausea
  • Wrong scale perception → objects appear miniature or giant
  • Geometric distortion outside the sweet spot

This module auto-detects the platform baseline and corrects by recomputing
stereo rectification with a scaled translation vector:

    T_corrected = T_actual × (IPD_target / baseline_actual)

This synthesizes virtual cameras at human eye spacing while preserving the
original camera intrinsics and distortion model.

Usage:
    corrector = StereoCorrection(calib_path="/data/calibration/stereo_calibration.npz")
    left_vr, right_vr = corrector.correct(left_raw, right_raw)

References:
  - OpenCV stereoRectify docs: https://docs.opencv.org/4.x/d9/d0c/group__calib3d.html
  - ExoPilot 01M config: system/hardware/rk3588/camera_config.py  (80 mm)
  - ExoPilot 02M config: visionpilot's own camera config (160 mm)
"""

from __future__ import annotations

import os
from pathlib import Path

import numpy as np

# Optional cv2 — if not available, fall back to simple crop/shift
try:
    import cv2
    HAS_CV2 = True
except ImportError:
    HAS_CV2 = False

from openpilot.common.swaglog import cloudlog
from openpilot.system.hardware.hw import Paths


class StereoCorrection:
    """
    Corrects wide-baseline stereo images for comfortable headset viewing.

    Two operating modes:
      1. **Full rectification** (preferred): loads calibration NPZ and
         recomputes rectification maps with scaled baseline.
      2. **Fallback shift** (no calibration): simple horizontal crop/shift
         that approximates baseline reduction.
    """

    # Default calibration search paths (canonical HAL store first, then legacy)
    DEFAULT_CALIB_PATHS: tuple[str, ...] = (
        os.path.join(Paths.eop_data_root(), "calibration", "stereo_intrinsics.npz"),
        os.path.join(Paths.eop_data_root(), "calibration", "stereo_calibration.npz"),
        "/data/params/d/calibration/stereo_calibration.npz",
        "/persist/calibration/stereo_calibration.npz",
    )

    def __init__(
        self,
        calib_path: str | None = None,
        target_ipd_mm: float = 63.0,
        actual_baseline_mm: float | None = None,
        image_size: tuple[int, int] | None = None,
    ):
        self.target_ipd_mm = float(target_ipd_mm)

        # Auto-detect baseline from platform hardware if not provided
        if actual_baseline_mm is None:
            actual_baseline_mm = self._detect_baseline()

        self.actual_baseline_mm = float(actual_baseline_mm)
        self.image_size = image_size  # (width, height)
        self._scale = self.target_ipd_mm / self.actual_baseline_mm

        cloudlog.info(
            f"StereoCorrection: target_ipd={self.target_ipd_mm}mm, " +
            f"actual_baseline={self.actual_baseline_mm}mm, scale={self._scale:.3f}"
        )

        # Calibration data
        self._has_full_calib = False
        self._left_map1: np.ndarray | None = None
        self._left_map2: np.ndarray | None = None
        self._right_map1: np.ndarray | None = None
        self._right_map2: np.ndarray | None = None
        self._roi_left: tuple[int, int, int, int] | None = None
        self._roi_right: tuple[int, int, int, int] | None = None

        # Fallback data
        self._fallback_shift_px = 0

        # Attempt to load calibration
        self._load_calibration(calib_path)

    @staticmethod
    def _detect_baseline() -> float:
        """Detect stereo baseline from platform hardware."""
        try:
            from openpilot.system.hardware import HARDWARE
            baseline = HARDWARE.get_stereo_baseline_mm()
            if baseline > 0:
                cloudlog.info(f"StereoCorrection: auto-detected baseline {baseline}mm from {HARDWARE.get_device_type()}")
                return baseline
        except Exception as e:
            cloudlog.warning(f"StereoCorrection: hardware detection failed ({e}), using default 80mm")
        return 80.0

    # ------------------------------------------------------------------
    # Calibration loading
    # ------------------------------------------------------------------

    def _load_calibration(self, calib_path: str | None = None) -> bool:
        """Load stereo calibration and compute VR-corrected rectification maps."""
        paths = []
        if calib_path:
            paths.append(calib_path)
        paths.extend(self.DEFAULT_CALIB_PATHS)

        npz_path: Path | None = None
        for p in paths:
            candidate = Path(p)
            if candidate.exists():
                npz_path = candidate
                break

        if npz_path is None:
            cloudlog.warning(
                f"VRStereoCorrection: no calibration found (tried {paths}). " +
                "Using fallback shift mode."
            )
            self._init_fallback()
            return False

        if not HAS_CV2:
            cloudlog.warning("VRStereoCorrection: cv2 unavailable, using fallback shift mode.")
            self._init_fallback()
            return False

        try:
            try:
                # Factory intrinsics are owned by the exopilot HAL
                from hal.drivers.camera import load_stereo_intrinsics
                cal = load_stereo_intrinsics(str(npz_path))
                if cal is None:
                    raise ValueError(f"malformed calibration: {npz_path}")
                M1, dist1, M2, dist2, R, T = cal.M1, cal.dist1, cal.M2, cal.dist2, cal.R, cal.T
                calib_image_size = None
            except ImportError:
                data = np.load(npz_path)

                # Required fields
                M1 = data["M1"]           # left camera matrix
                dist1 = data["dist1"]     # left distortion
                M2 = data["M2"]           # right camera matrix
                dist2 = data["dist2"]     # right distortion
                R = data["R"]             # rotation: right -> left
                T = data["T"]             # translation: right -> left (mm or m)
                calib_image_size = tuple(data["image_size"]) if "image_size" in data else None

            # Image size — infer from calibration or argument
            if self.image_size is not None:
                img_w, img_h = self.image_size
            elif calib_image_size is not None:
                img_w, img_h = calib_image_size
            else:
                # Infer from camera matrix focal-length guess
                img_w = int(M1[0, 2] * 2)
                img_h = int(M1[1, 2] * 2)
                cloudlog.info(f"VRStereoCorrection: inferred image size {img_w}x{img_h}")

            self.image_size = (img_w, img_h)

            # ------------------------------------------------------------------
            # Scale translation to human IPD
            # ------------------------------------------------------------------
            T_norm = np.linalg.norm(T)
            if T_norm > 0:
                # Normalise then scale
                T_scaled = T * self._scale
                cloudlog.info(
                    f"VRStereoCorrection: scaling baseline {T_norm:.1f} → " +
                    f"{np.linalg.norm(T_scaled):.1f} (scale={self._scale:.3f}, " +
                    f"IPD={self.target_ipd_mm}mm)"
                )
            else:
                T_scaled = T
                cloudlog.warning("StereoCorrection: T has zero norm, skipping scaling")

            # ------------------------------------------------------------------
            # Compute rectification with scaled baseline
            # ------------------------------------------------------------------
            R1, R2, P1, P2, Q, roi1, roi2 = cv2.stereoRectify(
                M1, dist1,
                M2, dist2,
                (img_w, img_h),
                R, T_scaled,
                flags=cv2.CALIB_ZERO_DISPARITY,
                alpha=0,  # crop to valid pixels
            )

            self._left_map1, self._left_map2 = cv2.initUndistortRectifyMap(
                M1, dist1, R1, P1, (img_w, img_h), cv2.CV_32FC1
            )
            self._right_map1, self._right_map2 = cv2.initUndistortRectifyMap(
                M2, dist2, R2, P2, (img_w, img_h), cv2.CV_32FC1
            )

            self._roi_left = roi1
            self._roi_right = roi2
            self._has_full_calib = True

            cloudlog.info(
                f"StereoCorrection: full rectification loaded from {npz_path} " +
                f"(ROI left={roi1}, right={roi2})"
            )
            return True

        except Exception as e:
            cloudlog.error(f"StereoCorrection: failed to load calibration: {e}")
            self._init_fallback()
            return False

    def _init_fallback(self) -> None:
        """Initialise simple horizontal shift fallback (no calibration)."""
        if self.image_size is None:
            self._fallback_shift_px = 0
            return
        img_w, _ = self.image_size
        # Approximate shift: for a typical focal length ~960 px @ 1280x720,
        # the pixel shift at infinity is ~f × (baseline_diff / Z).
        # At typical teleop distance Z = 1.5 m, baseline_diff = 0.097 m:
        # shift ≈ 960 × 0.097 / 1.5 ≈ 62 px.
        # We use a conservative fraction of image width.
        self._fallback_shift_px = max(1, int(img_w * 0.05 * (1.0 - self._scale)))
        cloudlog.info(
            f"StereoCorrection: fallback shift = {self._fallback_shift_px}px " +
            f"(scale={self._scale:.3f})"
        )

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def correct(
        self,
        left: np.ndarray,
        right: np.ndarray,
    ) -> tuple[np.ndarray, np.ndarray]:
        """
        Correct a stereo pair for human IPD viewing.

        Returns (left_corrected, right_corrected) as BGR uint8 images.
        """
        if self._has_full_calib and HAS_CV2:
            return self._correct_full(left, right)
        return self._correct_fallback(left, right)

    def _correct_full(
        self,
        left: np.ndarray,
        right: np.ndarray,
    ) -> tuple[np.ndarray, np.ndarray]:
        """Full OpenCV rectification with scaled baseline."""
        left_rect = cv2.remap(left, self._left_map1, self._left_map2, cv2.INTER_LINEAR)
        right_rect = cv2.remap(right, self._right_map1, self._right_map2, cv2.INTER_LINEAR)

        # Crop to valid ROI to remove black borders
        if self._roi_left is not None:
            x, y, w, h = self._roi_left
            left_rect = left_rect[y : y + h, x : x + w]
        if self._roi_right is not None:
            x, y, w, h = self._roi_right
            right_rect = right_rect[y : y + h, x : x + w]

        return left_rect, right_rect

    def _correct_fallback(
        self,
        left: np.ndarray,
        right: np.ndarray,
    ) -> tuple[np.ndarray, np.ndarray]:
        """
        Fallback: crop/shift to approximate baseline reduction.

        Shifts both images inward so that the effective disparity range
        matches a narrower baseline. This is a coarse approximation but
        works without calibration data.
        """
        if self._fallback_shift_px <= 0:
            return left, right

        h, w = left.shape[:2]
        s = self._fallback_shift_px

        # Crop symmetrically from outer edges
        left_cropped = left[:, s : w - s]
        right_cropped = right[:, s : w - s]

        # Resize back to original width (slight horizontal stretch)
        if HAS_CV2:
            left_out = cv2.resize(left_cropped, (w, h), interpolation=cv2.INTER_LINEAR)
            right_out = cv2.resize(right_cropped, (w, h), interpolation=cv2.INTER_LINEAR)
        else:
            # No cv2 — just pad with black
            left_out = np.zeros_like(left)
            right_out = np.zeros_like(right)
            left_out[:, s : w - s] = left_cropped
            right_out[:, s : w - s] = right_cropped

        return left_out, right_out

    @property
    def has_calibration(self) -> bool:
        return self._has_full_calib

    @property
    def scale_factor(self) -> float:
        return self._scale

    @property
    def baseline_mm(self) -> float:
        return self.actual_baseline_mm

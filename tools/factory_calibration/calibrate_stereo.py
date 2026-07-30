#!/usr/bin/env python3
"""
Stereo Camera Factory Calibration Tool for RK3588/LubanCat5 BTB.

Generates calibration files in the HAL calibration directory
(Paths.eop_data_root()/calibration, /data/calibration on device):
  - camera_intrinsics_video2.json (left camera)
  - camera_intrinsics_video3.json (right camera)
  - stereo_calibration.json (extrinsics)
  - stereo_intrinsics.npz (factory intrinsics, via exopilot HAL store)

Usage:
  python calibrate_stereo.py --board-width 9 --board-height 6 --square-size 0.025

Requirements:
  - Checkerboard pattern printed and mounted on flat surface
  - Stereo cameras connected and accessible via V4L2 (/dev/video2, /dev/video3)
"""

from __future__ import annotations

import argparse
import json
import math
import os
import sys
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import cv2
import numpy as np

# Default calibration settings
DEFAULT_BOARD_WIDTH = 9  # Number of inner corners (width)
DEFAULT_BOARD_HEIGHT = 6  # Number of inner corners (height)
DEFAULT_SQUARE_SIZE_M = 0.025  # 25mm squares

# Factory intrinsics are owned by the exopilot HAL store; fall back to the
# legacy standalone paths when run without the repo/HAL on sys.path.
try:
    from openpilot.system.hardware.hw import Paths
    DEFAULT_OUTPUT_DIR = os.path.join(Paths.eop_data_root(), "calibration")
except ImportError:
    DEFAULT_OUTPUT_DIR = "/data/calibration"

try:
    from hal.drivers.camera import StereoIntrinsics, save_stereo_intrinsics
    HAL_AVAILABLE = True
except ImportError:
    HAL_AVAILABLE = False


@dataclass
class CalibrationData:
    """Holds calibration data for a single camera."""
    camera_matrix: np.ndarray
    distortion: np.ndarray
    image_size: tuple[int, int]
    reprojection_error_px: float
    calibration_valid: bool


def detect_chessboard(frame: np.ndarray, board_width: int, board_height: int) -> np.ndarray | None:
    """Detect chessboard corners in frame. Returns corners or None."""
    gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
    found, corners = cv2.findChessboardCorners(
        gray, (board_width, board_height),
        cv2.CALIB_CB_ADAPTIVE_THRESH + cv2.CALIB_CB_NORMALIZE_IMAGE
    )
    if found:
        # Refine corner positions
        criteria = (cv2.TERM_CRITERIA_EPS + cv2.TERM_CRITERIA_MAX_ITER, 30, 0.001)
        corners = cv2.cornerSubPix(gray, corners, (11, 11), (-1, -1), criteria)
    return corners if found else None


def capture_calibration_frames(
    device_path: str,
    board_width: int,
    board_height: int,
    num_frames: int = 20,
    delay_sec: float = 1.0,
) -> list[np.ndarray]:
    """Capture frames with chessboard pattern detected."""
    print(f"\nOpening camera: {device_path}")
    cap = cv2.VideoCapture(device_path)
    if not cap.isOpened():
        raise RuntimeError(f"Failed to open camera: {device_path}")

    # Set camera properties for GC4653
    cap.set(cv2.CAP_PROP_FRAME_WIDTH, 1280)
    cap.set(cv2.CAP_PROP_FRAME_HEIGHT, 720)
    cap.set(cv2.CAP_PROP_FPS, 20)

    frames: list[np.ndarray] = []
    print(f"Capturing {num_frames} calibration frames...")
    print("Show the chessboard pattern to the camera.")
    print("Press 'q' to quit early, 'c' to capture manually.\n")

    last_capture_time = 0.0
    while len(frames) < num_frames:
        ret, frame = cap.read()
        if not ret:
            print("Failed to read frame, retrying...")
            time.sleep(0.1)
            continue

        display = frame.copy()
        corners = detect_chessboard(frame, board_width, board_height)

        status_color = (0, 255, 0) if corners is not None else (0, 0, 255)
        status_text = "Pattern detected" if corners is not None else "No pattern"
        cv2.putText(display, status_text, (10, 30), cv2.FONT_HERSHEY_SIMPLEX, 1, status_color, 2)
        cv2.putText(display, f"Captured: {len(frames)}/{num_frames}", (10, 70), cv2.FONT_HERSHEY_SIMPLEX, 1, (255, 255, 255), 2)

        if corners is not None:
            cv2.drawChessboardCorners(display, (board_width, board_height), corners, True)

        cv2.imshow(f"Calibration - {device_path}", display)
        key = cv2.waitKey(1) & 0xFF

        if key == ord('q'):
            print("Quit requested")
            break
        elif key == ord('c') and corners is not None:
            frames.append(frame)
            print(f"  Manual capture: {len(frames)}/{num_frames}")
            time.sleep(0.5)
        elif corners is not None and time.monotonic() - last_capture_time > delay_sec:
            frames.append(frame)
            last_capture_time = time.monotonic()
            print(f"  Auto capture: {len(frames)}/{num_frames}")

    cap.release()
    cv2.destroyWindow(f"Calibration - {device_path}")
    return frames


def calibrate_single_camera(
    frames: list[np.ndarray],
    board_width: int,
    board_height: int,
    square_size_m: float,
) -> CalibrationData:
    """Calibrate a single camera from captured frames."""
    objp = np.zeros((board_width * board_height, 3), np.float32)
    objp[:, :2] = np.mgrid[0:board_width, 0:board_height].T.reshape(-1, 2) * square_size_m

    objpoints: list[np.ndarray] = []
    imgpoints: list[np.ndarray] = []
    image_size: tuple[int, int] = (0, 0)

    print(f"\nProcessing {len(frames)} frames...")
    for i, frame in enumerate(frames):
        corners = detect_chessboard(frame, board_width, board_height)
        if corners is not None:
            objpoints.append(objp)
            imgpoints.append(corners)
            image_size = (frame.shape[1], frame.shape[0])
            print(f"  Frame {i+1}: corners detected")
        else:
            print(f"  Frame {i+1}: no corners")

    if len(objpoints) < 5:
        raise RuntimeError(f"Need at least 5 valid frames, got {len(objpoints)}")

    print(f"\nCalibrating with {len(objpoints)} valid frames...")
    ret, camera_matrix, distortion, rvecs, tvecs = cv2.calibrateCamera(
        objpoints, imgpoints, image_size, None, None
    )

    # Calculate reprojection error
    total_error = 0.0
    for i in range(len(objpoints)):
        imgpoints2, _ = cv2.projectPoints(objpoints[i], rvecs[i], tvecs[i], camera_matrix, distortion)
        error = cv2.norm(imgpoints[i], imgpoints2, cv2.NORM_L2) / len(imgpoints2)
        total_error += error
    reprojection_error_px = total_error / len(objpoints)

    # Validate calibration quality
    calibration_valid = reprojection_error_px < 1.0  # < 1px is good

    print(f"  Reprojection error: {reprojection_error_px:.3f} px")
    print(f"  Calibration valid: {calibration_valid}")

    return CalibrationData(
        camera_matrix=camera_matrix,
        distortion=distortion,
        image_size=image_size,
        reprojection_error_px=reprojection_error_px,
        calibration_valid=calibration_valid,
    )


def calibrate_stereo(
    left_frames: list[np.ndarray],
    right_frames: list[np.ndarray],
    left_calib: CalibrationData,
    right_calib: CalibrationData,
    board_width: int,
    board_height: int,
    square_size_m: float,
) -> tuple[np.ndarray, np.ndarray, float, float]:
    """Calibrate stereo camera pair. Returns (R, T, baseline_m, rotation_angle_deg)."""
    objp = np.zeros((board_width * board_height, 3), np.float32)
    objp[:, :2] = np.mgrid[0:board_width, 0:board_height].T.reshape(-1, 2) * square_size_m

    objpoints: list[np.ndarray] = []
    left_imgpoints: list[np.ndarray] = []
    right_imgpoints: list[np.ndarray] = []

    print(f"\nProcessing stereo pairs...")
    for left_frame, right_frame in zip(left_frames, right_frames):
        left_corners = detect_chessboard(left_frame, board_width, board_height)
        right_corners = detect_chessboard(right_frame, board_width, board_height)

        if left_corners is not None and right_corners is not None:
            objpoints.append(objp)
            left_imgpoints.append(left_corners)
            right_imgpoints.append(right_corners)
            print(f"  Stereo pair: both patterns detected")
        else:
            print(f"  Stereo pair: skipped (missing pattern)")

    if len(objpoints) < 5:
        raise RuntimeError(f"Need at least 5 valid stereo pairs, got {len(objpoints)}")

    print(f"\nCalibrating stereo with {len(objpoints)} valid pairs...")

    flags = cv2.CALIB_FIX_INTRINSIC
    criteria = (cv2.TERM_CRITERIA_EPS + cv2.TERM_CRITERIA_MAX_ITER, 30, 1e-6)

    ret, K1, D1, K2, D2, R, T, E, F = cv2.stereoCalibrate(
        objpoints,
        left_imgpoints,
        right_imgpoints,
        left_calib.camera_matrix,
        left_calib.distortion,
        right_calib.camera_matrix,
        right_calib.distortion,
        left_calib.image_size,
        flags=flags,
        criteria=criteria,
    )

    baseline_m = float(np.linalg.norm(T))
    rotation_angle_deg = float(np.degrees(np.arccos(np.clip((np.trace(R) - 1) / 2, -1, 1))))

    print(f"  Stereo reprojection error: {ret:.3f} px")
    print(f"  Baseline: {baseline_m * 1000:.2f} mm")
    print(f"  Rotation angle: {rotation_angle_deg:.2f}°")

    return R, T, baseline_m, rotation_angle_deg


def save_intrinsics_json(
    path: Path,
    calib: CalibrationData,
    camera_id: str,
    device_path: str,
) -> None:
    """Save camera intrinsics to JSON file."""
    data: dict[str, Any] = {
        "camera_id": camera_id,
        "device_path": device_path,
        "image_width": calib.image_size[0],
        "image_height": calib.image_size[1],
        "camera_matrix": {
            "fx": float(calib.camera_matrix[0, 0]),
            "fy": float(calib.camera_matrix[1, 1]),
            "cx": float(calib.camera_matrix[0, 2]),
            "cy": float(calib.camera_matrix[1, 2]),
        },
        "distortion": {
            "k1": float(calib.distortion[0, 0]),
            "k2": float(calib.distortion[0, 1]),
            "p1": float(calib.distortion[0, 2]),
            "p2": float(calib.distortion[0, 3]),
            "k3": float(calib.distortion[0, 4]) if calib.distortion.shape[1] > 4 else 0.0,
        },
        "quality": {
            "reprojection_error_px": calib.reprojection_error_px,
            "calibration_valid": calib.calibration_valid,
        },
    }

    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, 'w') as f:
        json.dump(data, f, indent=2)
    print(f"  Saved: {path}")


def save_stereo_json(
    path: Path,
    R: np.ndarray,
    T: np.ndarray,
    baseline_m: float,
    rotation_angle_deg: float,
    left_device: str,
    right_device: str,
) -> None:
    """Save stereo extrinsics to JSON file."""
    data: dict[str, Any] = {
        "left_device": left_device,
        "right_device": right_device,
        "rotation_matrix": R.tolist(),
        "translation_vector": T.tolist(),
        "baseline_m": baseline_m,
        "rotation_angle_deg": rotation_angle_deg,
        "quality": {
            "calibration_valid": rotation_angle_deg < 5.0,  # < 5 deg rotation is good
        },
    }

    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, 'w') as f:
        json.dump(data, f, indent=2)
    print(f"  Saved: {path}")


def save_npz(
    output_dir: Path,
    left_calib: CalibrationData,
    right_calib: CalibrationData,
    R: np.ndarray,
    T: np.ndarray,
) -> None:
    """Save calibration in NPZ format (for direct loading)."""
    npz_path = output_dir / "stereo_intrinsics.npz"

    # Compute rectification maps
    R1, R2, P1, P2, Q, roi1, roi2 = cv2.stereoRectify(
        left_calib.camera_matrix, left_calib.distortion,
        right_calib.camera_matrix, right_calib.distortion,
        left_calib.image_size, R, T,
        flags=cv2.CALIB_ZERO_DISPARITY,
        alpha=0,
    )

    left_map1, left_map2 = cv2.initUndistortRectifyMap(
        left_calib.camera_matrix, left_calib.distortion,
        R1, P1, left_calib.image_size, cv2.CV_32FC1
    )
    right_map1, right_map2 = cv2.initUndistortRectifyMap(
        right_calib.camera_matrix, right_calib.distortion,
        R2, P2, right_calib.image_size, cv2.CV_32FC1
    )

    if HAL_AVAILABLE:
        save_stereo_intrinsics(StereoIntrinsics(
            M1=left_calib.camera_matrix,
            dist1=left_calib.distortion,
            M2=right_calib.camera_matrix,
            dist2=right_calib.distortion,
            R=R,
            T=T,
            R1=R1,
            R2=R2,
            P1=P1,
            P2=P2,
            Q=Q,
            left_map1=left_map1,
            left_map2=left_map2,
            right_map1=right_map1,
            right_map2=right_map2,
        ), str(npz_path))
    else:
        np.savez(
            npz_path,
            M1=left_calib.camera_matrix,
            dist1=left_calib.distortion,
            M2=right_calib.camera_matrix,
            dist2=right_calib.distortion,
            R=R,
            T=T,
            R1=R1,
            R2=R2,
            P1=P1,
            P2=P2,
            Q=Q,
            left_map1=left_map1,
            left_map2=left_map2,
            right_map1=right_map1,
            right_map2=right_map2,
        )
    print(f"  Saved: {npz_path}")


def main() -> int:
    parser = argparse.ArgumentParser(description="Stereo camera factory calibration")
    parser.add_argument("--board-width", type=int, default=DEFAULT_BOARD_WIDTH, help="Number of inner corners horizontally")
    parser.add_argument("--board-height", type=int, default=DEFAULT_BOARD_HEIGHT, help="Number of inner corners vertically")
    parser.add_argument("--square-size", type=float, default=DEFAULT_SQUARE_SIZE_M, help="Square size in meters")
    parser.add_argument("--num-frames", type=int, default=20, help="Number of frames to capture")
    parser.add_argument("--delay", type=float, default=1.0, help="Delay between auto captures (seconds)")
    parser.add_argument("--left-device", type=str, default="/dev/video2", help="Left camera device path")
    parser.add_argument("--right-device", type=str, default="/dev/video3", help="Right camera device path")
    parser.add_argument("--output-dir", type=str, default=DEFAULT_OUTPUT_DIR, help="Output directory")
    args = parser.parse_args()

    output_dir = Path(args.output_dir)

    print("=" * 60)
    print("Stereo Camera Factory Calibration")
    print("=" * 60)
    print(f"Checkerboard: {args.board_width}x{args.board_height}")
    print(f"Square size: {args.square_size * 1000:.1f} mm")
    print(f"Output directory: {output_dir}")

    try:
        # Step 1: Capture calibration frames
        print("\n" + "-" * 60)
        print("STEP 1: Capture Left Camera Frames")
        print("-" * 60)
        left_frames = capture_calibration_frames(
            args.left_device, args.board_width, args.board_height, args.num_frames, args.delay
        )

        print("\n" + "-" * 60)
        print("STEP 2: Capture Right Camera Frames")
        print("-" * 60)
        right_frames = capture_calibration_frames(
            args.right_device, args.board_width, args.board_height, args.num_frames, args.delay
        )

        if len(left_frames) < 5 or len(right_frames) < 5:
            print("\nError: Need at least 5 frames per camera")
            return 1

        # Step 2: Calibrate individual cameras
        print("\n" + "-" * 60)
        print("STEP 3: Calibrate Left Camera")
        print("-" * 60)
        left_calib = calibrate_single_camera(
            left_frames, args.board_width, args.board_height, args.square_size
        )

        print("\n" + "-" * 60)
        print("STEP 4: Calibrate Right Camera")
        print("-" * 60)
        right_calib = calibrate_single_camera(
            right_frames, args.board_width, args.board_height, args.square_size
        )

        # Step 3: Stereo calibration
        print("\n" + "-" * 60)
        print("STEP 5: Stereo Calibration")
        print("-" * 60)
        R, T, baseline_m, rotation_angle_deg = calibrate_stereo(
            left_frames, right_frames,
            left_calib, right_calib,
            args.board_width, args.board_height, args.square_size
        )

        # Step 4: Save calibration files
        print("\n" + "-" * 60)
        print("STEP 6: Save Calibration Files")
        print("-" * 60)

        save_intrinsics_json(
            output_dir / "camera_intrinsics_video2.json",
            left_calib,
            "stereo_left",
            args.left_device
        )
        save_intrinsics_json(
            output_dir / "camera_intrinsics_video3.json",
            right_calib,
            "stereo_right",
            args.right_device
        )
        save_stereo_json(
            output_dir / "stereo_calibration.json",
            R, T, baseline_m, rotation_angle_deg,
            args.left_device, args.right_device
        )
        save_npz(output_dir, left_calib, right_calib, R, T)

        print("\n" + "=" * 60)
        print("Calibration Complete!")
        print("=" * 60)
        print(f"Files saved to: {output_dir}")
        print("\nNext steps:")
        print("  1. Verify calibration by running stereod")
        print("  2. Check /data/log/stereod.log for validation messages")
        print("  3. Recalibrate if depth accuracy is poor")

        return 0

    except Exception as e:
        print(f"\nError: {e}")
        import traceback
        traceback.print_exc()
        return 1
    finally:
        cv2.destroyAllWindows()


if __name__ == "__main__":
    sys.exit(main())

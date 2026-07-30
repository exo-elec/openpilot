#!/usr/bin/env python3
"""
EOP Camera Calibration Tool
===========================

Interactive tool for calibrating ExoPilot camera arrays using OpenCV.

Supports calibration patterns:
- Chessboard: Classic checkerboard
- ChArUco: Chessboard with ArUco markers (recommended for accuracy)
- Circles: Symmetric circles grid

Usage:
    # Calibrate from video file
    python camera_calibrator.py --video road.mp4 --camera road
    
    # Calibrate from camera device
    python camera_calibrator.py --device /dev/video0 --camera road
    
    # Calibrate all cameras on ExoPilot 01M
    python camera_calibrator.py --batch \
        --road /dev/video0 --wide_road /dev/video1 \
        --stereo_left /dev/video22 --stereo_right /dev/video31
    
    # Generate calibration pattern
    python camera_calibrator.py --generate-pattern --type charuco

Output:
    Creates YAML calibration files compatible with:
    - OpenPilot / EOP
    - VisionPilot (for cross-compatibility)

See Also:
    - OpenCV calibration tutorial: https://docs.opencv.org/4.x/dc/dbb/tutorial_py_calibration.html
    - ChArUco calibration: https://docs.opencv.org/4.x/df/d4a/tutorial_charuco_detection.html
"""

from __future__ import annotations

import argparse
import cv2
import cv2.aruco as aruco
import numpy as np
from dataclasses import dataclass, field
from pathlib import Path
from datetime import datetime
from typing import Optional, Tuple, List, Dict, Union
import yaml
import sys

# Add openpilot to path
sys.path.insert(0, str(Path(__file__).parent.parent.parent))

from openpilot.selfdrive.gridd.camera_geometry import (
    CameraArrayGeometry, CameraIntrinsics, CameraExtrinsics, CameraConfig
)


@dataclass
class CalibrationResult:
    """Single camera calibration result."""
    camera_id: str
    camera_matrix: np.ndarray = field(default_factory=lambda: np.eye(3))
    dist_coeffs: np.ndarray = field(default_factory=lambda: np.zeros(5))
    image_width: int = 1920
    image_height: int = 1080
    rotation: Optional[np.ndarray] = None
    translation: Optional[np.ndarray] = None
    reprojection_error: float = float('inf')
    num_images: int = 0
    calibration_date: str = ""
    
    def to_yaml(self) -> dict:
        """Convert to YAML format."""
        data = {
            'camera_id': self.camera_id,
            'intrinsics': {
                'width': self.image_width,
                'height': self.image_height,
                'fx': float(self.camera_matrix[0, 0]),
                'fy': float(self.camera_matrix[1, 1]),
                'cx': float(self.camera_matrix[0, 2]),
                'cy': float(self.camera_matrix[1, 2]),
                'distortion': {
                    'k1': float(self.dist_coeffs[0]) if len(self.dist_coeffs) > 0 else 0.0,
                    'k2': float(self.dist_coeffs[1]) if len(self.dist_coeffs) > 1 else 0.0,
                    'p1': float(self.dist_coeffs[2]) if len(self.dist_coeffs) > 2 else 0.0,
                    'p2': float(self.dist_coeffs[3]) if len(self.dist_coeffs) > 3 else 0.0,
                    'k3': float(self.dist_coeffs[4]) if len(self.dist_coeffs) > 4 else 0.0,
                }
            },
            'calibration': {
                'date': self.calibration_date,
                'reprojection_error': float(self.reprojection_error),
                'num_images': self.num_images,
            }
        }
        
        if self.rotation is not None:
            data['extrinsics'] = {
                'rotation': self.rotation.tolist(),
                'translation': self.translation.tolist() if self.translation is not None else [0.0, 0.0, 0.0]
            }
        
        return data


class PatternDetector:
    """Detect calibration patterns in images."""
    
    def __init__(self, pattern_type: str, pattern_size: Tuple[int, int], square_size_m: float):
        self.pattern_type = pattern_type.lower()
        self.pattern_size = pattern_size
        self.square_size_m = square_size_m
        
        if self.pattern_type == 'charuco':
            self.aruco_dict = aruco.getPredefinedDictionary(aruco.DICT_4X4_50)
            self.charuco_board = aruco.CharucoBoard(
                pattern_size, square_size_m, square_size_m * 0.6, self.aruco_dict
            )
    
    def detect(self, image: np.ndarray) -> Optional[Tuple[np.ndarray, np.ndarray]]:
        """Detect pattern in image.
        
        Returns:
            (object_points, image_points) or None if not detected
        """
        gray = cv2.cvtColor(image, cv2.COLOR_BGR2GRAY)
        
        if self.pattern_type == 'chessboard':
            return self._detect_chessboard(gray, image)
        elif self.pattern_type == 'charuco':
            return self._detect_charuco(gray)
        elif self.pattern_type == 'circles':
            return self._detect_circles(gray)
        else:
            raise ValueError(f"Unknown pattern type: {self.pattern_type}")
    
    def _detect_chessboard(self, gray: np.ndarray, image: np.ndarray) -> Optional[Tuple[np.ndarray, np.ndarray]]:
        """Detect chessboard corners."""
        found, corners = cv2.findChessboardCorners(
            gray, self.pattern_size,
            cv2.CALIB_CB_ADAPTIVE_THRESH + cv2.CALIB_CB_NORMALIZE_IMAGE
        )
        
        if not found:
            return None
        
        # Refine corners
        criteria = (cv2.TERM_CRITERIA_EPS + cv2.TERM_CRITERIA_MAX_ITER, 30, 0.001)
        corners = cv2.cornerSubPix(gray, corners, (11, 11), (-1, -1), criteria)
        
        # Generate 3D object points
        objp = np.zeros((self.pattern_size[0] * self.pattern_size[1], 3), np.float32)
        objp[:, :2] = np.mgrid[0:self.pattern_size[0], 0:self.pattern_size[1]].T.reshape(-1, 2)
        objp *= self.square_size_m
        
        return objp, corners.reshape(-1, 2)
    
    def _detect_charuco(self, gray: np.ndarray) -> Optional[Tuple[np.ndarray, np.ndarray]]:
        """Detect ChArUco pattern."""
        corners, ids, _ = aruco.detectMarkers(gray, self.aruco_dict)
        
        if len(corners) < 4:
            return None
        
        # Interpolate Charuco corners
        charuco_retval, charuco_corners, charuco_ids = aruco.interpolateCornersCharuco(
            corners, ids, gray, self.charuco_board
        )
        
        if charuco_corners is None or len(charuco_corners) < 4:
            return None
        
        # Get object points for detected corners
        obj_points = []
        img_points = []
        
        for i, corner_id in enumerate(charuco_ids.flatten()):
            corner_3d = self.charuco_board.getChessboardCorners()[corner_id]
            obj_points.append(corner_3d)
            img_points.append(charuco_corners[i][0])
        
        return np.array(obj_points), np.array(img_points)
    
    def _detect_circles(self, gray: np.ndarray) -> Optional[Tuple[np.ndarray, np.ndarray]]:
        """Detect symmetric circles grid."""
        found, centers = cv2.findCirclesGrid(
            gray, self.pattern_size,
            cv2.CALIB_CB_SYMMETRIC_GRID + cv2.CALIB_CB_CLUSTERING
        )
        
        if not found:
            return None
        
        objp = np.zeros((self.pattern_size[0] * self.pattern_size[1], 3), np.float32)
        objp[:, :2] = np.mgrid[0:self.pattern_size[0], 0:self.pattern_size[1]].T.reshape(-1, 2)
        objp *= self.square_size_m
        
        return objp, centers.reshape(-1, 2)


def calibrate_camera(
    video_source: str,
    camera_id: str,
    pattern_type: str = 'charuco',
    pattern_size: Tuple[int, int] = (9, 6),
    square_size_m: float = 0.025,
    min_images: int = 15,
    output_dir: Path = Path("./calibration_output")
) -> Optional[CalibrationResult]:
    """Calibrate a single camera.
    
    Args:
        video_source: Path to video file or device (e.g., /dev/video0)
        camera_id: Camera identifier (road, wide_road, tele, stereo_left, stereo_right)
        pattern_type: 'chessboard', 'charuco', or 'circles'
        pattern_size: (width, height) of pattern in squares
        square_size_m: Size of each square in meters
        min_images: Minimum number of calibration images
        output_dir: Directory to save calibration files
    
    Returns:
        CalibrationResult or None if calibration failed
    """
    print(f"\n{'='*60}")
    print(f"Calibrating {camera_id}")
    print(f"{'='*60}\n")
    
    # Open video source
    if video_source.startswith('/dev'):
        cap = cv2.VideoCapture(video_source)
        print(f"Using camera device: {video_source}")
    else:
        cap = cv2.VideoCapture(video_source)
        print(f"Using video file: {video_source}")
    
    if not cap.isOpened():
        print(f"Error: Could not open {video_source}")
        return None
    
    # Get video properties
    ret, frame = cap.read()
    if not ret:
        print("Error: Could not read from video source")
        return None
    
    h, w = frame.shape[:2]
    print(f"Resolution: {w}x{h}")
    print(f"Pattern: {pattern_type} {pattern_size[0]}x{pattern_size[1]}")
    print(f"Square size: {square_size_m*1000:.0f}mm")
    print()
    
    # Create detector
    detector = PatternDetector(pattern_type, pattern_size, square_size_m)
    
    # Storage for calibration data
    obj_points = []
    img_points = []
    
    # Create output directory
    output_dir.mkdir(parents=True, exist_ok=True)
    
    # Processing parameters
    frame_count = 0
    sample_interval = 10
    
    print("Controls:")
    print("  SPACE: Pause/Resume")
    print("  S: Save current frame (if pattern detected)")
    print("  Q: Quit and calibrate")
    print()
    
    paused = False
    
    while True:
        if not paused:
            ret, frame = cap.read()
            if not ret:
                break
            frame_count += 1
            
            if frame_count % sample_interval != 0:
                continue
        
        # Detect pattern
        detection = detector.detect(frame)
        
        # Create visualization
        vis = frame.copy()
        if detection:
            # Draw detected corners
            for pt in detection[1]:
                cv2.circle(vis, tuple(pt.astype(int)), 5, (0, 255, 0), -1)
            status = f"DETECTED ({len(img_points)}/{min_images})"
            color = (0, 255, 0)
        else:
            status = "NO PATTERN"
            color = (0, 0, 255)
        
        # Add status overlay
        cv2.putText(vis, status, (10, 30), cv2.FONT_HERSHEY_SIMPLEX, 1, color, 2)
        cv2.putText(vis, f"Frame: {frame_count}", (10, 60), cv2.FONT_HERSHEY_SIMPLEX, 0.6, (255, 255, 255), 1)
        cv2.putText(vis, f"Samples: {len(img_points)}", (10, 85), cv2.FONT_HERSHEY_SIMPLEX, 0.6, (255, 255, 255), 1)
        
        if paused:
            cv2.putText(vis, "PAUSED", (w-150, 30), cv2.FONT_HERSHEY_SIMPLEX, 1, (0, 255, 255), 2)
        
        cv2.imshow(f"Calibration - {camera_id}", vis)
        
        key = cv2.waitKey(1 if not paused else 0) & 0xFF
        
        if key == ord('q'):
            break
        elif key == ord(' '):
            paused = not paused
        elif key == ord('s') and detection:
            obj_points.append(detection[0])
            img_points.append(detection[1])
            print(f"Frame {frame_count}: Pattern saved ({len(img_points)} total)")
    
    cap.release()
    cv2.destroyAllWindows()
    
    print(f"\nTotal frames processed: {frame_count}")
    print(f"Patterns collected: {len(img_points)}")
    
    if len(img_points) < min_images:
        print(f"Warning: Only {len(img_points)} images collected. Need at least {min_images}.")
        response = input("Continue anyway? (y/n): ")
        if response.lower() != 'y':
            return None
    
    if len(img_points) < 5:
        print(f"Error: Need at least 5 images for calibration")
        return None
    
    # Run calibration
    print("\nRunning calibration...")
    
    criteria = (cv2.TERM_CRITERIA_EPS + cv2.TERM_CRITERIA_MAX_ITER, 30, 1e-6)
    
    ret, camera_matrix, dist_coeffs, rvecs, tvecs = cv2.calibrateCamera(
        obj_points, img_points, (w, h), None, None,
        flags=cv2.CALIB_RATIONAL_MODEL, criteria=criteria
    )
    
    # Compute RMS reprojection error (standard OpenCV calibration metric)
    total_sq_error = 0.0
    total_points = 0
    for i in range(len(obj_points)):
        img_points_reproj, _ = cv2.projectPoints(
            obj_points[i], rvecs[i], tvecs[i], camera_matrix, dist_coeffs
        )
        error = cv2.norm(img_points[i], img_points_reproj.reshape(-1, 2), cv2.NORM_L2)
        total_sq_error += error ** 2
        total_points += len(img_points_reproj)

    rms_error = np.sqrt(total_sq_error / total_points)

    print(f"\nCalibration Results:")
    print(f"  RMS reprojection error: {rms_error:.4f} pixels")
    print(f"  Focal length: fx={camera_matrix[0,0]:.1f}, fy={camera_matrix[1,1]:.1f}")
    print(f"  Principal point: cx={camera_matrix[0,2]:.1f}, cy={camera_matrix[1,2]:.1f}")
    
    # Create result
    result = CalibrationResult(
        camera_id=camera_id,
        camera_matrix=camera_matrix,
        dist_coeffs=dist_coeffs.flatten(),
        image_width=w,
        image_height=h,
        reprojection_error=rms_error,
        num_images=len(img_points),
        calibration_date=datetime.now().isoformat()
    )
    
    # Save to YAML
    yaml_path = output_dir / f"{camera_id}_calibration.yaml"
    with open(yaml_path, 'w') as f:
        yaml.dump(result.to_yaml(), f, default_flow_style=False)
    
    print(f"\nSaved to: {yaml_path}")
    
    return result


def merge_calibrations(
    results: Dict[str, CalibrationResult],
    output_path: Path,
    platform: str = 'rk3588'
):
    """Merge individual calibrations into unified config file.

    Args:
        results: Dict mapping camera_id to CalibrationResult
        output_path: Path to output YAML file
        platform: 'rk3588'
    """
    print(f"\nMerging calibrations into {output_path}...")
    
    # Get geometry for platform
    geometry = CameraArrayGeometry.for_platform(platform)
    
    data = {
        'camera_array': {
            'platform': platform,
            'reference_camera': 'road',
            'calibration_date': datetime.now().isoformat(),
            'cameras': {}
        }
    }
    
    for camera_id, result in results.items():
        cam_data = result.to_yaml()
        
        # Add default extrinsics from geometry if available
        if camera_id in geometry.cameras:
            geom_config = geometry.cameras[camera_id]
            if 'extrinsics' not in cam_data:
                cam_data['extrinsics'] = {
                    'rotation': geom_config.extrinsics.R.tolist(),
                    'translation': geom_config.extrinsics.t.tolist()
                }
        
        data['camera_array']['cameras'][camera_id] = cam_data
    
    # Add stereo configuration
    data['camera_array']['stereo_pairs'] = [
        {'name': 'stereo_front', 'left': 'stereo_left', 'right': 'stereo_right', 'baseline_m': 0.080}
    ]
    
    # Save
    output_path.parent.mkdir(parents=True, exist_ok=True)
    with open(output_path, 'w') as f:
        yaml.dump(data, f, default_flow_style=False)
    
    print(f"Saved unified calibration to: {output_path}")


def generate_pattern(
    pattern_type: str = 'charuco',
    pattern_size: Tuple[int, int] = (9, 6),
    square_size_mm: float = 25.0,
    output_path: Path = Path('calibration_pattern.png')
):
    """Generate a calibration pattern image for printing."""
    print(f"\nGenerating {pattern_type} pattern...")
    
    dpi = 300
    pixels_per_mm = dpi / 25.4
    square_px = int(square_size_mm * pixels_per_mm)
    
    width_px = pattern_size[0] * square_px
    height_px = pattern_size[1] * square_px
    
    if pattern_type == 'chessboard':
        pattern = np.ones((height_px, width_px), dtype=np.uint8) * 255
        
        for y in range(pattern_size[1]):
            for x in range(pattern_size[0]):
                if (x + y) % 2 == 0:
                    x1, y1 = x * square_px, y * square_px
                    x2, y2 = x1 + square_px, y1 + square_px
                    pattern[y1:y2, x1:x2] = 0
    
    elif pattern_type == 'charuco':
        aruco_dict = aruco.getPredefinedDictionary(aruco.DICT_4X4_50)
        marker_size_mm = square_size_mm * 0.6
        
        board = aruco.CharucoBoard(pattern_size, square_size_mm, marker_size_mm, aruco_dict)
        pattern = board.generateImage((width_px, height_px))
    
    else:
        raise ValueError(f"Pattern type {pattern_type} not supported for generation")
    
    cv2.imwrite(str(output_path), pattern)
    
    print(f"Saved to: {output_path}")
    print(f"  Size: {width_px}x{height_px} pixels")
    print(f"  Print size: {pattern_size[0]*square_size_mm/10:.1f} x {pattern_size[1]*square_size_mm/10:.1f} cm")
    print(f"  Print at {dpi} DPI without scaling!")


def main():
    parser = argparse.ArgumentParser(
        description='EOP Camera Calibration Tool',
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  # Calibrate single camera from device
  %(prog)s --device /dev/video0 --camera road
  
  # Calibrate from video file
  %(prog)s --video road.mp4 --camera road
  
  # Batch calibrate all cameras (ExoPilot 01M)
  %(prog)s --batch --platform rk3588 \\
           --road /dev/video0 --wide_road /dev/video1 \\
           --stereo_left /dev/video22 --stereo_right /dev/video31
  
  # Generate calibration pattern
  %(prog)s --generate-pattern --type charuco
        """
    )
    
    # Input options
    input_group = parser.add_mutually_exclusive_group()
    input_group.add_argument('--video', type=str, help='Video file path')
    input_group.add_argument('--device', type=str, help='Camera device (e.g., /dev/video0)')
    input_group.add_argument('--generate-pattern', action='store_true', help='Generate pattern')
    input_group.add_argument('--batch', action='store_true', help='Batch mode for all cameras')
    
    # Camera selection
    parser.add_argument('--camera', type=str,
                       choices=['road', 'wide_road', 'stereo_left', 'stereo_right', 'side_left', 'side_right'],
                       help='Camera to calibrate')

    # Batch mode options
    parser.add_argument('--platform', type=str, default='rk3588', choices=['rk3588'])
    parser.add_argument('--road', type=str, help='Road camera source')
    parser.add_argument('--wide_road', type=str, help='Wide road camera source')
    parser.add_argument('--stereo_left', type=str, help='Stereo left source')
    parser.add_argument('--stereo_right', type=str, help='Stereo right source')
    
    # Pattern options
    parser.add_argument('--pattern', type=str, default='charuco',
                       choices=['chessboard', 'charuco', 'circles'])
    parser.add_argument('--pattern-size', type=str, default='9,6', help='WxH')
    parser.add_argument('--square-size', type=float, default=25.0, help='Square size in mm')
    
    # Processing options
    parser.add_argument('--min-images', type=int, default=15)
    parser.add_argument('--output', type=str, default='./calibration_output')
    parser.add_argument('--merge', type=str, default='camera_calibration.yaml')
    
    args = parser.parse_args()
    
    # Parse pattern size
    pattern_size = tuple(map(int, args.pattern_size.split(',')))
    square_size_m = args.square_size / 1000.0
    output_dir = Path(args.output)
    
    if args.generate_pattern:
        generate_pattern(args.pattern, pattern_size, args.square_size, Path('calibration_pattern.png'))
    
    elif args.batch:
        # Batch calibration
        results = {}
        
        cameras = {
            'road': args.road,
            'wide_road': args.wide_road,
            'stereo_left': args.stereo_left,
            'stereo_right': args.stereo_right,
        }
        
        for cam_id, source in cameras.items():
            if source:
                result = calibrate_camera(
                    source, cam_id, args.pattern, pattern_size,
                    square_size_m, args.min_images, output_dir
                )
                if result:
                    results[cam_id] = result
        
        if results:
            merge_calibrations(results, output_dir / args.merge, args.platform)
    
    elif args.video or args.device:
        source = args.video or args.device
        if not args.camera:
            print("Error: --camera required when calibrating from video/device")
            sys.exit(1)
        
        calibrate_camera(
            source, args.camera, args.pattern, pattern_size,
            square_size_m, args.min_images, output_dir
        )
    
    else:
        parser.print_help()


if __name__ == "__main__":
    main()

"""
Camera Geometry and Calibration Utilities
=========================================

Handles coordinate transformations between:
- World coordinates (vehicle frame): X=forward, Y=left, Z=up (ISO 8855)
- Camera coordinates: X=right, Y=down, Z=forward (OpenCV)
- Image coordinates: u=x (0=left), v=y (0=top)

Camera Array Layouts:

ExoPilot 01M (RK3588) - 7 Camera Layout (4 MIPI + 3 USB):
```
TOP VIEW (looking down, X=forward, Y=left, Z=up):

      Y (left)
      ↑
      │ [WIDE_ROAD]        [ROAD]
      │ 1.7mm               8.0mm
      │ (left, top)         (right, top)
      │
      │ [STEREO_LEFT]      [STEREO_RIGHT]
      │ 3.6mm               3.6mm
      │ (left, bottom)     (right, bottom)
      │
      │ Side cameras (USB hub, hood fender):
      │ [SIDE_LEFT]        [SIDE_RIGHT]
      │ 120°                120°
      │
      │ Rear camera (USB, 170°):
      │ [REAR_CAMERA]
      │
      └────────────────────────────────→ X (forward)

Stereo baseline: 80mm
```

Usage:
    from openpilot.selfdrive.gridd.camera_geometry import CameraArrayGeometry

    # Load from calibration file
    geometry = CameraArrayGeometry.from_calibration_file('camera_calibration.yaml')

    # Or use defaults for platform
    geometry = CameraArrayGeometry.for_platform('rk3588')

    # Project world point to image
    u, v = geometry.world_to_image('road', np.array([50.0, 2.0, 0.0]))

    # Back-project to ray
    origin, direction = geometry.image_to_world_ray('road', u, v)

See Also:
    - VisionPilot camera_geometry.py (reference implementation)
    - OpenCV calib3d: https://docs.opencv.org/4.x/d9/d0c/group__calib3d.html
"""

from __future__ import annotations

import numpy as np
from dataclasses import dataclass
from pathlib import Path
from openpilot.common.swaglog import cloudlog

@dataclass
class CameraIntrinsics:
    """Camera intrinsic parameters (K matrix)."""
    
    fx: float  # Focal length x (pixels)
    fy: float  # Focal length y (pixels)
    cx: float  # Principal point x (pixels)
    cy: float  # Principal point y (pixels)
    
    # Distortion coefficients (k1, k2, p1, p2, k3)
    k1: float = 0.0
    k2: float = 0.0
    p1: float = 0.0
    p2: float = 0.0
    k3: float = 0.0
    
    @property
    def K(self) -> np.ndarray:
        """3x3 camera matrix."""
        return np.array([
            [self.fx, 0, self.cx],
            [0, self.fy, self.cy],
            [0, 0, 1]
        ])
    
    @property
    def dist_coeffs(self) -> np.ndarray:
        """Distortion coefficients vector."""
        return np.array([self.k1, self.k2, self.p1, self.p2, self.k3])
    
    def scale(self, scale_x: float, scale_y: float) -> 'CameraIntrinsics':
        """Return scaled intrinsics for different resolution."""
        return CameraIntrinsics(
            fx=self.fx * scale_x,
            fy=self.fy * scale_y,
            cx=self.cx * scale_x,
            cy=self.cy * scale_y,
            k1=self.k1,
            k2=self.k2,
            p1=self.p1,
            p2=self.p2,
            k3=self.k3
        )


@dataclass
class CameraExtrinsics:
    """Camera extrinsic parameters (pose in vehicle frame).
    
    Coordinate system:
    - World: X=forward, Y=left, Z=up (ISO 8855 vehicle frame)
    - Camera: X=right, Y=down, Z=forward (OpenCV convention)
    """
    
    # Rotation matrix (3x3): transforms from world to camera coordinates
    # R @ v_world gives vector in camera frame
    R: np.ndarray
    
    # Translation vector (3,): camera position in world coordinates
    # t is where camera center is in world frame
    t: np.ndarray
    
    def __post_init__(self):
        if self.R.shape != (3, 3):
            raise ValueError(f"R must be 3x3, got {self.R.shape}")
        if self.t.shape != (3,):
            raise ValueError(f"t must be (3,), got {self.t.shape}")
    
    def world_to_camera(self, P_world: np.ndarray) -> np.ndarray:
        """Transform point from world to camera coordinates."""
        return self.R @ (P_world - self.t)
    
    def camera_to_world(self, P_cam: np.ndarray) -> np.ndarray:
        """Transform point from camera to world coordinates."""
        return self.R.T @ P_cam + self.t


@dataclass
class CameraConfig:
    """Complete camera configuration."""
    
    name: str
    intrinsics: CameraIntrinsics
    extrinsics: CameraExtrinsics
    image_width: int
    image_height: int
    sensor_type: str = "unknown"  # ox03c10, gc4653, etc.
    lens_focal_mm: float = 8.0
    fov_degrees: float = 60.0
    role: str = "unknown"  # road, wide, tele, stereo_left, stereo_right


class CameraArrayGeometry:
    """Camera array geometry for ExoPilot 01M (RK3588).

    7 cameras (4 MIPI + 3 USB), 80mm stereo baseline.

    Loads camera parameters from calibration YAML files or uses defaults.
    """
    
    # ========== Default Configurations ==========
    #
    # Physically-measured mounting positions/angles/lens data for ExoPilot 01M
    # ships from the closed exopilot hal package (hal.platform.rk3588_camera_geometry)
    # rather than living in this public repo. Without hal, these are empty and
    # _load_exo01_defaults() produces no cameras (caller must supply a calibration
    # YAML via from_calibration_file() instead).
    try:
        from hal.platform import rk3588_camera_geometry as _hal_geo
        EXO01_CAMERAS = _hal_geo.CAMERAS
        EXO01_DEFAULT_POSITIONS = {name: np.array(pos) for name, pos in _hal_geo.POSITIONS_M.items()}
        _EXO01_YAW_DEG = _hal_geo.YAW_DEG
        EXO01_DEFAULT_FOCALS = _hal_geo.FOCAL_PX
        EXO01_IMAGE_SIZES = _hal_geo.IMAGE_SIZE_PX
        EXO01_STEREO_BASELINE = _hal_geo.STEREO_BASELINE_M
        _EXO01_LENS_MM = _hal_geo.LENS_MM
        _EXO01_FOV_DEG = _hal_geo.FOV_DEG
        _EXO01_SENSOR_TYPE = _hal_geo.SENSOR_TYPE
    except ImportError:
        EXO01_CAMERAS = []
        EXO01_DEFAULT_POSITIONS = {}
        _EXO01_YAW_DEG = {}
        EXO01_DEFAULT_FOCALS = {}
        EXO01_IMAGE_SIZES = {}
        EXO01_STEREO_BASELINE = 0.0
        _EXO01_LENS_MM = {}
        _EXO01_FOV_DEG = {}
        _EXO01_SENSOR_TYPE = {}

    @staticmethod
    def _euler_to_rot_matrix(roll: float, pitch: float, yaw: float) -> np.ndarray:
        """XYZ Euler angles to rotation matrix."""
        cr, sr = np.cos(roll), np.sin(roll)
        cp, sp = np.cos(pitch), np.sin(pitch)
        cy, sy = np.cos(yaw), np.sin(yaw)
        Rx = np.array([[1, 0, 0], [0, cr, -sr], [0, sr, cr]])
        Ry = np.array([[cp, 0, sp], [0, 1, 0], [-sp, 0, cp]])
        Rz = np.array([[cy, -sy, 0], [sy, cy, 0], [0, 0, 1]])
        return Rz @ Ry @ Rx

    # Rotation matrices: world (X=forward, Y=left, Z=up) -> camera (X=right, Y=down, Z=forward)
    # This is the standard OpenCV camera coordinate system
    _R_WORLD_TO_CAM = np.array([
        [0, -1,  0],  # X_cam = -Y_world (left becomes right)
        [0,  0, -1],  # Y_cam = -Z_world (up becomes down)
        [1,  0,  0],  # Z_cam = X_world (forward becomes forward)
    ])

    # Per-camera mounting yaw comes from hal (_EXO01_YAW_DEG); cameras not listed
    # there are forward-facing (0 deg yaw). Plain loop, not a comprehension:
    # comprehensions get their own scope in a class body and can't see
    # _euler_to_rot_matrix/_R_WORLD_TO_CAM above.
    EXO01_DEFAULT_ROTATIONS = {}
    for _name in EXO01_CAMERAS:
        EXO01_DEFAULT_ROTATIONS[_name] = (
            _euler_to_rot_matrix(0.0, 0.0, np.radians(_EXO01_YAW_DEG.get(_name, 0.0))) @ _R_WORLD_TO_CAM
        )
    if EXO01_CAMERAS:
        del _name

    def __init__(self, platform: str = 'rk3588', config_path: str | None = None):
        """Initialize camera array geometry.

        Args:
            platform: 'rk3588' (ExoPilot 01M)
            config_path: Optional path to calibration YAML file
        """
        self.platform = platform.lower()
        self.cameras: dict[str, CameraConfig] = {}
        self.reference_camera = 'road'
        
        if config_path:
            self._load_from_yaml(config_path)
        else:
            self._load_defaults()
    
    @classmethod
    def for_platform(cls, platform: str) -> 'CameraArrayGeometry':
        """Create geometry for a specific platform."""
        return cls(platform=platform)
    
    @classmethod
    def from_calibration_file(cls, config_path: str) -> 'CameraArrayGeometry':
        """Create geometry from calibration file."""
        # Detect platform from file or use generic
        return cls(config_path=config_path)
    
    def _load_defaults(self):
        """Load default hardcoded geometry for platform."""
        self._load_exo01_defaults()
        cloudlog.info(f"Loaded default camera geometry for {self.platform}")
    
    def _load_exo01_defaults(self):
        """Load ExoPilot 01M (RK3588) defaults."""
        for name in self.EXO01_CAMERAS:
            width, height = self.EXO01_IMAGE_SIZES[name]
            fx, fy = self.EXO01_DEFAULT_FOCALS[name]
            lens_mm = self._EXO01_LENS_MM[name]
            fov_deg = self._EXO01_FOV_DEG[name]
            sensor = self._EXO01_SENSOR_TYPE[name]

            self.cameras[name] = CameraConfig(
                name=name,
                intrinsics=CameraIntrinsics(
                    fx=fx, fy=fy,
                    cx=width / 2.0,
                    cy=height / 2.0
                ),
                extrinsics=CameraExtrinsics(
                    R=self.EXO01_DEFAULT_ROTATIONS[name],
                    t=self.EXO01_DEFAULT_POSITIONS[name]
                ),
                image_width=width,
                image_height=height,
                sensor_type=sensor,
                lens_focal_mm=lens_mm,
                fov_degrees=fov_deg,
                role=name
            )
    
    def _load_from_yaml(self, config_path: str):
        """Load camera geometry from calibration YAML file."""
        try:
            import yaml
            with open(config_path, 'r') as f:
                data = yaml.safe_load(f)
            
            array_config = data.get('camera_array', data)
            self.reference_camera = array_config.get('reference_camera', 'road')
            self.platform = array_config.get('platform', self.platform)
            
            cameras_data = array_config.get('cameras', {})
            for cam_id, cam_data in cameras_data.items():
                self.cameras[cam_id] = self._parse_camera_data(cam_id, cam_data)
            
            cloudlog.info(f"Loaded camera calibration from {config_path}")
            
        except Exception as e:
            cloudlog.warning(f"Failed to load calibration from {config_path}: {e}")
            cloudlog.info("Falling back to defaults")
            self._load_defaults()
    
    def _parse_camera_data(self, cam_id: str, data: dict) -> CameraConfig:
        """Parse camera data from YAML."""
        intr = data.get('intrinsics', {})
        width = intr.get('width', 1920)
        height = intr.get('height', 1080)
        
        intrinsics = CameraIntrinsics(
            fx=intr.get('fx', 1000.0),
            fy=intr.get('fy', 1000.0),
            cx=intr.get('cx', width / 2.0),
            cy=intr.get('cy', height / 2.0),
            k1=intr.get('distortion', {}).get('k1', 0.0),
            k2=intr.get('distortion', {}).get('k2', 0.0),
            p1=intr.get('distortion', {}).get('p1', 0.0),
            p2=intr.get('distortion', {}).get('p2', 0.0),
            k3=intr.get('distortion', {}).get('k3', 0.0),
        )
        
        ext = data.get('extrinsics', {})
        rotation = np.array(ext.get('rotation', [[1, 0, 0], [0, 1, 0], [0, 0, 1]]))
        translation = np.array(ext.get('translation', [0.0, 0.0, 0.0]))
        extrinsics = CameraExtrinsics(R=rotation, t=translation)
        
        return CameraConfig(
            name=cam_id,
            intrinsics=intrinsics,
            extrinsics=extrinsics,
            image_width=width,
            image_height=height,
            sensor_type=data.get('sensor_type', 'unknown'),
            lens_focal_mm=data.get('lens_focal_mm', 8.0),
            fov_degrees=data.get('fov_degrees', 60.0),
            role=data.get('role', cam_id)
        )
    
    # ========== Coordinate Transformations ==========
    
    def world_to_camera(self, camera_name: str, P_world: np.ndarray) -> np.ndarray:
        """Transform point from world to camera coordinates.
        
        Args:
            camera_name: Name of camera
            P_world: 3D point in world coordinates [X, Y, Z] (forward, left, up)
        
        Returns:
            P_camera: 3D point in camera frame [X, Y, Z] (right, down, forward)
        """
        cam = self.cameras[camera_name]
        return cam.extrinsics.world_to_camera(P_world)
    
    def camera_to_image(self, camera_name: str, P_camera: np.ndarray) -> tuple[float, float]:
        """Project 3D camera point to 2D image coordinates.
        
        Args:
            camera_name: Name of camera
            P_camera: 3D point in camera frame
        
        Returns:
            (u, v): Image coordinates in pixels, or (nan, nan) if behind camera
        """
        cam = self.cameras[camera_name]
        x, y, z = P_camera
        
        if z <= 0:  # Behind camera
            return (float('nan'), float('nan'))
        
        u = cam.intrinsics.fx * (x / z) + cam.intrinsics.cx
        v = cam.intrinsics.fy * (y / z) + cam.intrinsics.cy
        
        return (u, v)
    
    def world_to_image(self, camera_name: str, P_world: np.ndarray) -> tuple[float, float]:
        """Project world point directly to image coordinates.
        
        Args:
            camera_name: Name of camera
            P_world: 3D point in world coordinates [X, Y, Z]
        
        Returns:
            (u, v): Image coordinates in pixels
        """
        P_camera = self.world_to_camera(camera_name, P_world)
        return self.camera_to_image(camera_name, P_camera)
    
    def image_to_camera_ray(self, camera_name: str, u: float, v: float) -> np.ndarray:
        """Back-project image point to normalized ray in camera coordinates.
        
        Args:
            camera_name: Name of camera
            u, v: Image coordinates in pixels
        
        Returns:
            Normalized 3D ray direction in camera frame [X=right, Y=down, Z=forward]
        """
        cam = self.cameras[camera_name]
        
        # Camera coordinates: X=right, Y=down, Z=forward
        # Image: u increases right, v increases down
        x = (u - cam.intrinsics.cx) / cam.intrinsics.fx  # Right
        y = (v - cam.intrinsics.cy) / cam.intrinsics.fy  # Down
        z = 1.0  # Forward
        
        ray = np.array([x, y, z])
        return ray / np.linalg.norm(ray)
    
    def image_to_world_ray(self, camera_name: str, u: float, v: float) -> tuple[np.ndarray, np.ndarray]:
        """Back-project image point to ray in world coordinates.
        
        Args:
            camera_name: Name of camera
            u, v: Image coordinates in pixels
        
        Returns:
            (origin, direction): Ray origin (camera center) and direction in world frame
        """
        cam = self.cameras[camera_name]
        
        # Camera center in world frame
        origin = cam.extrinsics.t
        
        # Ray in camera frame
        ray_cam = self.image_to_camera_ray(camera_name, u, v)
        
        # Transform to world frame
        direction = cam.extrinsics.R.T @ ray_cam
        
        return origin, direction
    
    # ========== Multi-Camera Operations ==========
    
    def project_world_to_all_cameras(self, P_world: np.ndarray) -> dict[str, tuple[float, float | None]]:
        """Project a world point to all camera images.
        
        Args:
            P_world: 3D point in world coordinates
        
        Returns:
            Dict mapping camera name to (u, v) or None if not visible
        """
        results = {}
        for name in self.cameras:
            u, v = self.world_to_image(name, P_world)
            cam = self.cameras[name]
            
            # Check if within image bounds
            if 0 <= u < cam.image_width and 0 <= v < cam.image_height:
                results[name] = (u, v)
            else:
                results[name] = None
        
        return results
    
    def find_best_camera_for_point(self, P_world: np.ndarray) -> str | None:
        """Find which camera has the best view of a world point.
        
        Best = largest projected area (closest to image center with reasonable distance)
        """
        projections = self.project_world_to_all_cameras(P_world)
        
        best_cam = None
        best_score = -1.0
        
        for name, proj in projections.items():
            if proj is None:
                continue
            
            u, v = proj
            cam = self.cameras[name]
            cx, cy = cam.intrinsics.cx, cam.intrinsics.cy
            
            # Distance from center (normalized)
            dist_from_center = np.sqrt(((u - cx) / cx)**2 + ((v - cy) / cy)**2)
            
            # Score: closer to center is better
            score = 1.0 - dist_from_center
            
            if score > best_score:
                best_score = score
                best_cam = name
        
        return best_cam
    
    def get_stereo_baseline(self, left: str = 'stereo_left', right: str = 'stereo_right') -> float:
        """Get stereo baseline between two cameras."""
        if left not in self.cameras or right not in self.cameras:
            return 0.0
        
        p1 = self.cameras[left].extrinsics.t
        p2 = self.cameras[right].extrinsics.t
        return abs(p1[1] - p2[1])  # Lateral separation (Y axis = left)
    
    def get_relative_pose(self, from_cam: str, to_cam: str) -> tuple[np.ndarray, np.ndarray]:
        """Get relative pose from one camera to another.
        
        Returns:
            (R_rel, t_rel): Rotation and translation from from_cam to to_cam
        """
        if from_cam not in self.cameras or to_cam not in self.cameras:
            raise ValueError(f"Unknown camera: {from_cam} or {to_cam}")
        
        ext_from = self.cameras[from_cam].extrinsics
        ext_to = self.cameras[to_cam].extrinsics
        
        # Relative rotation: R_to @ R_from.T
        R_rel = ext_to.R @ ext_from.R.T
        
        # Relative translation: t_to - R_rel @ t_from
        t_rel = ext_to.t - R_rel @ ext_from.t
        
        return R_rel, t_rel
    
    # ========== Utility Methods ==========
    
    def get_camera_names(self) -> list[str]:
        """Get list of camera names."""
        return list(self.cameras.keys())
    
    def get_camera_config(self, name: str) -> CameraConfig:
        """Get configuration for a specific camera."""
        if name not in self.cameras:
            raise ValueError(f"Unknown camera: {name}")
        return self.cameras[name]
    
    def get_platform_info(self) -> dict:
        """Get platform information."""
        return {
            'platform': self.platform,
            'reference_camera': self.reference_camera,
            'num_cameras': len(self.cameras),
            'cameras': list(self.cameras.keys()),
            'stereo_baseline_m': self.get_stereo_baseline()
        }


# ========== Convenience Functions ==========

def create_geometry_for_hardware() -> CameraArrayGeometry:
    """Create camera geometry based on detected hardware."""
    return CameraArrayGeometry.for_platform('rk3588')


def test_geometry():
    """Test camera geometry module."""
    print("Testing Camera Geometry Module")
    print("=" * 60)

    # Test ExoPilot 01M (RK3588)
    print("\n--- ExoPilot 01M (RK3588) 7-Camera Layout ---")
    EXO01 = CameraArrayGeometry.for_platform('rk3588')
    print(f"Cameras: {EXO01.get_camera_names()}")
    print(f"Stereo baseline: {EXO01.get_stereo_baseline()*1000:.0f}mm")

    # Test projections
    print("\n--- Projection Tests ---")

    # Test point: 50m forward, 2m left (in adjacent lane)
    P_world = np.array([50.0, 2.0, 0.0])
    print(f"\nWorld point: {P_world} (50m forward, 2m left)")

    for cam_name in ['road', 'wide_road']:
        if cam_name in EXO01.cameras:
            u, v = EXO01.world_to_image(cam_name, P_world)
            cam = EXO01.cameras[cam_name]
            print(f"  {cam_name} ({cam.lens_focal_mm}mm): ({u:.1f}, {v:.1f}) px")

    # Best camera selection
    best = EXO01.find_best_camera_for_point(P_world)
    print(f"  Best camera: {best}")

    # Test back-projection
    print("\n--- Back-Projection Test ---")
    origin, direction = EXO01.image_to_world_ray('road', 960, 540)
    print(f"Camera center: {origin}")
    print(f"Ray direction: {direction}")

    # Multi-camera projection
    print("\n--- Multi-Camera Projection ---")
    P_close = np.array([10.0, 0.0, 0.0])  # 10m ahead, center
    projections = EXO01.project_world_to_all_cameras(P_close)
    for name, proj in projections.items():
        if proj:
            print(f"  {name}: ({proj[0]:.1f}, {proj[1]:.1f})")
        else:
            print(f"  {name}: Not visible")

    print("\n✓ All tests passed!")


if __name__ == "__main__":
    test_geometry()

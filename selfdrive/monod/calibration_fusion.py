#!/usr/bin/env python3
"""
calibration_fusion.py - Camera Calibration and Multi-Source Fusion

Calibrates monod detections against:
1. drive_vision.rknn model output (ground truth road geometry)
2. stereod depth output (stereo disparity)
3. liveCalibration (camera extrinsics)

Provides accurate 3D positioning by fusing:
- Monod multi-camera YOLO (Hailo-8)
- Drive vision neural output (RKNN NPU)
- Stereo depth (SGBM + SceneSeg)

Calibration Process:
1. Static calibration: Camera intrinsics/extrinsics from liveCalibration
2. Dynamic calibration: Online alignment with drive_vision objects
3. Depth fusion: Stereo depth refinement for monod detections
4. Temporal smoothing: Kalman filtering for stable positions

Author: EnhancedOpenPilot Team
"""
import numpy as np
from dataclasses import dataclass

from openpilot.selfdrive.gridd.camera_geometry import CameraArrayGeometry
from openpilot.system.hardware.camera_geometry import CameraGeometry, CameraPosition
from openpilot.common.swaglog import cloudlog

@dataclass
class CalibrationState:
    """Calibration state for a camera with ExoPilot geometry."""
    # Intrinsics
    focal_x: float = 800.0
    focal_y: float = 800.0
    center_x: float = 960.0
    center_y: float = 540.0

    # Extrinsics (camera to car)
    cam_to_car: np.ndarray = None

    # Distortion (simplified radial)
    k1: float = 0.0
    k2: float = 0.0

    # Dynamic calibration offset
    depth_scale: float = 1.0  # Scale factor for depth estimates
    lateral_offset: float = 0.0  # Lateral bias correction

    def __post_init__(self):
        if self.cam_to_car is None:
            self.cam_to_car = np.eye(4)


@dataclass
class FusedObject3D:
    """Fused 3D object from multiple sources."""
    track_id: int
    class_name: str
    confidence: float

    # Fused position (road frame)
    x: float  # forward
    y: float  # left
    z: float  # up

    # Uncertainty (covariance)
    sigma_x: float
    sigma_y: float
    sigma_z: float

    # Sources
    has_monod: bool
    has_drivevision: bool
    has_stereo: bool

    # Source-specific data
    monod_distance: float = 0.0
    drivevision_distance: float = 0.0
    stereo_distance: float = 0.0

    # Velocity
    vx: float = 0.0
    vy: float = 0.0


class CameraCalibrationManager:
    """Manages calibration for all mono cameras using HAL CameraGeometry."""

    def __init__(self, geometry: CameraGeometry = None):
        self.calibrations: dict[str, CalibrationState] = {}
        # Use provided geometry or auto-detect from hardware
        self.geometry = geometry or self._load_geometry_from_hal()
        self._init_calibrations()

    def _load_geometry_from_hal(self) -> CameraGeometry:
        """Load camera geometry from HAL (hardware abstraction layer)."""
        try:
            from openpilot.system.hardware.registry import PlatformRegistry
            hardware = PlatformRegistry.create()
            return hardware.get_camera_geometry()
        except Exception as e:
            cloudlog.warning(f"Failed to load geometry from HAL: {e}, using ExoPilot 01M default")
            return CameraArrayGeometry.for_platform("rk3588")

    def _init_calibrations(self):
        """Initialize calibrations from HAL geometric layout."""
        positions = self.geometry.positions

        # Wide camera (1.7mm)
        if 'wide_road' in positions:
            wide = positions['wide_road']
            self.calibrations['wide_road'] = CalibrationState(
                focal_x=300.0, focal_y=300.0,
                center_x=960.0, center_y=540.0,
                cam_to_car=self._get_camera_transform(wide)
            )

        # Road camera (8mm)
        if 'road' in positions:
            road = positions['road']
            self.calibrations['road'] = CalibrationState(
                focal_x=800.0, focal_y=800.0,
                center_x=960.0, center_y=540.0,
                cam_to_car=self._get_camera_transform(road)
            )

    def _get_camera_transform(self, position: CameraPosition) -> np.ndarray:
        """Get camera to car transform from position vector."""
        transform = np.eye(4)
        transform[0:3, 3] = position
        return transform

    def get_relative_transform(self, from_cam: str, to_cam: str) -> np.ndarray:
        """
        Get relative transform from one camera to another.
        Used for cross-camera projection and epipolar matching.
        """
        from_pos = self.camera_positions.get(from_cam, np.zeros(3))
        to_pos = self.camera_positions.get(to_cam, np.zeros(3))

        # Transform: from_cam → car → to_cam
        transform = np.eye(4)
        transform[0:3, 3] = to_pos - from_pos
        return transform

    def project_between_cameras(self,
                                 uv_src: tuple[float, float],
                                 depth: float,
                                 cam_src: str,
                                 cam_dst: str) -> tuple[float, float | None]:
        """
        Project a point from source camera to destination camera.

        Args:
            uv_src: (u, v) in normalized coordinates [0, 1]
            depth: distance along optical axis (meters)
            cam_src: source camera name
            cam_dst: destination camera name

        Returns:
            (u, v) in destination camera, or None if outside FOV
        """
        if cam_src not in self.calibrations or cam_dst not in self.calibrations:
            return None

        # Get camera positions
        self.camera_positions.get(cam_src, np.zeros(3))
        self.camera_positions.get(cam_dst, np.zeros(3))

        # Get source calibration
        calib_src = self.calibrations[cam_src]

        # Back-project to 3D in source camera frame
        u_px = uv_src[0] * 1920  # Assuming 1920 width
        v_px = uv_src[1] * 1080  # Assuming 1080 height

        x_cam = (u_px - calib_src.center_x) / calib_src.focal_x * depth
        y_cam = (v_px - calib_src.center_y) / calib_src.focal_y * depth
        z_cam = depth

        # Transform to car frame
        point_cam = np.array([x_cam, y_cam, z_cam, 1.0])
        point_car = calib_src.cam_to_car @ point_cam

        # Transform to destination camera frame
        calib_dst = self.calibrations[cam_dst]
        cam_to_car_inv = np.linalg.inv(calib_dst.cam_to_car)
        point_dst_cam = cam_to_car_inv @ point_car

        # Project to destination image
        if point_dst_cam[2] <= 0:
            return None  # Behind camera

        u_dst = point_dst_cam[0] / point_dst_cam[2] * calib_dst.focal_x + calib_dst.center_x
        v_dst = point_dst_cam[1] / point_dst_cam[2] * calib_dst.focal_y + calib_dst.center_y

        # Normalize
        u_dst_norm = u_dst / 1920
        v_dst_norm = v_dst / 1080

        # Check bounds
        if not (0 <= u_dst_norm <= 1 and 0 <= v_dst_norm <= 1):
            return None

        return (u_dst_norm, v_dst_norm)

    def update_from_live_calibration(self, live_calib):
        """Update calibrations from live calibration message."""
        if live_calib is None:
            return

        # Update road camera calibration
        if hasattr(live_calib, 'extrinsicMatrix'):
            # Convert flat matrix to 4x4
            extrinsic = np.array(live_calib.extrinsicMatrix).reshape(4, 4)
            self.calibrations['road'].cam_to_car = extrinsic

        # Update based on rpy (roll, pitch, yaw)
        if hasattr(live_calib, 'rpy'):
            rpy = live_calib.rpy
            # Apply small angle corrections
            for _name, calib in self.calibrations.items():
                # Simple pitch correction for distance scale
                pitch_correction = 1.0 + rpy[1] * 0.1  # 10% per radian
                calib.depth_scale = pitch_correction

    def image_to_car(self, camera: str, u: float, v: float, distance: float) -> tuple[float, float, float]:
        """Convert image coordinates to car coordinates."""
        if camera not in self.calibrations:
            return 0.0, 0.0, 0.0

        calib = self.calibrations[camera]

        # Undistort and normalize
        u_norm = (u * 1920 - calib.center_x) / calib.focal_x
        v_norm = (v * 1080 - calib.center_y) / calib.focal_y

        # Apply distance scale correction
        distance_corrected = distance * calib.depth_scale

        # Camera coordinates: z is forward, x is right, y is down
        x_cam = u_norm * distance_corrected
        y_cam = v_norm * distance_corrected
        z_cam = distance_corrected

        # Transform to car coordinates
        point_cam = np.array([x_cam, y_cam, z_cam, 1.0])
        point_car = calib.cam_to_car @ point_cam

        # Car coordinates: x forward, y left, z up
        return point_car[0], -point_car[1], -point_car[2]  # Flip y and z for car frame


class DriveVisionFusion:
    """Fuse monod detections with drive_vision model output."""

    def __init__(self):
        self.drive_objects: list[dict] = []
        self.lead_objects: list[dict] = []

        # Known object dimensions from drive_vision training
        self.DV_DIMENSIONS = {
            'car': {'width': 1.8, 'height': 1.5, 'length': 4.5},
            'truck': {'width': 2.5, 'height': 2.5, 'length': 12.0},
            'bus': {'width': 2.5, 'height': 2.8, 'length': 12.0},
            'motorcycle': {'width': 0.8, 'height': 1.2, 'length': 2.0},
            'bicycle': {'width': 0.6, 'height': 1.0, 'length': 1.8},
            'person': {'width': 0.5, 'height': 1.7, 'length': 0.3},
        }

    def update_drive_vision(self, model_v2):
        """Update with latest drive_vision output."""
        self.drive_objects = []
        self.lead_objects = []

        if model_v2 is None:
            return

        # Parse leads from modelV2
        if hasattr(model_v2, 'leads'):
            for lead in model_v2.leads:
                if lead.status:
                    self.lead_objects.append({
                        'x': lead.x[0],  # Forward position
                        'y': lead.y[0],  # Lateral position
                        'v': lead.v[0],  # Velocity
                        'prob': lead.prob,
                    })

        # Parse lane lines for road geometry
        # This helps validate monod object positions

    def find_matching_drivevision_object(self, monod_obj) -> dict | None:
        """Find matching drive_vision object for a monod detection."""
        best_match = None
        best_dist = float('inf')

        for dv_obj in self.lead_objects:
            # Distance in road frame
            dist = np.sqrt(
                (monod_obj.x_road - dv_obj['x'])**2 +
                (monod_obj.y_road - dv_obj['y'])**2
            )

            # Match if within 5 meters and similar position
            if dist < 5.0 and dist < best_dist:
                best_dist = dist
                best_match = dv_obj

        return best_match

    def calibrate_monod_distance(self, monod_obj) -> float:
        """Calibrate monod distance using drive_vision reference."""
        match = self.find_matching_drivevision_object(monod_obj)

        if match is None:
            # No match, use monod estimate
            return monod_obj.distance_m

        # Use drive_vision distance as ground truth
        dv_distance = match['x']

        # Calculate correction factor
        if monod_obj.distance_m > 0:
            correction = dv_distance / monod_obj.distance_m
            # Apply partial correction (trust drive_vision more)
            calibrated = monod_obj.distance_m * (0.7 * correction + 0.3)
            return calibrated

        return dv_distance


class StereoDepthFusion:
    """Refine monod positions using stereo depth."""

    def __init__(self):
        self.stereo_points: np.ndarray = None
        self.has_stereo = False

    def update_stereo(self, gridd_msg):
        """Update with latest stereo depth from gridd."""
        if gridd_msg is None:
            self.has_stereo = False
            return

        # Extract stereo point cloud if available
        if hasattr(gridd_msg, 'stereoGround'):
            self.stereo_points = gridd_msg.stereoGround
            self.has_stereo = True

    def query_depth_at(self, x: float, y: float) -> float | None:
        """Query stereo depth at a road-frame position."""
        if not self.has_stereo or self.stereo_points is None:
            return None

        # Find nearest stereo point
        # Simplified: search for point within radius

        # In production, use KD-tree for efficient search
        # For now, return None (fallback to monod)
        return None

    def refine_position(self, monod_obj) -> tuple[float, float, float]:
        """Refine monod position using stereo depth."""
        stereo_depth = self.query_depth_at(monod_obj.x_road, monod_obj.y_road)

        if stereo_depth is None:
            # No stereo data, use monod
            return monod_obj.x_road, monod_obj.y_road, monod_obj.z_road

        # Fuse depths
        # Weight by confidence (stereo is more accurate for close objects)
        if monod_obj.distance_m < 50.0:
            # Close range: trust stereo more
            weight_stereo = 0.7
            weight_monod = 0.3
        else:
            # Far range: monod is better (stereo limited)
            weight_stereo = 0.2
            weight_monod = 0.8

        fused_x = weight_stereo * stereo_depth + weight_monod * monod_obj.x_road

        return fused_x, monod_obj.y_road, monod_obj.z_road


class TemporalSmoother:
    """Kalman filter-based temporal smoothing for object tracks."""

    def __init__(self):
        self.tracks: dict[int, dict] = {}

    def update(self, track_id: int, measurement: tuple[float, float, float]) -> tuple[float, float, float]:
        """Update track with new measurement, return smoothed position."""
        if track_id not in self.tracks:
            # Initialize new track
            self.tracks[track_id] = {
                'x': measurement[0],
                'y': measurement[1],
                'z': measurement[2],
                'vx': 0.0,
                'vy': 0.0,
                'P': np.eye(4) * 10.0,  # Covariance
            }
            return measurement

        track = self.tracks[track_id]
        dt = 0.05  # 20 Hz

        # Prediction
        track['x'] += track['vx'] * dt
        track['y'] += track['vy'] * dt

        # Kalman gain (simplified)
        np.eye(2) * 0.1  # Process noise
        np.eye(2) * 2.0  # Measurement noise

        # Update
        z = np.array([measurement[0], measurement[1]])
        x_pred = np.array([track['x'], track['y']])

        # Innovation
        y = z - x_pred

        # Simplified Kalman update
        K = 0.3  # Fixed gain for simplicity
        track['x'] += K * y[0]
        track['y'] += K * y[1]

        # Update velocity
        track['vx'] = (measurement[0] - track['x']) / dt
        track['vy'] = (measurement[1] - track['y']) / dt

        return track['x'], track['y'], track['z']


class CalibrationFusion:
    """Main calibration and fusion engine."""

    def __init__(self):
        self.calibration = CameraCalibrationManager()
        self.dv_fusion = DriveVisionFusion()
        self.stereo_fusion = StereoDepthFusion()
        self.temporal = TemporalSmoother()

        cloudlog.info("CalibrationFusion initialized")

    def update_references(self, live_calib, model_v2, gridd_msg):
        """Update all reference data sources."""
        # Update camera calibrations
        self.calibration.update_from_live_calibration(live_calib)

        # Update drive_vision objects
        self.dv_fusion.update_drive_vision(model_v2)

        # Update stereo depth
        self.stereo_fusion.update_stereo(gridd_msg)

    def fuse_detection(self, monod_obj) -> FusedObject3D:
        """Fuse a single monod detection with all available sources."""
        # Step 1: Calibrate distance using drive_vision
        calibrated_distance = self.dv_fusion.calibrate_monod_distance(monod_obj)

        # Step 2: Recompute 3D position with calibrated distance
        x, y, z = self.calibration.image_to_car(
            monod_obj.lens.camera_name,
            monod_obj.u,
            monod_obj.v,
            calibrated_distance
        )

        # Step 3: Refine with stereo depth (if available)
        stereo_distance = 0.0
        if self.stereo_fusion.has_stereo:
            stereo_depth = self.stereo_fusion.query_depth_at(monod_obj.x_road, monod_obj.y_road)
            if stereo_depth is not None:
                stereo_distance = float(stereo_depth)
            x, y, z = self.stereo_fusion.refine_position(monod_obj)

        # Step 4: Temporal smoothing
        x_smooth, y_smooth, z_smooth = self.temporal.update(monod_obj.track_id, (x, y, z))

        # Check for drive_vision match
        dv_match = self.dv_fusion.find_matching_drivevision_object(monod_obj)
        has_dv = dv_match is not None

        # Compute uncertainty
        sigma_x = 0.5 if has_dv else 1.5  # Lower uncertainty if drive_vision confirms
        sigma_y = 0.3 if has_dv else 1.0
        sigma_z = 0.5

        return FusedObject3D(
            track_id=monod_obj.track_id,
            class_name=monod_obj.class_name,
            confidence=monod_obj.confidence * (1.2 if has_dv else 1.0),
            x=x_smooth,
            y=y_smooth,
            z=z_smooth,
            sigma_x=sigma_x,
            sigma_y=sigma_y,
            sigma_z=sigma_z,
            has_monod=True,
            has_drivevision=has_dv,
            has_stereo=self.stereo_fusion.has_stereo,
            monod_distance=monod_obj.distance_m,
            drivevision_distance=dv_match['x'] if has_dv else 0.0,
            stereo_distance=stereo_distance,
            vx=monod_obj.vx_mps,
            vy=monod_obj.vy_mps,
        )

    def fuse_all(self, monod_detections: list) -> list[FusedObject3D]:
        """Fuse all monod detections."""
        fused = []
        for det in monod_detections:
            try:
                fused_obj = self.fuse_detection(det)
                fused.append(fused_obj)
            except Exception as e:
                cloudlog.error(f"Fusion failed for track {det.track_id}: {e}")

        return fused

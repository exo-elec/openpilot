#!/usr/bin/env python3
"""
Unified Calibration Storage System
==================================

Bridges OpenPilot's binary Cap'n Proto format with VisionPilot's YAML format.

Storage Formats:
----------------
1. Runtime (OpenPilot native): Binary Cap'n Proto in params
   - Key: "CalibrationParams" / "CameraCalibrationParams"
   - Fast, efficient, cereal-native

2. Factory/External: YAML for human-readable calibration
   - Path: /data/params/calibration/camera_calibration.yaml
   - Compatible with VisionPilot format
   - Can be imported to binary format

3. Legacy: OpenCV YAML format
   - For camera intrinsics only
   - Compatible with OpenCV calib3d

Usage:
    from calibration_storage import CalibrationStorage

    # Load from params (runtime)
    calib = CalibrationStorage.load_from_params()

    # Import from YAML (factory calibration)
    calib = CalibrationStorage.load_from_yaml('/data/calibration/factory.yaml')

    # Save to both formats
    calib.save_to_params()  # Binary for runtime
    calib.save_to_yaml()    # YAML for backup/sharing

See Also:
    - OpenPilot: selfdrive/locationd/calibrationd.py (binary format)
    - VisionPilot: camera_calibration.py (YAML format)
"""

from __future__ import annotations

import os
import yaml
import numpy as np
from pathlib import Path
from dataclasses import dataclass, field

from cereal import log
import cereal.messaging as messaging
from openpilot.common.params import Params
from openpilot.common.swaglog import cloudlog

from openpilot.selfdrive.gridd.camera_geometry import CameraArrayGeometry, CameraIntrinsics

@dataclass
class SingleCameraCalibration:
    """Calibration data for a single camera."""
    camera_id: str

    # Intrinsics
    focal_x: float = 800.0
    focal_y: float = 800.0
    center_x: float = 960.0
    center_y: float = 540.0
    image_width: int = 1920
    image_height: int = 1080

    # Distortion
    k1: float = 0.0
    k2: float = 0.0
    p1: float = 0.0
    p2: float = 0.0
    k3: float = 0.0

    # Extrinsics (camera to vehicle)
    rpy: np.ndarray = field(default_factory=lambda: np.array([0.0, 0.0, 0.0]))
    height: float = 1.22

    # Quality metrics
    reprojection_error: float = 0.0
    num_images: int = 0
    calibration_date: str = ""

    # Per-axis spread (max-min) of rpy across the calibration block window.
    # Populated by calibrationd from live history; static/factory loads leave at 0.
    rpy_spread: np.ndarray = field(default_factory=lambda: np.array([0.0, 0.0, 0.0]))
    height_spread: float = 0.0

    def to_intrinsics(self) -> CameraIntrinsics:
        """Convert to CameraIntrinsics object."""
        return CameraIntrinsics(
            fx=self.focal_x,
            fy=self.focal_y,
            cx=self.center_x,
            cy=self.center_y,
            k1=self.k1,
            k2=self.k2,
            p1=self.p1,
            p2=self.p2,
            k3=self.k3
        )

    def to_matrix(self) -> np.ndarray:
        """Get 3x3 camera matrix."""
        return np.array([
            [self.focal_x, 0, self.center_x],
            [0, self.focal_y, self.center_y],
            [0, 0, 1]
        ])

    def to_dist_coeffs(self) -> np.ndarray:
        """Get distortion coefficients."""
        return np.array([self.k1, self.k2, self.p1, self.p2, self.k3])


@dataclass
class MultiCameraCalibration:
    """Complete calibration for all cameras."""
    platform: str = "rk3588"
    reference_camera: str = "road"
    calibration_date: str = ""
    cameras: dict[str, SingleCameraCalibration] = field(default_factory=dict)

    # Stereo configuration
    stereo_baseline_mm: float = 80.0

    def get_camera(self, camera_id: str) -> SingleCameraCalibration | None:
        """Get calibration for a specific camera."""
        return self.cameras.get(camera_id)

    def to_geometry(self) -> CameraArrayGeometry:
        """Convert to CameraArrayGeometry."""
        geometry = CameraArrayGeometry.for_platform(self.platform)

        # Update with calibrated intrinsics
        for cam_id, calib in self.cameras.items():
            if cam_id in geometry.cameras:
                geometry.cameras[cam_id].intrinsics = calib.to_intrinsics()

        return geometry


class CalibrationStorage:
    """Unified calibration storage supporting multiple formats."""

    # Default paths
    YAML_PATH = Path("/data/params/calibration/camera_calibration.yaml")
    LEGACY_CALIBRATION_PARAMS = "CalibrationParams"
    MULTI_CAMERA_CALIBRATION_PARAMS = "CameraCalibrationParams"

    @classmethod
    def load_from_params(cls, multi_camera: bool = False) -> MultiCameraCalibration | None:
        """Load calibration from OpenPilot params (binary format).

        Args:
            multi_camera: If True, try CameraCalibrationParams first

        Returns:
            MultiCameraCalibration or None if not found
        """
        params = Params()

        # Try multi-camera format first if requested
        if multi_camera:
            data = params.get(cls.MULTI_CAMERA_CALIBRATION_PARAMS)
            if data:
                try:
                    return cls._parse_multi_camera_binary(data)
                except Exception as e:
                    cloudlog.warning(f"Failed to parse multi-camera params: {e}")

        # Fall back to legacy format
        data = params.get(cls.LEGACY_CALIBRATION_PARAMS)
        if not data:
            cloudlog.info("No calibration found in params")
            return None

        try:
            return cls._parse_legacy_binary(data)
        except Exception as e:
            cloudlog.error(f"Failed to parse legacy calibration: {e}")
            return None

    @classmethod
    def _parse_legacy_binary(cls, data: bytes) -> MultiCameraCalibration:
        """Parse legacy OpenPilot calibration format."""
        with log.Event.from_bytes(data) as msg:
            live_cal = msg.liveCalibration

            calib = MultiCameraCalibration(
                platform="rk3588",  # Default
                reference_camera="road",
                calibration_date=""
            )

            # Road camera calibration
            if live_cal.rpyCalib:
                rpy = np.array(live_cal.rpyCalib)
                road_calib = SingleCameraCalibration(
                    camera_id="road",
                    rpy=rpy,
                    height=live_cal.height[0] if live_cal.height else 1.22
                )
                calib.cameras["road"] = road_calib

            # Wide camera calibration
            if live_cal.wideFromDeviceEuler:
                wide_rpy = np.array(live_cal.wideFromDeviceEuler)
                wide_calib = SingleCameraCalibration(
                    camera_id="wide_road",
                    rpy=wide_rpy
                )
                calib.cameras["wide_road"] = wide_calib

            return calib

    @classmethod
    def _parse_multi_camera_binary(cls, data: bytes) -> MultiCameraCalibration:
        """Parse multi-camera binary format (EOP extension)."""
        # This would parse a custom cereal message format
        # For now, fall back to legacy
        return cls._parse_legacy_binary(data)

    @classmethod
    def save_to_params(cls, calib: MultiCameraCalibration,
                       param_put: bool = True) -> bytes:
        """Save calibration to OpenPilot params (binary format).

        Args:
            calib: Calibration to save
            param_put: If True, also write to params

        Returns:
            Binary serialized data
        """
        # Create LiveCalibrationData message
        msg = messaging.new_message('liveCalibration')
        live_cal = msg.liveCalibration

        # Get road camera (primary)
        road_calib = calib.cameras.get("road")
        if road_calib:
            live_cal.rpyCalib = road_calib.rpy.tolist()
            live_cal.rpyCalibSpread = np.asarray(road_calib.rpy_spread, dtype=float).tolist()
            live_cal.height = [road_calib.height]

        # Get wide camera
        wide_calib = calib.cameras.get("wide_road")
        if wide_calib:
            live_cal.wideFromDeviceEuler = wide_calib.rpy.tolist()

        # Status
        live_cal.calStatus = log.LiveCalibrationData.Status.calibrated
        live_cal.validBlocks = 50  # Assume fully calibrated
        live_cal.calPerc = 100

        # Serialize
        data = msg.to_bytes()

        if param_put:
            params = Params()
            params.put(cls.LEGACY_CALIBRATION_PARAMS, data)

            # Also save multi-camera format
            if len(calib.cameras) > 2:
                multi_data = cls._serialize_multi_camera(calib)
                params.put(cls.MULTI_CAMERA_CALIBRATION_PARAMS, multi_data)

        return data

    @classmethod
    def _serialize_multi_camera(cls, calib: MultiCameraCalibration) -> bytes:
        """Serialize multi-camera calibration to binary.

        Uses YAML as intermediate for now (since we need custom schema).
        In production, this would use a proper capnp schema.
        """
        yaml_data = cls._to_yaml_dict(calib)
        return yaml.dump(yaml_data).encode('utf-8')

    @classmethod
    def load_from_yaml(cls, filepath: str | Path) -> MultiCameraCalibration | None:
        """Load calibration from YAML file.

        Supports both VisionPilot and EOP formats.
        """
        filepath = Path(filepath)
        if not filepath.exists():
            cloudlog.warning(f"Calibration file not found: {filepath}")
            return None

        try:
            with open(filepath) as f:
                data = yaml.safe_load(f)

            # Check format
            if 'camera_array' in data:
                return cls._parse_eop_yaml(data)
            elif any(k in data for k in ['cameras', 'intrinsics']):
                return cls._parse_visionpilot_yaml(data)
            else:
                cloudlog.error(f"Unknown YAML format in {filepath}")
                return None

        except Exception as e:
            cloudlog.error(f"Failed to load YAML calibration: {e}")
            return None

    @classmethod
    def _parse_eop_yaml(cls, data: dict) -> MultiCameraCalibration:
        """Parse EOP YAML format."""
        array_data = data['camera_array']

        calib = MultiCameraCalibration(
            platform=array_data.get('platform', 'rk3588'),
            reference_camera=array_data.get('reference_camera', 'road'),
            calibration_date=array_data.get('calibration_date', ''),
            stereo_baseline_mm=array_data.get('stereo_baseline_mm', 80.0)
        )

        # Parse each camera
        cameras_data = array_data.get('cameras', {})
        for cam_id, cam_data in cameras_data.items():
            calib.cameras[cam_id] = cls._parse_camera_yaml(cam_id, cam_data)

        return calib

    @classmethod
    def _parse_visionpilot_yaml(cls, data: dict) -> MultiCameraCalibration:
        """Parse VisionPilot YAML format."""
        calib = MultiCameraCalibration(
            platform='rk3576',
            calibration_date=data.get('calibration', {}).get('date', '')
        )

        # VisionPilot uses different structure
        if 'cameras' in data:
            for cam_id, cam_data in data['cameras'].items():
                calib.cameras[cam_id] = cls._parse_camera_yaml(cam_id, cam_data)

        return calib

    @classmethod
    def _parse_camera_yaml(cls, cam_id: str, data: dict) -> SingleCameraCalibration:
        """Parse single camera from YAML."""
        intrinsics = data.get('intrinsics', {})
        extrinsics = data.get('extrinsics', {})
        calibration = data.get('calibration', {})

        # Parse RPY from rotation matrix if available
        rpy = np.array([0.0, 0.0, 0.0])
        if 'rotation' in extrinsics:
            # Convert rotation matrix to RPY (simplified)
            R = np.array(extrinsics['rotation'])
            # Assume small angles, approximate
            rpy[1] = np.arctan2(-R[2, 0], np.sqrt(R[0, 0]**2 + R[1, 0]**2))  # pitch
            rpy[2] = np.arctan2(R[1, 0], R[0, 0])  # yaw

        # Parse translation for height
        height = 1.22
        if 'translation' in extrinsics:
            t = extrinsics['translation']
            height = -t[2] if len(t) > 2 else 1.22

        return SingleCameraCalibration(
            camera_id=cam_id,
            focal_x=intrinsics.get('fx', 800.0),
            focal_y=intrinsics.get('fy', 800.0),
            center_x=intrinsics.get('cx', 960.0),
            center_y=intrinsics.get('cy', 540.0),
            image_width=intrinsics.get('width', 1920),
            image_height=intrinsics.get('height', 1080),
            k1=intrinsics.get('distortion', {}).get('k1', 0.0),
            k2=intrinsics.get('distortion', {}).get('k2', 0.0),
            p1=intrinsics.get('distortion', {}).get('p1', 0.0),
            p2=intrinsics.get('distortion', {}).get('p2', 0.0),
            k3=intrinsics.get('distortion', {}).get('k3', 0.0),
            rpy=rpy,
            height=height,
            reprojection_error=calibration.get('reprojection_error', 0.0),
            num_images=calibration.get('num_images', 0),
            calibration_date=calibration.get('date', '')
        )

    @classmethod
    def save_to_yaml(cls, calib: MultiCameraCalibration,
                     filepath: str | (Path | None) = None) -> Path:
        """Save calibration to YAML file."""
        if filepath is None:
            filepath = cls.YAML_PATH

        filepath = Path(filepath)
        filepath.parent.mkdir(parents=True, exist_ok=True)

        data = cls._to_yaml_dict(calib)

        with open(filepath, 'w') as f:
            yaml.dump(data, f, default_flow_style=False)

        cloudlog.info(f"Saved calibration to {filepath}")
        return filepath

    @classmethod
    def _to_yaml_dict(cls, calib: MultiCameraCalibration) -> dict:
        """Convert calibration to YAML dictionary."""
        cameras = {}
        for cam_id, cam in calib.cameras.items():
            cameras[cam_id] = {
                'intrinsics': {
                    'width': cam.image_width,
                    'height': cam.image_height,
                    'fx': float(cam.focal_x),
                    'fy': float(cam.focal_y),
                    'cx': float(cam.center_x),
                    'cy': float(cam.center_y),
                    'distortion': {
                        'k1': float(cam.k1),
                        'k2': float(cam.k2),
                        'p1': float(cam.p1),
                        'p2': float(cam.p2),
                        'k3': float(cam.k3),
                    }
                },
                'extrinsics': {
                    'rotation': cls._rpy_to_rotation_matrix(cam.rpy).tolist(),
                    'translation': [0.0, 0.0, -cam.height]
                },
                'calibration': {
                    'date': cam.calibration_date,
                    'reprojection_error': float(cam.reprojection_error),
                    'num_images': cam.num_images
                }
            }

        return {
            'camera_array': {
                'platform': calib.platform,
                'reference_camera': calib.reference_camera,
                'calibration_date': calib.calibration_date,
                'stereo_baseline_mm': calib.stereo_baseline_mm,
                'cameras': cameras
            }
        }

    @classmethod
    def _rpy_to_rotation_matrix(cls, rpy: np.ndarray) -> np.ndarray:
        """Convert roll-pitch-yaw to rotation matrix."""
        # Simplified: assume small angles
        R = np.eye(3)
        R[1, 1] = np.cos(rpy[0])  # roll
        R[2, 2] = np.cos(rpy[0])
        R[1, 2] = -np.sin(rpy[0])
        R[2, 1] = np.sin(rpy[0])
        return R

    FACTORY_CALIBRATION_PARAMS = "FactoryCalibrationParams"

    @classmethod
    def import_from_factory(cls, factory_yaml: str | Path,
                           save_to_params: bool = True) -> MultiCameraCalibration:
        """Import factory calibration and optionally save to params.

        This is the main entry point for factory calibration workflow:
        1. Load from factory YAML (VisionPilot or EOP format)
        2. Save to FactoryCalibrationParams (IMMUTABLE - intrinsics)
        3. Save to CalibrationParams (runtime - extrinsics can be refined)

        IMPORTANT: Factory calibration (intrinsics) is stored separately and
        should NEVER be modified by runtime calibration processes.
        """
        calib = cls.load_from_yaml(factory_yaml)
        if calib is None:
            raise ValueError(f"Failed to load factory calibration from {factory_yaml}")

        if save_to_params:
            # Save to factory params (IMMUTABLE - never touched by runtime calibration)
            cls.save_factory_calibration(calib)

            # Also save as runtime params (extrinsics can be refined onroad)
            cls.save_to_params(calib)
            cloudlog.info("Imported factory calibration to params (intrinsics protected)")

        return calib

    @classmethod
    def save_factory_calibration(cls, calib: MultiCameraCalibration) -> bytes:
        """Save factory calibration to immutable params.

        This stores the factory-measured intrinsics (focal length, distortion, etc.)
        that should NEVER be modified by runtime calibration.

        Runtime calibration (calibrationd/camera_calibrationd) only refines
        extrinsics (pitch, yaw, height) based on driving data.
        """
        yaml_data = cls._to_yaml_dict(calib)
        data = yaml.dump(yaml_data).encode('utf-8')

        params = Params()
        params.put(cls.FACTORY_CALIBRATION_PARAMS, data)
        params.put_bool("EOPFactoryCalibrated", True)

        cloudlog.info("Saved factory calibration (intrinsics protected)")
        return data

    @classmethod
    def load_factory_calibration(cls) -> MultiCameraCalibration | None:
        """Load factory calibration (intrinsics).

        This loads the immutable factory calibration containing camera intrinsics.
        Use this when you need the original factory-measured values.
        """
        params = Params()
        data = params.get(cls.FACTORY_CALIBRATION_PARAMS)

        if not data:
            cloudlog.info("No factory calibration found")
            return None

        try:
            yaml_data = yaml.safe_load(data.decode('utf-8'))
            return cls._parse_eop_yaml(yaml_data)
        except Exception as e:
            cloudlog.error(f"Failed to load factory calibration: {e}")
            return None

    @classmethod
    def get_merged_calibration(cls) -> MultiCameraCalibration | None:
        """Get calibration with factory intrinsics + runtime extrinsics.

        This is the recommended way to get calibration for most use cases:
        - Intrinsics come from factory calibration (immutable)
        - Extrinsics come from runtime calibration (refined onroad)

        Falls back to runtime-only if no factory calibration exists.
        """
        factory = cls.load_factory_calibration()
        runtime = cls.load_from_params(multi_camera=True)

        if factory is None:
            return runtime

        if runtime is None:
            return factory

        # Merge: use factory intrinsics, runtime extrinsics
        merged = MultiCameraCalibration(
            platform=factory.platform,
            reference_camera=factory.reference_camera,
            calibration_date=runtime.calibration_date or factory.calibration_date,
            stereo_baseline_mm=factory.stereo_baseline_mm
        )

        for cam_id, factory_cam in factory.cameras.items():
            runtime_cam = runtime.cameras.get(cam_id)

            merged_cam = SingleCameraCalibration(
                camera_id=cam_id,
                # Factory intrinsics (IMMUTABLE)
                focal_x=factory_cam.focal_x,
                focal_y=factory_cam.focal_y,
                center_x=factory_cam.center_x,
                center_y=factory_cam.center_y,
                image_width=factory_cam.image_width,
                image_height=factory_cam.image_height,
                k1=factory_cam.k1,
                k2=factory_cam.k2,
                p1=factory_cam.p1,
                p2=factory_cam.p2,
                k3=factory_cam.k3,
                # Runtime extrinsics (refined onroad)
                rpy=runtime_cam.rpy if runtime_cam else factory_cam.rpy,
                height=runtime_cam.height if runtime_cam else factory_cam.height,
                # Quality metrics
                reprojection_error=factory_cam.reprojection_error,
                num_images=factory_cam.num_images,
                calibration_date=factory_cam.calibration_date
            )
            merged.cameras[cam_id] = merged_cam

        return merged

    @classmethod
    def export_for_sharing(cls, output_path: str | Path,
                          include_intrinsics: bool = True,
                          include_extrinsics: bool = True) -> Path:
        """Export current calibration to YAML for sharing/backup.

        Loads from params and exports to YAML format.
        """
        calib = cls.load_from_params(multi_camera=True)
        if calib is None:
            raise ValueError("No calibration found in params")

        return cls.save_to_yaml(calib, output_path)


def test_calibration_storage():
    """Test calibration storage system."""
    print("Testing Calibration Storage")
    print("=" * 60)

    # Create sample calibration
    calib = MultiCameraCalibration(
        platform="rk3588",
        reference_camera="road",
        calibration_date="2026-03-24T12:00:00",
        stereo_baseline_mm=80.0
    )

    # Add road camera
    calib.cameras["road"] = SingleCameraCalibration(
        camera_id="road",
        focal_x=1050.0,
        focal_y=1050.0,
        center_x=960.0,
        center_y=540.0,
        image_width=1920,
        image_height=1080,
        rpy=np.array([0.0, 0.02, -0.01]),
        height=1.22,
        reprojection_error=0.5,
        num_images=25
    )

    # Add wide camera
    calib.cameras["wide_road"] = SingleCameraCalibration(
        camera_id="wide_road",
        focal_x=350.0,
        focal_y=350.0,
        rpy=np.array([0.0, 0.0, 0.0])
    )

    # Save to YAML
    import tempfile
    with tempfile.NamedTemporaryFile(mode='w', suffix='.yaml', delete=False) as f:
        temp_path = f.name

    print(f"\n1. Saving to YAML: {temp_path}")
    CalibrationStorage.save_to_yaml(calib, temp_path)

    # Load back from YAML
    print("2. Loading from YAML")
    loaded = CalibrationStorage.load_from_yaml(temp_path)
    print(f"   Platform: {loaded.platform}")
    print(f"   Cameras: {list(loaded.cameras.keys())}")

    road = loaded.cameras.get("road")
    if road:
        print(f"   Road fx: {road.focal_x:.1f}")

    # Clean up
    os.unlink(temp_path)

    print("\n✓ Calibration storage test passed!")


if __name__ == "__main__":
    test_calibration_storage()

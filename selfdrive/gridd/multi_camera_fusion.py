"""
Multi-Camera Fusion for EOP (OpenPilot)
=======================================

Fuses camera inputs from ExoPilot 01M (RK3588):
4 MIPI cameras (wide_road, road, stereo_left, stereo_right)
+ up to 3 USB cameras (side_left, side_right, rear_camera)

Fusion Strategy:
1. Temporal alignment (sync frames within time window)
2. Geometric projection using camera calibration
3. Range-aware weighting
4. Temporal consistency for stable perception

Output: Unified perception with:
- Fused feature maps for model input
- Range-aware confidence weights
- Multi-scale detection fusion

Usage:
    from openpilot.selfdrive.gridd.multi_camera_fusion import MultiCameraFusion

    # Create fusion system
    fusion = MultiCameraFusion()

    # Add frames from each camera
    fusion.add_frame('road', road_image, timestamp)
    fusion.add_frame('wide_road', wide_image, timestamp)

    # Get fused output
    result = fusion.fuse()

    # Access fused data
    fused_image = result.fused_image
    range_weights = result.range_weights
"""

from __future__ import annotations

import numpy as np
import cv2
from dataclasses import dataclass, field
from enum import Enum, auto
from collections import deque
import time

from openpilot.selfdrive.gridd.camera_geometry import CameraArrayGeometry
from openpilot.common.swaglog import cloudlog
from openpilot.system.hardware import HARDWARE

class CameraRole(Enum):
    """Camera roles in the array."""
    WIDE_ROAD = auto()      # Wide FOV top (1.7mm, ~120°, 0-30m)
    ROAD = auto()           # Main road camera (8mm, ~60°, 0-80m) - PRIMARY
    STEREO_LEFT = auto()    # Stereo left (3.6mm, ~100°, 0-50m)
    STEREO_RIGHT = auto()   # Stereo right (3.6mm, ~100°, 0-50m)


@dataclass
class CameraFrame:
    """Single camera frame with metadata."""
    role: CameraRole
    image: np.ndarray       # BGR image
    timestamp: float        # seconds
    width: int
    height: int

    # Camera properties
    focal_length_mm: float = 8.0
    fov_degrees: float = 60.0

    # Effective range
    min_range_m: float = 0.0
    max_range_m: float = 80.0

    @property
    def is_valid(self) -> bool:
        return self.image is not None and self.image.size > 0


@dataclass
class FusedPerception:
    """Fused perception output from all cameras."""

    # Fused image for model input
    fused_image: np.ndarray

    # Individual camera features (for debugging/analysis)
    camera_features: dict[CameraRole, np.ndarray] = field(default_factory=dict)

    # Range-aware weights
    range_weights: np.ndarray | None = None

    # Metadata
    timestamp: float = 0.0
    active_cameras: list[CameraRole] = field(default_factory=list)

    # Platform info
    max_detection_range_m: float = 80.0
    platform: str = "unknown"


@dataclass
class FusionConfig:
    """Configuration for multi-camera fusion."""

    # Timing
    sync_window_ms: float = 50.0    # Max time diff for frame sync
    temporal_decay: float = 0.7     # Weight for historical data

    # Image processing
    target_width: int = 512
    target_height: int = 256

    # Range weighting
    enable_range_weights: bool = True
    road_weight_near: float = 0.8   # Road camera weight at close range

    # Platform-specific. None means "the board that is running" -- resolved
    # in MultiCameraFusion, not here, so the dataclass stays importable
    # without touching hardware.
    platform: str | None = None


# Which HAL camera fills each role, per board. The HAL describes cameras by
# name and this enum describes them by job, so the two need joining somewhere
# -- and the names genuinely differ: 01M's road camera is "road", 02M's is
# "mono_narrow". 02M's mono_tele has no role here; the fusion takes four
# inputs and the tele camera is not one of them.
HAL_NAME_BY_ROLE = {
    'rk3588': {
        CameraRole.WIDE_ROAD: 'wide_road',
        CameraRole.ROAD: 'road',
        CameraRole.STEREO_LEFT: 'stereo_left',
        CameraRole.STEREO_RIGHT: 'stereo_right',
    },
    'rk3576': {
        CameraRole.WIDE_ROAD: 'mono_wide',
        CameraRole.ROAD: 'mono_narrow',
        CameraRole.STEREO_LEFT: 'stereo_left',
        CameraRole.STEREO_RIGHT: 'stereo_right',
    },
}

# Fusion range policy per role, in metres. Not board data -- how far a given
# role is trusted is a tuning decision, and it is the same decision for the
# equivalent camera on either board. Optics come from the HAL instead.
ROLE_RANGE_M = {
    CameraRole.WIDE_ROAD: (0.0, 30.0),
    CameraRole.ROAD: (0.0, 80.0),
    CameraRole.STEREO_LEFT: (0.0, 50.0),
    CameraRole.STEREO_RIGHT: (0.0, 50.0),
}

# Last-resort optics, used only when no geometry source is available (dev PC,
# CI). These were the in-repo numbers for 01M and had drifted from the board:
# the HAL gives road 40 deg at 1920x1280, not 60 deg at 1920x1080. Corrected
# here so the fallback at least matches the hardware, but the HAL is the
# source of truth.
FALLBACK_OPTICS = {
    CameraRole.WIDE_ROAD: {'focal_mm': 1.7, 'fov': 119.0, 'width': 1920, 'height': 1280},
    CameraRole.ROAD: {'focal_mm': 8.0, 'fov': 40.0, 'width': 1920, 'height': 1280},
    CameraRole.STEREO_LEFT: {'focal_mm': 3.6, 'fov': 71.0, 'width': 2560, 'height': 1440},
    CameraRole.STEREO_RIGHT: {'focal_mm': 3.6, 'fov': 71.0, 'width': 2560, 'height': 1440},
}


def build_camera_specs(platform: str, geometry) -> dict[CameraRole, dict]:
    """Per-role optics for a board, from a HAL camera-geometry module.

    Pure: everything it needs is an argument, so it is callable with a real
    HAL module, a stand-in, or None without touching global hardware state.
    `geometry` is whatever HARDWARE.hal_module("camera_geometry") returns --
    an object exposing LENS_MM / FOV_DEG / IMAGE_SIZE_PX keyed by camera
    name -- or None when the HAL is not installed.

    This replaced a hardcoded table keyed by board name with exactly one
    board in it, so the other board silently borrowed 01M's focal lengths
    and fields of view.
    """
    names = HAL_NAME_BY_ROLE.get(platform)
    if geometry is None or names is None:
        cloudlog.warning(
            "camera specs: no HAL camera geometry for '%s'; using fallback "
            "optics. Fields of view and resolutions are approximate.", platform)
        return {role: {**optics, 'min_m': ROLE_RANGE_M[role][0],
                       'max_m': ROLE_RANGE_M[role][1]}
                for role, optics in FALLBACK_OPTICS.items()}

    specs = {}
    for role, name in names.items():
        if name not in geometry.LENS_MM:
            cloudlog.error(
                "camera specs: HAL has no camera '%s' for role %s on '%s' -- "
                "dropping that input rather than guessing.", name, role, platform)
            continue
        width, height = geometry.IMAGE_SIZE_PX[name]
        min_m, max_m = ROLE_RANGE_M[role]
        specs[role] = {
            'focal_mm': geometry.LENS_MM[name], 'fov': float(geometry.FOV_DEG[name]),
            'min_m': min_m, 'max_m': max_m, 'width': width, 'height': height,
        }
    return specs


class MultiCameraFusion:
    """Fuses multiple camera inputs for unified perception.

    Supports ExoPilot 01M (RK3588, 7 cameras).
    Automatically adapts fusion strategy based on available cameras.
    """

    # Camera specifications by platform
    def __init__(self, config: FusionConfig | None = None, camera_geometry=None):
        """Initialize multi-camera fusion.

        Args:
            config: Fusion configuration. If None, defaults are used and the
                board is detected.
            camera_geometry: HAL camera-geometry module to read optics from.
                Defaults to the running board's. Passing one explicitly is
                how a caller -- or a test -- supplies a different board's
                geometry without reaching into global hardware state.
        """
        if config is None:
            config = FusionConfig()

        self.config = config
        self.platform = config.platform or HARDWARE.get_device_type()
        if camera_geometry is None:
            camera_geometry = HARDWARE.hal_module("camera_geometry")
        # Resolved once: reading the HAL is not something to do per frame.
        self._specs = build_camera_specs(self.platform, camera_geometry)

        # Camera geometry
        self.geometry = CameraArrayGeometry.for_platform(self.platform)

        # Frame buffers
        self._frames: dict[CameraRole, CameraFrame] = {}
        self._frame_history: deque = deque(maxlen=5)

        # Precompute range weight maps
        self._range_weights: dict[CameraRole, np.ndarray] = {}
        self._init_range_weights()

        # RGA for hardware-accelerated preprocessing
        self._rga = None
        try:
            from openpilot.system.inferenced.client import InferenceClient
            client = InferenceClient("gridd")
            self._rga = client.rga()
            cloudlog.info("MultiCameraFusion: RGA available for preprocessing")
        except Exception:
            cloudlog.warning("MultiCameraFusion: RGA not available, using cv2 fallback")

        cloudlog.info(f"MultiCameraFusion initialized for {self.platform}")
        cloudlog.info(f"Supported cameras: {self._get_supported_cameras()}")

    def _get_supported_cameras(self) -> list[CameraRole]:
        """Roles this board actually has cameras for."""
        return list(self._specs.keys())

    def _init_range_weights(self):
        """Initialize range-aware weight maps."""
        h, w = self.config.target_height, self.config.target_width

        # Create coordinate grids
        x = np.linspace(-1, 1, w)   # -1 = left, 1 = right
        y = np.linspace(0, 1, h)    # 0 = top (far), 1 = bottom (near)
        xx, yy = np.meshgrid(x, y)

        # Distance from center (normalized)
        np.sqrt(xx**2 + (yy - 0.5)**2)

        for role in self._get_supported_cameras():
            if role == CameraRole.ROAD:
                # Road: primary weight in center, all ranges
                weight = np.exp(-((xx)/0.4)**2) * np.ones_like(yy)

            elif role == CameraRole.WIDE_ROAD:
                # Wide: high weight for close range, side regions
                weight = np.exp(-((xx + 0.5)/0.5)**2) * (1 - yy * 0.3)

            elif role in (CameraRole.STEREO_LEFT, CameraRole.STEREO_RIGHT):
                # Stereo: side views, close to medium range
                center_x = -0.8 if role == CameraRole.STEREO_LEFT else 0.8
                weight = np.exp(-((xx - center_x)/0.3)**2) * (1 - yy * 0.2)
            else:
                weight = np.ones((h, w))

            self._range_weights[role] = weight

    def add_frame(self, camera_name: str, image: np.ndarray, timestamp: float):
        """Add a frame from a camera.

        Args:
            camera_name: 'road', 'wide_road', 'stereo_left', 'stereo_right'
            image: BGR image
            timestamp: frame timestamp in seconds
        """
        if image is None or image.size == 0:
            return

        # Map name to role
        role_map = {
            'road': CameraRole.ROAD,
            'wide_road': CameraRole.WIDE_ROAD,
            'stereo_left': CameraRole.STEREO_LEFT,
            'stereo_right': CameraRole.STEREO_RIGHT,
        }

        role = role_map.get(camera_name.lower())
        if role is None:
            cloudlog.warning(f"Unknown camera: {camera_name}")
            return

        if role not in self._get_supported_cameras():
            cloudlog.debug(f"Camera {camera_name} not supported on {self.platform}")
            return

        specs = self._specs[role]

        self._frames[role] = CameraFrame(
            role=role,
            image=image,
            timestamp=timestamp,
            width=image.shape[1],
            height=image.shape[0],
            focal_length_mm=specs['focal_mm'],
            fov_degrees=specs['fov'],
            min_range_m=specs['min_m'],
            max_range_m=specs['max_m']
        )

    def _sync_frames(self) -> dict[CameraRole, CameraFrame]:
        """Synchronize frames within time window."""
        if not self._frames:
            return {}

        # Find reference timestamp (most recent)
        timestamps = [f.timestamp for f in self._frames.values()]
        ref_time = max(timestamps)

        # Filter frames within sync window
        synced = {}
        for role, frame in self._frames.items():
            if abs(frame.timestamp - ref_time) * 1000 <= self.config.sync_window_ms:
                synced[role] = frame

        return synced

    def _preprocess(self, frame: CameraFrame) -> np.ndarray:
        """Preprocess frame for fusion."""
        image = frame.image

        # Ensure BGR
        if len(image.shape) == 2:
            image = cv2.cvtColor(image, cv2.COLOR_GRAY2BGR)
        elif len(image.shape) == 3 and image.shape[2] == 4:
            image = cv2.cvtColor(image, cv2.COLOR_BGRA2BGR)

        # Resize to target using RGA if available
        target_size = (self.config.target_width, self.config.target_height)
        resized = self._rga_resize(image, target_size)

        # Normalize to float
        normalized = resized.astype(np.float32) / 255.0

        return normalized

    def _rga_resize(self, image: np.ndarray, target_size: tuple) -> np.ndarray:
        """Resize image using RGA hardware accelerator if available."""
        if self._rga is not None:
            try:
                result = self._rga.infer(
                    model_name='scale',
                    inputs={'src': image, 'width': target_size[0], 'height': target_size[1]}
                )
                if result.success:
                    return result.outputs['output']
            except Exception:
                pass  # Fall back to cv2
        return cv2.resize(image, target_size, interpolation=cv2.INTER_LINEAR)

    def fuse(self) -> FusedPerception | None:
        """Fuse all camera frames.

        Returns:
            FusedPerception or None if insufficient frames
        """
        # Synchronize frames
        synced = self._sync_frames()

        # Need at least road camera
        if CameraRole.ROAD not in synced:
            return None

        # Preprocess all frames
        features = {}
        for role, frame in synced.items():
            features[role] = self._preprocess(frame)

        # Create fused image using weighted combination
        h, w = self.config.target_height, self.config.target_width
        fused = np.zeros((h, w, 3), dtype=np.float32)
        total_weight = np.zeros((h, w), dtype=np.float32)

        for role, feature in features.items():
            weight = self._range_weights.get(role, np.ones((h, w)))

            # Expand weight for broadcasting
            weight_expanded = np.expand_dims(weight, axis=-1)

            # Weighted addition
            fused += feature * weight_expanded
            total_weight += weight

        # Normalize by total weight
        total_weight_expanded = np.expand_dims(total_weight, axis=-1)
        fused = np.divide(fused, total_weight_expanded,
                         where=total_weight_expanded > 0,
                         out=np.zeros_like(fused))

        max_range = 80.0

        # Create range weight map
        range_weights = self._create_range_weight_map()

        # Temporal smoothing
        timestamp = time.monotonic()
        if self._frame_history:
            prev = self._frame_history[-1]
            dt = timestamp - prev.timestamp
            if dt < 0.5:
                alpha = self.config.temporal_decay * np.exp(-dt / 0.1)
                fused = alpha * prev.fused_image + (1 - alpha) * fused

        # Create result
        result = FusedPerception(
            fused_image=(fused * 255).astype(np.uint8),
            camera_features=features,
            range_weights=range_weights,
            timestamp=timestamp,
            active_cameras=list(synced.keys()),
            max_detection_range_m=max_range,
            platform=self.platform
        )

        # Update history
        self._frame_history.append(result)

        return result

    def _create_range_weight_map(self) -> np.ndarray:
        """Create range-aware weight map for VTSC/path planning."""
        h, w = self.config.target_height, self.config.target_width

        # Create distance map (0-1, where 1 is farthest/top)
        y = np.linspace(0, 1, h)
        distance_map = np.tile(y.reshape(-1, 1), (1, w))

        # Lower confidence at far range
        weights = np.clip(1.0 - distance_map * 0.5, 0.5, 1.0)

        return weights

    def clear(self):
        """Clear all frames."""
        self._frames.clear()

    def get_camera_status(self) -> dict:
        """Get status of all cameras."""
        specs = self._specs

        has_stereo = (CameraRole.STEREO_LEFT in self._frames and
                     CameraRole.STEREO_RIGHT in self._frames)

        top_row_active = sum(1 for r in [CameraRole.WIDE_ROAD, CameraRole.ROAD]
                           if r in self._frames)

        return {
            'platform': self.platform,
            'total_cameras': len(specs),
            'active_cameras': [r.name for r in self._frames.keys()],
            'top_row_active': top_row_active,
            'has_stereo_pair': has_stereo,
            'stereo_baseline_mm': 80,
            'max_detection_range_m': 80.0,
        }


def test_multi_camera_fusion():
    """Test multi-camera fusion."""
    print("Testing Multi-Camera Fusion")
    print("=" * 60)

    # Test ExoPilot 01M (RK3588)
    print("\n--- ExoPilot 01M (RK3588) 4-Camera ---")
    fusion1 = MultiCameraFusion(FusionConfig())

    # Add dummy frames
    for cam in ['road', 'wide_road', 'stereo_left', 'stereo_right']:
        dummy = np.random.randint(0, 255, (1080, 1920, 3), dtype=np.uint8)
        fusion1.add_frame(cam, dummy, time.monotonic())

    result1 = fusion1.fuse()
    if result1:
        print("✓ Fusion successful")
        print(f"  Active: {[r.name for r in result1.active_cameras]}")
        print(f"  Max range: {result1.max_detection_range_m}m")
        print(f"  Fused shape: {result1.fused_image.shape}")

    # Test status
    print("\n--- Camera Status ---")
    print(f"ExoPilot 01M: {fusion1.get_camera_status()}")

    print("\n✓ All tests passed!")


if __name__ == "__main__":
    test_multi_camera_fusion()

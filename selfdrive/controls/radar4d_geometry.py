"""
Radar-stereo camera geometry utilities.

The BGT60TR13C 4D radar is mounted at the center of the stereo camera pair
(between stereo_left and stereo_right, on the vehicle centerline).  This
module provides the geometric link between radar detections and camera
images so gridd can:

  - project radar points into stereo/road camera images for visualization
  - associate radar returns with stereo depth pixels
  - gate radar clutter using the camera's field of view

Coordinate frames:
  - Radar / vehicle / world: X=forward, Y=left, Z=up (ISO 8855)
  - Camera (OpenCV): X=right, Y=down, Z=forward

The radar frame is identical to the world frame modulo this module's mount
rotation, so radar (range, azimuth, elevation) converts directly to world
(x, y, z).  Camera projection then uses the existing CameraArrayGeometry.

Extrinsics split, same as camera calibration (see calibration_storage.py's
factory-vs-runtime split on the VisionPilot side):
  - x/y/z/roll: FACTORY bracket geometry.  Roll is never estimated on-road —
    OnRoadCalibrator/calibrationd assume a level mount, so there is no roll
    signal to share.
  - yaw/pitch: this module's fields are the FACTORY BORESIGHT RESIDUAL only
    (radar-to-camera-array alignment tolerance at assembly — nominally 0).
    The much larger on-road VEHICLE TILT (windshield angle, mount drift) is
    already applied upstream, before values reach this module: radar4d.py's
    `_apply_calibration()` rotates every detection's az/el by
    `liveCalibration.rpyCalib` (the same signal camera images are corrected
    by) before gridd.py ever calls into RadarStereoGeometry.  Composing that
    rotation again here would double-apply it — see radar4d.py's docstring
    and docs/eop/bgt60_radar.md#extrinsic-calibration for the full design.
"""

from __future__ import annotations

import json
import math
import os
from dataclasses import dataclass

import numpy as np
import yaml

from openpilot.common.transformations.orientation import rot_from_euler
from openpilot.selfdrive.gridd.camera_geometry import CameraArrayGeometry
from openpilot.system.hardware.hw import Paths


# Extrinsics are user-refined (mount position/orientation on the vehicle), so
# they live at the application layer — unlike factory intrinsics, which are
# owned by the exopilot HAL.
EXTRINSICS_PATH = os.path.join(Paths.eop_data_root(), "calibration", "radar_extrinsics.json")

# Shared vehicle-frame extrinsics registry (see ESP32_RADAR
# docs/pairing-and-calibration.md "Unified vehicle-frame extrinsics"):
# visionpilot WRITES it (pairing seeds, on-road refines); openpilot only READS.
SENSOR_REGISTRY_PATH = os.path.join(Paths.eop_data_root(), "calibration", "sensor_calibration.yaml")

# ESP32 corner id (radar_corner_id_t: 0=FL,1=FR,2=RL,3=RR) → registry key
R2D_CORNER_NAMES = {0: 'front_left', 1: 'front_right', 2: 'rear_left', 3: 'rear_right'}


def load_sensor_registry(path: str = SENSOR_REGISTRY_PATH) -> dict | None:
    """Read the shared sensor_calibration.yaml registry. Returns the parsed
    dict, or None if the file is absent/unparseable (callers fall back)."""
    try:
        with open(path) as f:
            data = yaml.safe_load(f)
        return data if isinstance(data, dict) else None
    except Exception:
        return None


def load_corner_poses(path: str = SENSOR_REGISTRY_PATH) -> dict[int, tuple[float, float, float]] | None:
    """corner_radars section → {corner_id: (x_m, y_m, yaw_deg)} in the vehicle
    frame. Returns None if the registry, the section, or ANY corner entry is
    missing/garbled — the caller then falls back to its placeholder table."""
    corners = (load_sensor_registry(path) or {}).get('corner_radars')
    if not isinstance(corners, dict):
        return None
    poses = {}
    for corner_id, name in R2D_CORNER_NAMES.items():
        entry = corners.get(name)
        try:
            pos, rot = entry['position'], entry['rotation']
            poses[corner_id] = (float(pos['x_m']), float(pos['y_m']),
                                float(rot['yaw_deg']))
        except (TypeError, KeyError, ValueError):
            return None
    return poses


@dataclass
class RadarMounting:
    """Physical mounting of the radar relative to the vehicle frame."""
    x_m: float = 0.0      # forward offset from vehicle center
    y_m: float = 0.0      # left offset (0 = centerline)
    z_m: float = 0.0      # up offset from ground reference
    yaw_deg: float = 0.0  # mounting yaw (0 = straight ahead)
    pitch_deg: float = 0.0
    roll_deg: float = 0.0

    @property
    def position(self) -> np.ndarray:
        return np.array([self.x_m, self.y_m, self.z_m])

    @classmethod
    def load(cls, path: str = EXTRINSICS_PATH) -> "RadarMounting":
        """Load stored mounting extrinsics; returns the default mount if absent."""
        if not os.path.exists(path):
            return cls()
        try:
            with open(path, "r") as f:
                data = json.load(f)
            return cls(
                x_m=float(data.get("x_m", 0.0)),
                y_m=float(data.get("y_m", 0.0)),
                z_m=float(data.get("z_m", 0.0)),
                yaw_deg=float(data.get("yaw_deg", 0.0)),
                pitch_deg=float(data.get("pitch_deg", 0.0)),
                roll_deg=float(data.get("roll_deg", 0.0)),
            )
        except Exception:
            return cls()

    def save(self, path: str = EXTRINSICS_PATH) -> str:
        """Write mounting extrinsics to JSON.  Returns the path written."""
        os.makedirs(os.path.dirname(path), exist_ok=True)
        data = {
            "x_m": self.x_m,
            "y_m": self.y_m,
            "z_m": self.z_m,
            "yaw_deg": self.yaw_deg,
            "pitch_deg": self.pitch_deg,
            "roll_deg": self.roll_deg,
        }
        with open(path, "w") as f:
            json.dump(data, f, indent=2)
        return path


# Default: radar at center of stereo pair, same height as cameras.
RADAR_MOUNT = RadarMounting(x_m=0.0, y_m=0.0, z_m=0.0)


def _world_from_radar_rotation(mount: RadarMounting) -> np.ndarray:
    """Rotation matrix mapping a radar-frame vector into the world frame.

    Built from rot_from_euler() (same primitive radar4d.py/calibrationd use
    elsewhere) with no transpose: mount's (roll, pitch, yaw) directly
    describes the radar frame's orientation relative to the world frame,
    matching this function's pre-existing yaw-only formula (verified to
    reduce to the identical result at pitch=roll=0 — do not add a `.T` here
    without re-checking that against the yaw-only case, since the transposed
    form silently flips rotation direction).  mount's angles are the small
    factory boresight residual (nominally 0,0,0), NOT the on-road vehicle
    tilt — see this module's docstring.
    """
    rpy = np.array([math.radians(mount.roll_deg),
                     math.radians(mount.pitch_deg),
                     math.radians(mount.yaw_deg)])
    return rot_from_euler(rpy)


def radar_polar_to_world(range_m: float, azimuth_deg: float, elevation_deg: float,
                         mount: RadarMounting = RADAR_MOUNT) -> np.ndarray:
    """Convert radar polar measurement to world coordinates.

    Args:
        range_m: radial distance (m)
        azimuth_deg: azimuth angle (deg, 0=forward, +left)
        elevation_deg: elevation angle (deg, 0=boresight, +up)
        mount: radar mounting position/orientation

    Returns:
        World point [X, Y, Z] in meters.
    """
    az = math.radians(azimuth_deg)
    el = math.radians(elevation_deg)
    cos_el = math.cos(el)

    # Radar frame: x=forward, y=left, z=up
    v_r = np.array([
        range_m * cos_el * math.cos(az),
        range_m * cos_el * math.sin(az),
        range_m * math.sin(el),
    ])

    v_w = _world_from_radar_rotation(mount) @ v_r
    return v_w + mount.position


def world_to_radar_polar(point_world: np.ndarray,
                         mount: RadarMounting = RADAR_MOUNT) -> tuple[float, float, float]:
    """Convert world point back to radar polar coordinates.

    Returns:
        (range_m, azimuth_deg, elevation_deg)
    """
    rel = point_world - mount.position
    v_r = _world_from_radar_rotation(mount).T @ rel
    x_r, y_r, z_r = v_r

    r = math.hypot(x_r, math.hypot(y_r, z_r))
    az = math.degrees(math.atan2(y_r, x_r))
    el = math.degrees(math.atan2(z_r, math.hypot(x_r, y_r))) if r > 0 else 0.0
    return r, az, el


class RadarStereoGeometry:
    """Geometry link between the center-mounted radar and the camera array."""

    def __init__(self, camera_geometry: CameraArrayGeometry | None = None,
                 mount: RadarMounting = RADAR_MOUNT):
        self.cam_geo = camera_geometry or CameraArrayGeometry.for_platform('rk3588')
        self.mount = mount

    def radar_to_image(self, camera_name: str, range_m: float,
                       azimuth_deg: float, elevation_deg: float) -> tuple[float, float]:
        """Project a radar detection into a camera image.

        Returns:
            (u, v) pixel coordinates, or (nan, nan) if outside the image.
        """
        P_world = radar_polar_to_world(range_m, azimuth_deg, elevation_deg, self.mount)
        return self.cam_geo.world_to_image(camera_name, P_world)

    def radar_in_camera_fov(self, camera_name: str, range_m: float,
                            azimuth_deg: float, elevation_deg: float) -> bool:
        """Check if a radar detection falls inside a camera's image bounds."""
        u, v = self.radar_to_image(camera_name, range_m, azimuth_deg, elevation_deg)
        if math.isnan(u) or math.isnan(v):
            return False
        cam = self.cam_geo.cameras[camera_name]
        return 0 <= u < cam.image_width and 0 <= v < cam.image_height

    def radar_to_stereo_depth(self, range_m: float, azimuth_deg: float,
                              elevation_deg: float) -> tuple[float, float, float] | None:
        """Convert radar detection to stereo disparity space.

        The radar is centered between the stereo pair, so its range/azimuth
        map directly to the stereo depth (Z) and lateral (X) used by gridd's
        lazy reprojection.

        Returns:
            (X_right, Y_down, Z_forward) in meters, or None if behind camera.
        """
        P_world = radar_polar_to_world(range_m, azimuth_deg, elevation_deg, self.mount)
        # World (X=forward, Y=left, Z=up) -> stereo camera (X=right, Y=down, Z=forward)
        x_cam = -P_world[1]   # left -> right
        y_cam = -P_world[2]   # up -> down
        z_cam = P_world[0]    # forward -> forward
        if z_cam <= 0:
            return None
        return (x_cam, y_cam, z_cam)

    def stereo_depth_to_radar(self, x_cam: float, y_cam: float, z_cam: float) -> tuple[float, float, float]:
        """Convert stereo depth point to radar polar coordinates.

        Useful for checking which radar returns correspond to a stereo obstacle.
        """
        P_world = np.array([z_cam, -x_cam, -y_cam])  # camera -> world
        return world_to_radar_polar(P_world, self.mount)

    def associate_radar_with_stereo(self, radar_detections: list,
                                    stereo_points: np.ndarray,
                                    gate_m: float = 1.0) -> list[int]:
        """Associate radar detections with stereo 3D points.

        Args:
            radar_detections: list of (range_m, azimuth_deg, elevation_deg)
            stereo_points: Nx3 array of stereo points in camera frame (right, down, forward)
            gate_m: Cartesian association gate in meters

        Returns:
            List of stereo point indices matched to each radar detection,
            or -1 if no match.
        """
        matches = []
        for det in radar_detections:
            range_m, az, el = det
            cam_pt = self.radar_to_stereo_depth(range_m, az, el)
            if cam_pt is None:
                matches.append(-1)
                continue

            x_r, y_r, z_r = cam_pt
            dists = np.sqrt(
                (stereo_points[:, 0] - x_r)**2 +
                (stereo_points[:, 1] - y_r)**2 +
                (stereo_points[:, 2] - z_r)**2
            )
            best = int(np.argmin(dists))
            matches.append(best if dists[best] < gate_m else -1)
        return matches

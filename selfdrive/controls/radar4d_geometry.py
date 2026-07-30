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

The radar frame is identical to the world frame, so radar (range, azimuth,
elevation) converts directly to world (x, y, z).  Camera projection then
uses the existing CameraArrayGeometry.
"""

from __future__ import annotations

import json
import math
import os
from dataclasses import dataclass

import numpy as np

from openpilot.selfdrive.gridd.camera_geometry import CameraArrayGeometry
from openpilot.system.hardware.hw import Paths


# Extrinsics are user-refined (mount position/orientation on the vehicle), so
# they live at the application layer — unlike factory intrinsics, which are
# owned by the exopilot HAL.
EXTRINSICS_PATH = os.path.join(Paths.eop_data_root(), "calibration", "radar_extrinsics.json")


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
    x_r = range_m * cos_el * math.cos(az)
    y_r = range_m * cos_el * math.sin(az)
    z_r = range_m * math.sin(el)

    # Apply mounting rotation (yaw only for now; pitch/roll are small)
    yaw = math.radians(mount.yaw_deg)
    cy, sy = math.cos(yaw), math.sin(yaw)
    x_w = cy * x_r - sy * y_r + mount.x_m
    y_w = sy * x_r + cy * y_r + mount.y_m
    z_w = z_r + mount.z_m

    return np.array([x_w, y_w, z_w])


def world_to_radar_polar(point_world: np.ndarray,
                         mount: RadarMounting = RADAR_MOUNT) -> tuple[float, float, float]:
    """Convert world point back to radar polar coordinates.

    Returns:
        (range_m, azimuth_deg, elevation_deg)
    """
    rel = point_world - mount.position
    yaw = math.radians(mount.yaw_deg)
    cy, sy = math.cos(-yaw), math.sin(-yaw)
    x_r = cy * rel[0] - sy * rel[1]
    y_r = sy * rel[0] + cy * rel[1]
    z_r = rel[2]

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

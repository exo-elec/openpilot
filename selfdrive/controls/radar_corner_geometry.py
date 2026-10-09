"""BLE corner-radar pose registry and Radar2D vehicle-frame transform."""

from __future__ import annotations

import math
import os
from dataclasses import dataclass

import yaml

from openpilot.system.hardware.hw import Paths

SENSOR_REGISTRY_PATH = os.path.join(
  Paths.eop_data_root(), "calibration", "sensor_calibration.yaml")

CORNER_NAMES = {
  0: 'front_left', 1: 'front_right', 2: 'rear_left', 3: 'rear_right',
}

# Reserve the upper byte of CameraObject.trackId for BLE corner tracks. This
# avoids collisions between four node-local trackers and lets downstream
# camera/radar association identify the source without a cereal schema change.
CORNER_TRACK_TAG = 0xD200000000000000
CORNER_TRACK_TAG_MASK = 0xFF00000000000000


@dataclass(frozen=True)
class CornerPose:
  """Static radar pose relative to the vehicle motion/IMU frame."""
  x_m: float
  y_m: float
  z_m: float
  roll_deg: float
  pitch_deg: float
  yaw_deg: float
  confirmed: bool = False


def load_corner_poses(path: str = SENSOR_REGISTRY_PATH,
                      require_confirmed: bool = True) -> dict[int, CornerPose] | None:
  """Load usable poses; an uncalibrated corner does not disable the other three."""
  try:
    with open(path) as stream:
      data = yaml.safe_load(stream)
  except Exception:
    return None
  corners = data.get('corner_radars') if isinstance(data, dict) else None
  if not isinstance(corners, dict):
    return None

  poses = {}
  for corner_id, name in CORNER_NAMES.items():
    entry = corners.get(name)
    try:
      position = entry['position']
      rotation = entry['rotation']
      pose = CornerPose(
        x_m=float(position['x_m']), y_m=float(position['y_m']),
        z_m=float(position['z_m']),
        roll_deg=float(rotation.get('roll_deg', 0.0)),
        pitch_deg=float(rotation.get('pitch_deg', 0.0)),
        yaw_deg=float(rotation['yaw_deg']),
        confirmed=bool(entry.get('confirmed', False)),
      )
    except (TypeError, KeyError, ValueError):
      continue
    if require_confirmed and not pose.confirmed:
      continue
    poses[corner_id] = pose
  return poses or None


def encode_corner_track_id(corner_id: int, node_track_id: int) -> int:
  """Namespace one node-local BLE track ID in CameraObject.trackId."""
  return CORNER_TRACK_TAG | ((int(corner_id) & 0xFF) << 48) | (int(node_track_id) & 0x0000FFFFFFFFFFFF)


def is_corner_track_id(track_id: int) -> bool:
  """Return whether a CameraObject track came from a BLE corner radar."""
  return (int(track_id) & CORNER_TRACK_TAG_MASK) == CORNER_TRACK_TAG


def corner_id_from_track_id(track_id: int) -> int | None:
  """Return the physical corner ID encoded in a namespaced BLE track."""
  if not is_corner_track_id(track_id):
    return None
  return (int(track_id) >> 48) & 0xFF


def corner_local_to_vehicle_frame(range_m: float, azimuth_deg: float,
                                  pose: CornerPose | tuple[float, float, float]
                                  ) -> tuple[float, float]:
  """Apply mounting yaw and XY translation to one leveled BLE Radar2D track."""
  if isinstance(pose, CornerPose):
    x_m, y_m, yaw_deg = pose.x_m, pose.y_m, pose.yaw_deg
  else:
    x_m, y_m, yaw_deg = pose
  bearing = math.radians(azimuth_deg)
  yaw = math.radians(yaw_deg)
  sensor_x = range_m * math.cos(bearing)
  sensor_y = range_m * math.sin(bearing)
  return (
    x_m + sensor_x * math.cos(yaw) - sensor_y * math.sin(yaw),
    y_m + sensor_x * math.sin(yaw) + sensor_y * math.cos(yaw),
  )

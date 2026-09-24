"""ESP32_RADAR corner point cloud -> vehicle-frame radar4d points.

Pure helpers (no cereal/messaging import) shared by the `radar4d` producer
daemon (selfdrive/controls/radar4d.py) and gridd's `_fuse_radar4d()`.

Input: `hal.drivers.radar.radar4d.CornerFrame`s from `RadarCornerReceiver`
(ESP32_RADAR dev/v2 Radar4D chunks over WiFi UDP 47000, 02M only). Each
frame holds one corner's points in that corner's own sensor frame
(`RadarDetection`: range, azimuth +left, elevation +up, radial velocity,
relative SNR dB). Output: points in the vehicle frame, placed with the same
confirmed corner-pose registry the BLE Radar2D path uses
(radar_corner_geometry.load_corner_poses()).
"""

from __future__ import annotations

import math
from dataclasses import dataclass

from openpilot.selfdrive.controls.radar_corner_geometry import corner_local_to_vehicle_frame

CORNER_IDS = (0, 1, 2, 3)          # 0xFF (unresolved strap) is never placed
STALE_S = 0.25                     # drop a corner's cloud this long after its last frame
MAX_POINTS = 512                   # strongest points kept per publish (all corners)
STATIC_TOL_MPS = 0.5               # |measured - expected static Doppler| below this = static


@dataclass(frozen=True)
class VehiclePoint:
  corner: int
  range_m: float        # horizontal distance from the vehicle origin
  azimuth_deg: float    # vehicle frame, 0 = forward, +left
  elevation_deg: float  # sensor frame (mounting roll/pitch not applied)
  v_rel: float          # radial Doppler seen by the corner, negative = approaching
  snr_db: float         # relative (the Radar4D SNR is relative, not calibrated)
  is_static: bool
  x_m: float            # vehicle frame, forward
  y_m: float            # vehicle frame, left


def _pose_yaw_deg(pose) -> float:
  return pose.yaw_deg if hasattr(pose, 'yaw_deg') else float(pose[2])


def expected_static_vrel(bearing_deg: float, v_ego: float) -> float:
  """Radial velocity a stationary target shows to a sensor moving forward at
  v_ego, along vehicle-frame bearing `bearing_deg` (negative = closing)."""
  return -v_ego * math.cos(math.radians(bearing_deg))


def detection_to_vehicle(det, corner: int, pose, v_ego: float) -> VehiclePoint:
  """One corner-local RadarDetection -> VehiclePoint."""
  horizontal = det.range_m * math.cos(math.radians(det.elevation_deg))
  x_m, y_m = corner_local_to_vehicle_frame(horizontal, det.azimuth_deg, pose)
  bearing = det.azimuth_deg + _pose_yaw_deg(pose)   # sensor line of sight, vehicle frame
  static = abs(det.vel_mps - expected_static_vrel(bearing, v_ego)) < STATIC_TOL_MPS
  return VehiclePoint(
    corner=corner, range_m=math.hypot(x_m, y_m),
    azimuth_deg=math.degrees(math.atan2(y_m, x_m)),
    elevation_deg=float(det.elevation_deg), v_rel=float(det.vel_mps),
    snr_db=float(det.snr_db), is_static=static, x_m=x_m, y_m=y_m,
  )


class CornerCloudCache:
  """Latest point cloud per corner, with staleness."""

  def __init__(self) -> None:
    self._latest: dict[int, tuple[float, list]] = {}

  def update(self, frames, now: float) -> None:
    for f in frames:
      if f.corner_id in CORNER_IDS:
        self._latest[f.corner_id] = (now, list(f.detections))

  def fresh_corners(self, now: float) -> list[int]:
    return sorted(c for c, (t, _d) in self._latest.items() if now - t <= STALE_S)

  def points(self, poses: dict | None, v_ego: float, now: float) -> list[VehiclePoint]:
    """Fresh corners with a confirmed pose, strongest MAX_POINTS first."""
    out: list[VehiclePoint] = []
    for corner in self.fresh_corners(now):
      pose = (poses or {}).get(corner)
      if pose is None:
        continue
      out.extend(detection_to_vehicle(d, corner, pose, v_ego) for d in self._latest[corner][1])
    out.sort(key=lambda p: p.snr_db, reverse=True)
    return out[:MAX_POINTS]


# --- gridd fusion -------------------------------------------------------------

FUSE_MAX_POINTS = 256        # costmap stamps per gridd cycle
FUSE_MAX_RANGE_M = 30.0      # Radar4D points are int16 mm: +-32.767 m reach
FUSE_RADIUS_M = 0.3
FUSE_COST_MOVING = 0.9
FUSE_COST_STATIC = 0.7


def points_to_obstacles(points) -> list[tuple[float, float, float, float]]:
  """radar4d points (capnp Radar4DPoint or VehiclePoint-like) -> costmap
  stamps (dRel, yRel, radius_m, cost), strongest first, capped."""
  pts = sorted(points, key=lambda p: _get(p, 'snrDb', 'snr_db'), reverse=True)
  out = []
  for p in pts:
    rng = _get(p, 'rangM', 'range_m')
    if not 0.0 < rng <= FUSE_MAX_RANGE_M:
      continue
    az = math.radians(_get(p, 'azimuth', 'azimuth_deg'))
    static = bool(_get(p, 'isStatic', 'is_static'))
    out.append((rng * math.cos(az), rng * math.sin(az), FUSE_RADIUS_M,
                FUSE_COST_STATIC if static else FUSE_COST_MOVING))
    if len(out) >= FUSE_MAX_POINTS:
      break
  return out


def _get(p, capnp_name: str, py_name: str):
  return getattr(p, capnp_name) if hasattr(p, capnp_name) else getattr(p, py_name)

"""Simulated SideD — publishes sideDetections from CARLA ground truth.

Replaces the real `sided` daemon in simulation. Queries CARLA for vehicles
in the side-camera blind-spot zones and publishes `sideDetections` + `sideStatus`
so that `bsd.py` can perform camera-augmented blind-spot monitoring.

This gives us an Autoware-style surround-monitoring feature without LiDAR:
camera-based 360° object detection for blind-spot and cross-traffic alerts.
"""
import math
import time
import cereal.messaging as messaging

from openpilot.common.params import Params
from openpilot.common.realtime import Ratekeeper

# Side-camera FOV and detection parameters
SIDE_FOV_DEG = 120.0
SIDE_RANGE_MAX_M = 30.0
SIDE_RANGE_MIN_M = 1.0

# Blind-spot zone (vehicle frame, metres)
# lateral: 1.5–5.0 m from centerline
# longitudinal: −20 m (rear) to +5 m (front)
BS_LATERAL_MIN = 1.5
BS_LATERAL_MAX = 5.0
BS_LONG_REAR = -20.0
BS_LONG_FRONT = 5.0


class SimulatedSideD:
  """Simulates camera-based side detection for CARLA."""

  def __init__(self):
    self.pm = messaging.PubMaster(['sideDetections', 'sideStatus'])
    self.params = Params()
    self.enabled = self.params.get_bool("EOPSideCamerasEnabled")
    self.rk = Ratekeeper(20, print_delay_threshold=None)
    self.frame_id = 0

  def _get_blindspot_objects(self, world, vehicle) -> list[dict]:
    """Query CARLA for vehicles in side-camera blind-spot zones."""
    objects = []
    if world is None or vehicle is None:
      return objects

    ego_transform = vehicle.get_transform()
    ego_loc = ego_transform.location
    ego_yaw = math.radians(ego_transform.rotation.yaw)

    # Forward vector of ego
    cos_yaw = math.cos(ego_yaw)
    sin_yaw = math.sin(ego_yaw)

    for actor in world.get_actors().filter('vehicle.*'):
      if actor.id == vehicle.id:
        continue

      loc = actor.get_transform().location
      # Relative vector in world frame
      dx = loc.x - ego_loc.x
      dy = loc.y - ego_loc.y

      # Transform to ego vehicle frame: +x = forward, +y = left
      d_long = dx * cos_yaw + dy * sin_yaw   # longitudinal
      d_lat = -dx * sin_yaw + dy * cos_yaw   # lateral (positive = left)

      # Check if in blind-spot zone
      lat_abs = abs(d_lat)
      if not (BS_LATERAL_MIN <= lat_abs <= BS_LATERAL_MAX):
        continue
      if not (BS_LONG_REAR <= d_long <= BS_LONG_FRONT):
        continue

      # Estimate relative speed (simple finite difference would be better,
      # but for sim ground-truth we can read velocity directly)
      vel = actor.get_velocity()
      ego_vel = vehicle.get_velocity()
      # Relative velocity in longitudinal direction
      v_rel = (vel.x - ego_vel.x) * cos_yaw + (vel.y - ego_vel.y) * sin_yaw

      # Approximate distance to side camera (not ego center)
      # Side camera is at y = ±0.85, x = 0.7
      side_y = 0.85 if d_lat > 0 else -0.85
      side_dx = d_long - 0.7
      side_dy = d_lat - side_y
      cam_dist = math.hypot(side_dx, side_dy)

      if cam_dist > SIDE_RANGE_MAX_M or cam_dist < SIDE_RANGE_MIN_M:
        continue

      objects.append({
        'label': 'car',
        'confidence': 0.95,
        'x': d_long,        # longitudinal (positive = forward)
        'y': d_lat,         # lateral (positive = left)
        'z': 0.0,
        'width': 1.8,
        'length': 4.5,
        'v_rel': v_rel,
        'cam_dist': cam_dist,
        'side': 'left' if d_lat > 0 else 'right',
      })

    return objects

  def _publish(self, objects: list[dict], ts: int):
    """Publish sideDetections + sideStatus cereal messages."""
    msg = messaging.new_message('sideDetections', valid=True)
    msg.sideDetections.frameId = self.frame_id
    msg.sideDetections.timestamp = ts / 1e9
    msg.sideDetections.numTracks = len(objects)
    msg.sideDetections.cameraSource = "simulated"

    if objects:
      items = msg.sideDetections.init('detections', len(objects))
      for i, obj in enumerate(objects):
        items[i].className = obj['label']
        items[i].confidence = obj['confidence']
        items[i].x = obj['x']
        items[i].y = obj['y']
        items[i].cameraSource = obj['side']

    self.pm.send('sideDetections', msg)

    status = messaging.new_message('sideStatus', valid=True)
    ss = status.sideStatus
    ss.enabled = self.enabled
    ss.fault = False
    ss.faultReason = ""
    ss.consecutiveFailures = 0
    ss.numTracks = len(objects)
    ss.processingTimeMs = 0.0
    self.pm.send('sideStatus', status)

  def update(self, world, vehicle):
    if not self.enabled:
      return

    objects = self._get_blindspot_objects(world, vehicle)
    ts = int(time.monotonic() * 1e9)
    self._publish(objects, ts)
    self.frame_id += 1

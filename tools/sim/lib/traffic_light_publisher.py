"""CARLA traffic light state publisher for openpilot TLSC testing.

Queries CARLA traffic lights near the ego vehicle and publishes their
states as stereoObjects so the Traffic Light Speed Controller (TLSC)
can react in simulation.

This enables Autoware-style traffic-light scenario testing without
requiring a real traffic-light classifier.
"""
import math

import cereal.messaging as messaging

from openpilot.common.params import Params

# Traffic light detection zone (meters, forward of ego)
TL_MAX_DIST = 100.0
TL_MIN_DIST = 3.0
TL_LAT_THRESHOLD = 6.0  # max lateral offset from ego path

# CARLA → cereal state mapping (use raw enum ordinals)
# Cap'n Proto enum ordinals: unknown=0, red=1, yellow=2, green=3
CARLA_TO_CEREAL = {
  'Red': 1,
  'Yellow': 2,
  'Green': 3,
}


class TrafficLightPublisher:
  """Publishes CARLA traffic light states for TLSC testing."""

  def __init__(self):
    self.pm = messaging.PubMaster(['stereoObjects'])
    self.params = Params()
    self.enabled = self.params.get_bool("EOPTLSCEnabled")
    self.frame_id = 0

  def _get_nearest_traffic_light(self, world, vehicle) -> dict | None:
    """Find the nearest traffic light ahead of the ego vehicle."""
    if world is None or vehicle is None:
      return None

    ego_transform = vehicle.get_transform()
    ego_loc = ego_transform.location
    ego_yaw = math.radians(ego_transform.rotation.yaw)

    cos_yaw = math.cos(ego_yaw)
    sin_yaw = math.sin(ego_yaw)

    best_tl = None
    best_dist = float('inf')

    for actor in world.get_actors().filter('traffic.traffic_light*'):
      loc = actor.get_transform().location
      dx = loc.x - ego_loc.x
      dy = loc.y - ego_loc.y

      # Ego-frame coordinates
      d_long = dx * cos_yaw + dy * sin_yaw
      d_lat = -dx * sin_yaw + dy * cos_yaw

      # Must be ahead of ego and within lane width
      if d_long < TL_MIN_DIST or d_long > TL_MAX_DIST:
        continue
      if abs(d_lat) > TL_LAT_THRESHOLD:
        continue

      dist = math.hypot(dx, dy)
      if dist < best_dist:
        best_dist = dist
        state_name = str(actor.state).split('.')[-1]  # e.g. "Red"
        cereal_state = CARLA_TO_CEREAL.get(state_name, 0)
        best_tl = {
          'distance': float(d_long),
          'lateral': float(d_lat),
          'state': cereal_state,
          'confidence': 0.95,
        }

    return best_tl

  def update(self, world, vehicle):
    if not self.enabled:
      return

    tl = self._get_nearest_traffic_light(world, vehicle)

    msg = messaging.new_message('stereoObjects', valid=True)
    if tl is not None and tl['state'] != 3:
      # Only publish red/yellow lights (green is not actionable for TLSC)
      objs = msg.stereoObjects.init('objects', 1)
      objs[0].dRel = tl['distance']
      objs[0].yRel = tl['lateral']
      objs[0].trafficLightState = tl['state']
      objs[0].trafficLightConfidence = tl['confidence']
      # ObstacleType.trafficLight ordinal = 10
      objs[0].obstacleType = 10
    else:
      msg.stereoObjects.init('objects', 0)

    self.pm.send('stereoObjects', msg)
    self.frame_id += 1

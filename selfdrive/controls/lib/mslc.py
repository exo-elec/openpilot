"""
mslc.py - Map Speed Limit Controller (MSLC)

Adjusts target speed based on OSM speed limit data from MAPD.
Merged with FrogPilot SLC features:
  - Per-speed-range offsets (bucket-based)
  - Driver confirmation for limit changes
"""

from openpilot.common.params import Params
from openpilot.common.realtime import DT_MDL
from openpilot.common.swaglog import cloudlog
from openpilot.selfdrive.controls.lib.eop_utils import SpeedLimitOffset, SpeedLimitConfirmation


class MSLC:
  STATE_DISABLED = 0
  STATE_NO_DATA = 1
  STATE_ACTIVE = 2
  STATE_OVERRIDDEN = 3

  LOOKAHEAD_DISTANCE = 500
  OVERRIDE_TIMEOUT = 10.0
  TRANSITION_DISTANCE = 300
  ALERT_CHANGE_THRESHOLD_KMH = 10

  def __init__(self):
    self.params = Params()
    self.enabled = False
    self.state = self.STATE_DISABLED
    self.current_limit = None
    self.target_speed = None
    self.override_time = 0.0
    self.frame = 0
    self._prev_limit = None
    self._alert_text = ""
    self._prev_alert_text = ""

    self._offset = SpeedLimitOffset(self.params)
    self._confirmation = SpeedLimitConfirmation()
    self._update_params()

  def _update_params(self):
    self.enabled = self.params.get_bool("EOPMSLCEnabled")

  def _parse_speed_limit(self, maxspeed_tag):
    if maxspeed_tag is None:
      return None
    if isinstance(maxspeed_tag, str):
      numeric_part = ''.join(c for c in maxspeed_tag if c.isdigit())
      if numeric_part:
        return int(numeric_part)
    elif isinstance(maxspeed_tag, (int, float)):
      return int(maxspeed_tag)
    return None

  def _get_speed_limit(self, map_data):
    if not map_data:
      return None, None, float('inf')
    current_limit = None
    if hasattr(map_data, 'speedLimit') and map_data.speedLimit > 0:
      current_limit = int(map_data.speedLimit)
    upcoming_limit = current_limit
    distance_to_change = float('inf')
    if hasattr(map_data, 'nextSpeedLimit') and map_data.nextSpeedLimit > 0:
      upcoming_limit = int(map_data.nextSpeedLimit)
      if hasattr(map_data, 'nextSpeedLimitDistance'):
        distance_to_change = float(map_data.nextSpeedLimitDistance)
    return current_limit, upcoming_limit, distance_to_change

  def _calculate_target(self, v_ego_kmh, current_limit, upcoming_limit, distance):
    if current_limit is None:
      return None

    # Per-speed-range offset (m/s)
    limit_ms = current_limit / 3.6
    offset_ms = self._offset.get_offset_ms(limit_ms)
    target_ms = limit_ms + offset_ms

    # Lookahead: start slowing for upcoming lower limit
    if upcoming_limit < current_limit and distance < self.LOOKAHEAD_DISTANCE:
      blend = max(0.0, min(1.0, 1.0 - (distance / self.LOOKAHEAD_DISTANCE)))
      upcoming_ms = upcoming_limit / 3.6 + self._offset.get_offset_ms(upcoming_limit / 3.6)
      target_ms = target_ms * (1.0 - blend) + upcoming_ms * blend

    # Don't suggest speed increase if already significantly above (user override)
    if v_ego_kmh > (target_ms * 3.6) + 10:
      return None

    return target_ms * 3.6

  def update(self, map_data, v_ego, driver_overriding, t, nav_speed_limit_ms: float = 0.0):
    self.frame += 1
    if self.frame >= int(1.0 / DT_MDL):
      self.frame = 0
      self._update_params()
      self._offset.refresh(t)
      self._confirmation.refresh_params(self.params, t)

    if not self.enabled:
      return None, self.STATE_DISABLED

    if driver_overriding and self.state == self.STATE_ACTIVE:
      self.state = self.STATE_OVERRIDDEN
      self.override_time = t

    if self.state == self.STATE_OVERRIDDEN and t - self.override_time > self.OVERRIDE_TIMEOUT:
      self.state = self.STATE_ACTIVE

    current_limit, upcoming_limit, distance = self._get_speed_limit(map_data)
    if current_limit is None and nav_speed_limit_ms > 0:
      current_limit = int(nav_speed_limit_ms * 3.6)
      upcoming_limit = current_limit
      distance = float("inf")

    if current_limit is None:
      self.state = self.STATE_NO_DATA
      self.distance_to_limit = float('inf')
      return None, self.state

    self.current_limit = current_limit
    self.upcoming_limit = upcoming_limit
    self.distance_to_limit = distance
    self.state = self.STATE_ACTIVE

    # Alert on meaningful limit change
    if (self._prev_limit is not None
        and abs(current_limit - self._prev_limit) >= self.ALERT_CHANGE_THRESHOLD_KMH):
      direction = "▼" if current_limit < self._prev_limit else "▲"
      self._alert_text = f"Speed limit {direction} {current_limit} km/h"
      if self._alert_text != self._prev_alert_text:
        self._prev_alert_text = self._alert_text
        cloudlog.info(f"MSLC: {self._alert_text}")
    self._prev_limit = current_limit

    # Calculate raw target with per-range offsets
    v_ego_kmh = v_ego * 3.6
    target_kmh = self._calculate_target(v_ego_kmh, current_limit, upcoming_limit, distance)
    if target_kmh is None:
      return None, self.state

    # Apply confirmation logic
    target_ms = target_kmh / 3.6
    confirmed_ms = self._confirmation.update(target_ms, driver_overriding, t)
    if confirmed_ms is None:
      return None, self.state

    self.target_speed = confirmed_ms
    return self.target_speed, self.state

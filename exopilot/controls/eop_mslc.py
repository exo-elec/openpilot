"""MSLC - Map Speed Limit Controller (posted limit from OSM map data or the navigation limit, per-range offsets, confirmation).

Moved from EOP10's `selfdrive/controls/lib/mslc.py` and the SpeedLimitOffset/SpeedLimitConfirmation helpers of `eop_utils.py`,
logic unchanged (golden-tested against recorded outputs of the original). Configuration is passed in instead of read from Params.
Behaviour to know: `_calculate_target` returns None while the car is more than 10 km/h ABOVE the target (read as a driver override),
so MSLC does not slow a car that is well over a new, lower limit. The target is a cap: callers take the minimum with the cruise speed.
Pure: no cereal, no Params.
"""
from __future__ import annotations

class SpeedLimitOffset:
  """Per-speed-range offset calculator (merged from FrogPilot SLC).

  Replaces global percent+fixed offset with bucket-based offsets that
  vary by posted speed limit. E.g., +5 km/h at 50 km/h, +10 at 120 km/h.
  """

  # Bucket edges in m/s: [0, 29, 49, 59, 79, 99, 119, 140] km/h
  BUCKET_EDGES_MS = [0.0, 8.1, 13.6, 16.4, 21.9, 27.5, 33.1, 38.9]

  def __init__(self, offsets_kph=None):
    self._offsets = [0.0] * (len(self.BUCKET_EDGES_MS) - 1)
    self.set_offsets_kph(offsets_kph or [])

  def set_offsets_kph(self, offsets_kph):
    """Per-bucket offsets in km/h (what the EOPSLCOffset1..7 params hold); missing or invalid entries are 0."""
    for i in range(len(self._offsets)):
      try:
        self._offsets[i] = float(offsets_kph[i]) * (1000.0 / 3600.0) if i < len(offsets_kph) and offsets_kph[i] is not None else 0.0
      except (ValueError, TypeError):
        self._offsets[i] = 0.0

  def refresh(self, now: float = 0.0):
    pass     # configuration is pushed in with set_offsets_kph

  def get_offset_ms(self, limit_ms: float) -> float:
    """Return offset in m/s for the given speed limit."""
    for i in range(len(self.BUCKET_EDGES_MS) - 1):
      if self.BUCKET_EDGES_MS[i] <= limit_ms < self.BUCKET_EDGES_MS[i + 1]:
        return self._offsets[i]
    return self._offsets[-1] if self._offsets else 0.0


class SpeedLimitConfirmation:
  """Require driver confirmation before applying speed limit changes.

  Merged from FrogPilot speed_limit_controller.py confirmation logic.
  """

  CONFIRMATION_TIMEOUT = 30.0  # seconds before auto-confirm
  MIN_CHANGE_MS = 2.78  # 10 km/h minimum change to trigger confirmation

  def __init__(self):
    self._confirmed_limit_ms: float | None = None
    self._unconfirmed_limit_ms: float | None = None
    self._change_time: float = 0.0
    self._confirmation_lower = False
    self._confirmation_higher = False
    self._last_param_t = 0.0

  def configure(self, confirm_lower: bool, confirm_higher: bool):
    self._confirmation_lower = bool(confirm_lower)
    self._confirmation_higher = bool(confirm_higher)

  def refresh_params(self, params, now: float):
    pass     # configuration is pushed in with configure()

  def update(self, new_limit_ms: float | None, driver_overriding: bool, now: float) -> float | None:
    """Return the speed limit to apply, or None if not confirmed yet."""
    if new_limit_ms is None:
      self._confirmed_limit_ms = None
      self._unconfirmed_limit_ms = None
      return None

    if self._confirmed_limit_ms is None:
      self._confirmed_limit_ms = new_limit_ms
      return new_limit_ms

    delta = new_limit_ms - self._confirmed_limit_ms
    if abs(delta) < self.MIN_CHANGE_MS:
      return self._confirmed_limit_ms

    is_lower = delta < 0
    needs_confirm = (is_lower and self._confirmation_lower) or (not is_lower and self._confirmation_higher)

    if not needs_confirm:
      self._confirmed_limit_ms = new_limit_ms
      return new_limit_ms

    # New limit needs confirmation
    if self._unconfirmed_limit_ms != new_limit_ms:
      self._unconfirmed_limit_ms = new_limit_ms
      self._change_time = now

    # Driver override (gas press) acts as confirmation
    if driver_overriding:
      self._confirmed_limit_ms = new_limit_ms
      self._unconfirmed_limit_ms = None
      return new_limit_ms

    # Auto-confirm after timeout
    if now - self._change_time > self.CONFIRMATION_TIMEOUT:
      self._confirmed_limit_ms = new_limit_ms
      self._unconfirmed_limit_ms = None
      return new_limit_ms

    # Not confirmed yet — keep old limit
    return self._confirmed_limit_ms

  def is_unconfirmed(self) -> bool:
    return self._unconfirmed_limit_ms is not None

  def unconfirmed_limit(self) -> float | None:
    return self._unconfirmed_limit_ms




class MSLC:
  STATE_DISABLED = 0
  STATE_NO_DATA = 1
  STATE_ACTIVE = 2
  STATE_OVERRIDDEN = 3

  LOOKAHEAD_DISTANCE = 500
  OVERRIDE_TIMEOUT = 10.0
  TRANSITION_DISTANCE = 300
  ALERT_CHANGE_THRESHOLD_KMH = 10

  def __init__(self, enabled: bool = True, offsets_kph=None, confirm_lower: bool = False, confirm_higher: bool = False):
    self.enabled = enabled
    self.state = self.STATE_DISABLED
    self.current_limit = None
    self.target_speed = None
    self.override_time = 0.0
    self.frame = 0
    self._prev_limit = None
    self._alert_text = ""
    self._prev_alert_text = ""

    self._offset = SpeedLimitOffset(offsets_kph)
    self._confirmation = SpeedLimitConfirmation()
    self._confirmation.configure(confirm_lower, confirm_higher)

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
      self._prev_alert_text = self._alert_text      # `alert_text` carries the latest change notice for the caller
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

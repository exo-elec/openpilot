"""In-memory trip accumulator with no persistence or extra daemon."""

from dataclasses import dataclass


@dataclass(frozen=True)
class TripSnapshot:
  distance_m: float
  onroad_time_s: float
  engaged_time_s: float
  engagement_ratio: float
  max_accel: float
  override_free_distance_m: float


class NGPTripStats:
  def __init__(self):
    self.distance_m = 0.0
    self.onroad_time_s = 0.0
    self.engaged_time_s = 0.0
    self.max_accel = 0.0
    self.override_free_distance_m = 0.0
    self._current_override_free_m = 0.0

  def update(self, v_ego: float, a_ego: float, engaged: bool,
             driver_override: bool, dt: float) -> TripSnapshot:
    dt = max(0.0, min(1.0, float(dt)))
    distance = max(0.0, float(v_ego)) * dt
    self.distance_m += distance
    self.onroad_time_s += dt
    self.engaged_time_s += dt if engaged else 0.0
    self.max_accel = max(self.max_accel, float(a_ego))
    if engaged and not driver_override:
      self._current_override_free_m += distance
      self.override_free_distance_m = max(self.override_free_distance_m, self._current_override_free_m)
    else:
      self._current_override_free_m = 0.0
    ratio = self.engaged_time_s / self.onroad_time_s if self.onroad_time_s > 0.0 else 0.0
    return TripSnapshot(self.distance_m, self.onroad_time_s, self.engaged_time_s,
                        ratio, self.max_accel, self.override_free_distance_m)

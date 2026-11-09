"""Pause lateral assistance while a turn signal is on at low speed (idea from sunnypilot's "blinker pause lateral").

Below the lane-change speed a turn signal means a turn or a parking manoeuvre, not a lane change, and lane centering
would fight the driver. Lateral is paused while one signal is on below `min_speed_mps`, and for `hold_s` after the signal
goes off so the turn can finish. `min_speed_mps <= 0` disables it. Pure policy; the caller decides what "paused" does.
"""


class NGPBlinkerPause:
  def __init__(self, min_speed_mps: float, hold_s: float = 1.5, dt: float = 0.01):
    self.min_speed = float(min_speed_mps)
    self.hold_s = float(hold_s)
    self.dt = float(dt)
    self._hold = 0.0

  def update(self, v_ego: float, left_blinker: bool, right_blinker: bool) -> bool:
    """True while lateral assistance should be paused."""
    if self.min_speed <= 0.0 or v_ego >= self.min_speed:
      self._hold = 0.0
      return False
    if left_blinker != right_blinker:
      self._hold = self.hold_s
      return True
    if self._hold > 0.0:
      self._hold = max(0.0, self._hold - self.dt)
      return True
    return False

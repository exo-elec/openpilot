"""Lane turn desire: tell the driving model about a turn at low speed when the driver signals one.

Idea from sunnypilot's `lane_turn_desire`, written from our own code: `modeld` feeds the model an 8-wide one-hot `desire` as a
pulse on its rising edge, and `DesireHelper` only ever produces the lane-change slots, so the `turnLeft` / `turnRight` slots are
never used. This returns the direction while exactly one signal is on, lateral is active, no lane change is running and the speed
is between a floor and a driver-chosen ceiling. Whether a given model reacts to the pulse must be measured on replays
(see `nagaspilot/docs/DRIVE_MODE_AND_TURN_DESIRE_STUDY.md`), so the feature is off unless a ceiling is set.
"""
MIN_SPEED_MPS = 2.0


def turn_signal_direction(left_blinker: bool, right_blinker: bool, eligible: bool) -> str | None:
  """Resolve one signal after the caller applies its product-specific turn gates."""
  if not eligible or left_blinker == right_blinker:
    return None
  return 'left' if left_blinker else 'right'


class NGPTurnDesire:
  def __init__(self, max_speed_mps: float, min_speed_mps: float = MIN_SPEED_MPS):
    self.max_speed = float(max_speed_mps)
    self.min_speed = float(min_speed_mps)

  def update(self, v_ego: float, left_blinker: bool, right_blinker: bool, lateral_active: bool, lane_change_active: bool) -> str | None:
    """'left', 'right' or None."""
    if self.max_speed <= 0.0 or not lateral_active or lane_change_active:
      return None
    return turn_signal_direction(left_blinker, right_blinker, self.min_speed <= v_ego < self.max_speed)

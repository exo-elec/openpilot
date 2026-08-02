"""Safety Offset Control proposal; never directly changes the desired path."""

from dataclasses import dataclass

from openpilot.nagaspilot.speed_zones import HIGHWAY_SPEED_MPS


@dataclass(frozen=True)
class SOCInput:
  v_ego: float
  left_threat: bool
  right_threat: bool
  lane_line_y: tuple[tuple[float, ...], ...]
  lane_line_probs: tuple[float, ...]
  lane_line_stds: tuple[float, ...]


@dataclass(frozen=True)
class SOCResult:
  offset_m: float
  active_suggestion: bool
  geometry_valid: bool
  reason: str
  control_authority: bool = False


class NGP10SOC:
  OFFSET_M = 0.20

  def __init__(self, confirmation_frames: int = 20):
    self.confirmation_frames = max(1, int(confirmation_frames))
    self._valid_frames = 0

  @staticmethod
  def _geometry_valid(sample: SOCInput):
    if len(sample.lane_line_y) < 4 or len(sample.lane_line_probs) < 4 or len(sample.lane_line_stds) < 4:
      return False
    if min(sample.lane_line_probs[:4]) < 0.60 or max(sample.lane_line_stds[:4]) > 0.35:
      return False
    try:
      line_y = [line[5] for line in sample.lane_line_y[:4]]
    except IndexError:
      return False
    widths = [line_y[index + 1] - line_y[index] for index in range(3)]
    return all(2.8 <= width <= 3.6 for width in widths)

  def update(self, sample: SOCInput) -> SOCResult:
    geometry_valid = self._geometry_valid(sample)
    one_sided = sample.left_threat != sample.right_threat
    eligible = sample.v_ego >= HIGHWAY_SPEED_MPS and geometry_valid and one_sided
    self._valid_frames = self._valid_frames + 1 if eligible else 0
    if self._valid_frames < self.confirmation_frames:
      return SOCResult(0.0, False, geometry_valid, "confirmation_or_gate")
    offset = -self.OFFSET_M if sample.left_threat else self.OFFSET_M
    return SOCResult(offset, True, True, "one_sided_threat")

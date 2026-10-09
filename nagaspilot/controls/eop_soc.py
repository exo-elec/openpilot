"""Safety Offset Control proposal; never directly changes the desired path."""

from dataclasses import dataclass

from nagaspilot.speed_zones import HIGHWAY_SPEED_MPS
from nagaspilot.controls.soc_policy import SOCInput, geometry_valid


@dataclass(frozen=True)
class SOCResult:
  offset_m: float
  active_suggestion: bool
  geometry_valid: bool
  reason: str
  control_authority: bool = False


class EOPSOC:
  OFFSET_M = 0.20

  def __init__(self, confirmation_frames: int = 20):
    self.confirmation_frames = max(1, int(confirmation_frames))
    self._valid_frames = 0

  _geometry_valid = staticmethod(geometry_valid)

  def update(self, sample: SOCInput) -> SOCResult:
    geometry_valid = self._geometry_valid(sample)
    one_sided = sample.left_threat != sample.right_threat
    eligible = sample.v_ego >= HIGHWAY_SPEED_MPS and geometry_valid and one_sided
    self._valid_frames = self._valid_frames + 1 if eligible else 0
    if self._valid_frames < self.confirmation_frames:
      return SOCResult(0.0, False, geometry_valid, "confirmation_or_gate")
    offset = -self.OFFSET_M if sample.left_threat else self.OFFSET_M
    return SOCResult(offset, True, True, "one_sided_threat")

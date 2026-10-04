"""SOC — Smart Offset Control: move a little away from vehicles beside you while staying in the lane.

Basic version, comma-3 inputs only: a vehicle is "beside" when the car's own blind-spot sensor says so, or when the
camera model sees a vehicle in the neighbouring lane just ahead. The offset is a small, slow bias that is only allowed on
the highway, with a well-formed lane (four confident, evenly spaced lane lines) and a threat on exactly one side that has
persisted for a while. It never fights lane centering: it is added to the desired curvature as a tiny bias.
ExoPilot extends it with tracked objects from its own cameras and radar (closing speed, large vehicles) in `pathd`.

Sign: `offset_m` is positive to the LEFT (the curvature convention); a threat on the left gives a negative offset.
modelV2 y is right-positive, so model leads are flipped once in `threats_from`.
"""
from dataclasses import dataclass

from nagaspilot.speed_zones import HIGHWAY_SPEED_MPS

ADJACENT_Y_MIN = 1.5  # m, a model lead beyond this lateral distance is in the neighbouring lane
ADJACENT_Y_MAX = 4.5  # m
ADJACENT_X_MAX = 30.0  # m ahead
MIN_LEAD_PROB = 0.5
RAMP_M_PER_S = 0.10  # the offset moves slowly in and out, so neither the start nor the release is a step
DT = 0.05  # s, one model frame
BIAS_PER_METER = 0.002  # 1/m of curvature bias per metre of offset (a 0.2 m offset -> 0.0004 1/m)


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


def threats_from(carstate, model_v2) -> tuple[bool, bool]:
  """(left_threat, right_threat) from the native blind-spot flags and neighbouring-lane vision leads."""
  left = bool(getattr(carstate, 'leftBlindspot', False))
  right = bool(getattr(carstate, 'rightBlindspot', False))
  for lead in getattr(model_v2, 'leadsV3', ()) or ():
    if lead.prob < MIN_LEAD_PROB or not (0.0 < lead.x[0] < ADJACENT_X_MAX):
      continue
    y_left = -lead.y[0]  # modelV2 y is right-positive
    if ADJACENT_Y_MIN < y_left < ADJACENT_Y_MAX:
      left = True
    elif -ADJACENT_Y_MAX < y_left < -ADJACENT_Y_MIN:
      right = True
  return left, right


class NGPSOC:
  OFFSET_M = 0.20

  def __init__(self, confirmation_frames: int = 20):
    self.confirmation_frames = max(1, int(confirmation_frames))
    self._valid_frames = 0
    self._offset = 0.0

  @staticmethod
  def geometry_valid(sample: SOCInput) -> bool:
    if len(sample.lane_line_y) < 4 or len(sample.lane_line_probs) < 4 or len(sample.lane_line_stds) < 4:
      return False
    if min(sample.lane_line_probs[:4]) < 0.60 or max(sample.lane_line_stds[:4]) > 0.35:
      return False
    try:
      line_y = [line[5] for line in sample.lane_line_y[:4]]
    except IndexError:
      return False
    widths = [line_y[i + 1] - line_y[i] for i in range(3)]
    return all(2.8 <= w <= 3.6 for w in widths)

  def update(self, sample: SOCInput) -> SOCResult:
    geometry_valid = self.geometry_valid(sample)
    one_sided = sample.left_threat != sample.right_threat
    eligible = sample.v_ego >= HIGHWAY_SPEED_MPS and geometry_valid and one_sided
    self._valid_frames = self._valid_frames + 1 if eligible else 0
    confirmed = self._valid_frames >= self.confirmation_frames
    target = (-self.OFFSET_M if sample.left_threat else self.OFFSET_M) if confirmed else 0.0
    step = RAMP_M_PER_S * DT
    self._offset += max(-step, min(step, target - self._offset))
    if abs(self._offset) < 1e-9:
      return SOCResult(0.0, False, geometry_valid, "confirmation_or_gate")
    return SOCResult(self._offset, True, geometry_valid, "one_sided_threat" if confirmed else "releasing")


def curvature_bias(result: SOCResult) -> float:
  """Curvature delta (1/m, positive = left) for the offset."""
  return result.offset_m * BIAS_PER_METER if result.active_suggestion else 0.0

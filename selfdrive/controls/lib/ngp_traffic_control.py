"""Non-controlling traffic-light/stop-sign approach proposal."""

from dataclasses import dataclass
from enum import IntEnum


class TrafficControlState(IntEnum):
  UNKNOWN = 0
  GREEN = 1
  YELLOW = 2
  RED = 3
  STOP_SIGN = 4


@dataclass(frozen=True)
class TrafficControlObservation:
  state: TrafficControlState
  distance_m: float
  confidence: float


@dataclass(frozen=True)
class TrafficControlResult:
  stop_suggestion: bool
  target_distance_m: float | None
  required_decel: float
  reason: str
  control_authority: bool = False


class NGP10TrafficControl:
  MIN_DISTANCE = 3.0
  MAX_DISTANCE = 80.0
  MIN_CONFIDENCE = 0.60
  COMFORTABLE_DECEL = 2.0

  def evaluate(self, v_ego: float, observations, has_lead: bool) -> TrafficControlResult:
    candidates = tuple(o for o in (observations or ())
                       if o.state in (TrafficControlState.RED, TrafficControlState.STOP_SIGN)
                       and o.confidence >= self.MIN_CONFIDENCE
                       and self.MIN_DISTANCE <= o.distance_m <= self.MAX_DISTANCE)
    if has_lead:
      return TrafficControlResult(False, None, 0.0, "lead_authoritative")
    if not candidates:
      return TrafficControlResult(False, None, 0.0, "no_valid_control")
    target = min(candidates, key=lambda observation: observation.distance_m)
    required = v_ego * v_ego / (2.0 * max(target.distance_m - 2.0, 1.0))
    if required > self.COMFORTABLE_DECEL:
      return TrafficControlResult(False, target.distance_m, required, "outside_comfort_envelope")
    return TrafficControlResult(True, target.distance_m, required, target.state.name.lower())

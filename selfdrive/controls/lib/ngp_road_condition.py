"""Pure road-condition policy without EOP10's OpenCV/RK perception path."""

from dataclasses import dataclass
from enum import IntEnum


class RoadCondition(IntEnum):
  UNKNOWN = 0
  DRY = 1
  WET = 2
  SNOW = 3
  ICE = 4


@dataclass(frozen=True)
class RoadConditionObservation:
  condition: RoadCondition
  confidence: float
  source: str


@dataclass(frozen=True)
class RoadConditionResult:
  condition: RoadCondition
  confidence: float
  speed_factor: float
  accel_max: float
  decel_min: float
  source: str
  control_authority: bool = False


class NGP10RoadCondition:
  FACTORS = {
    RoadCondition.UNKNOWN: (1.0, 2.0, -3.48),
    RoadCondition.DRY: (1.0, 2.0, -3.48),
    RoadCondition.WET: (0.85, 1.4, -2.8),
    RoadCondition.SNOW: (0.65, 0.9, -2.0),
    RoadCondition.ICE: (0.45, 0.6, -1.4),
  }

  def evaluate(self, observations) -> RoadConditionResult:
    valid = tuple(o for o in (observations or ()) if o.confidence >= 0.5)
    selected = max(valid, key=lambda o: int(o.condition), default=None)
    if selected is None:
      selected = RoadConditionObservation(RoadCondition.UNKNOWN, 0.0, "none")
    speed_factor, accel_max, decel_min = self.FACTORS[selected.condition]
    return RoadConditionResult(selected.condition, selected.confidence, speed_factor,
                               accel_max, decel_min, selected.source)

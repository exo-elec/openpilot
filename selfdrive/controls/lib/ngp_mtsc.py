"""NGP10 MTSC pure curvature evaluator (non-controlling shadow output)."""
from dataclasses import dataclass
from enum import Enum
from math import sqrt


class MTSCState(Enum):
  DISABLED = "disabled"
  APPROACHING = "approaching"
  HANDOVER = "handover"


@dataclass(frozen=True)
class MTSCResult:
  target_speed: float | None
  state: MTSCState
  distance: float | None
  curvature: float


class NGPMTSC:
  MIN_DISTANCE = 150.0
  MAX_DISTANCE = 500.0
  MIN_CURVATURE = 0.001
  MAX_CURVATURE = 0.1
  COMFORT_LAT_ACC = 1.8

  def __init__(self, enabled=True, comfort_lat_acc=COMFORT_LAT_ACC):
    self.enabled = enabled
    self.comfort_lat_acc = max(1.2, min(2.5, float(comfort_lat_acc)))
    self.state = MTSCState.DISABLED

  def update(self, curves):
    """Evaluate ``[(distance_m, curvature_1_per_m), ...]`` without map APIs."""
    if not self.enabled:
      self.state = MTSCState.DISABLED
      return MTSCResult(None, self.state, None, 0.0)
    valid = [(float(d), max(self.MIN_CURVATURE, min(self.MAX_CURVATURE, abs(float(k)))))
             for d, k in (curves or []) if self.MIN_DISTANCE <= float(d) <= self.MAX_DISTANCE]
    if not valid:
      self.state = MTSCState.DISABLED
      return MTSCResult(None, self.state, None, 0.0)
    distance, curvature = max(valid, key=lambda item: item[1])
    if distance <= self.MIN_DISTANCE:
      self.state = MTSCState.HANDOVER
      return MTSCResult(None, self.state, distance, curvature)
    self.state = MTSCState.APPROACHING
    return MTSCResult(sqrt(self.comfort_lat_acc / curvature), self.state, distance, curvature)

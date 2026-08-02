"""Non-controlling adaptive coasting proposal superseding EDP10 ACM."""

from dataclasses import dataclass
from math import sin


@dataclass(frozen=True)
class CoastingInput:
  v_ego: float
  v_cruise: float
  pitch_rad: float | None = None
  lead_distance: float | None = None
  lead_v_rel: float | None = None
  user_longitudinal_override: bool = False
  downhill_only: bool = False


@dataclass(frozen=True)
class CoastingResult:
  coast_suggestion: bool
  minimum_brake_mps2: float
  reason: str
  control_authority: bool = False


class NGP10Coasting:
  """Suggest mild-deceleration suppression without touching planner output."""

  def evaluate(self, sample: CoastingInput) -> CoastingResult:
    if sample.user_longitudinal_override:
      return CoastingResult(False, 0.0, "driver_override")
    if sample.v_cruise <= 0.0 or sample.v_ego <= sample.v_cruise * 0.9:
      return CoastingResult(False, 0.0, "below_cruise_gate")
    if sample.downhill_only and (sample.pitch_rad is None or sin(sample.pitch_rad) >= -0.04):
      return CoastingResult(False, 0.0, "downhill_gate")

    if sample.lead_distance is not None:
      closing_speed = max(0.0, -(sample.lead_v_rel or 0.0))
      ttc = sample.lead_distance / closing_speed if closing_speed > 0.1 else float("inf")
      if ttc < 3.5:
        return CoastingResult(False, 0.0, "lead_ttc")

    # Mirrors the useful EDP behavior as an observable proposal only.
    return CoastingResult(True, -0.5, "coast_available")

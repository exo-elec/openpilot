"""Turn a `pathAdjust` message into actuation requests: a slewed curvature bias and a cruise-speed cap.

Both are bounded and only the speed side is one-way: the offset is clamped (+-0.6 m), slewed
slowly in and out like SOC, and zeroed whenever the driver or a lane change is in charge; the
speed cap only ever lowers v_cruise and never goes below a floor. No braking authority.
Pure: no cereal, no Params.
"""
import math

BIAS_PER_METER = 0.002    # 1/m of curvature per metre of offset: the same gain as ngp_soc.BIAS_PER_METER (not imported: EOP10 has no ngp_soc)
MAX_OFFSET_M = 0.6
RAMP_M_PER_S = 0.15
MIN_SPEED_FLOOR = 8.3     # m/s, same floor as BRSC / cut-in
MIN_V_EGO = 8.0


class PathAdjustFollower:
  def __init__(self):
    self.offset = 0.0

  def reset(self) -> None:
    self.offset = 0.0

  def update(self, offset_cmd: float, fresh: bool, allowed: bool, dt: float) -> float:
    """Return the slewed offset (m, left positive). `allowed` is False under driver override, a blinker or a lane change."""
    target = 0.0
    if fresh and allowed and math.isfinite(offset_cmd):
      target = min(max(offset_cmd, -MAX_OFFSET_M), MAX_OFFSET_M)
    step = RAMP_M_PER_S * max(dt, 0.0)
    self.offset += min(max(target - self.offset, -step), step)
    return self.offset

  def curvature_delta(self) -> float:
    """Curvature bias (1/m, positive = left) for the current offset."""
    return self.offset * BIAS_PER_METER


def speed_cap(v_ego: float, factor: float, fresh: bool) -> float | None:
  """Cruise-speed cap from the message's speed factor, or None when nothing is asked."""
  if not fresh or not math.isfinite(factor) or not math.isfinite(v_ego) or v_ego < MIN_V_EGO or factor >= 1.0:
    return None
  factor = max(factor, 0.0)
  return min(v_ego, max(v_ego * factor, MIN_SPEED_FLOOR))

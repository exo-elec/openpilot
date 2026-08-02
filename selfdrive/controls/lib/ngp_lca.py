"""Human-nudge lane-change proposal with radar, BSM, DM, and edge gates."""

from dataclasses import dataclass
from enum import IntEnum

from nagaspilot.speed_zones import CITY_SPEED_MPS
from openpilot.selfdrive.controls.lib.ngp_radar import RadarZones


class LCADirection(IntEnum):
  NONE = 0
  LEFT = 1
  RIGHT = 2


class LCAState(IntEnum):
  OFF = 0
  PRE_CHANGE = 1
  STARTING = 2
  FINISHING = 3


@dataclass(frozen=True)
class LCAInput:
  enabled: bool
  v_ego: float
  left_blinker: bool = False
  right_blinker: bool = False
  driver_nudge: bool = False
  steering_override: bool = False
  driver_attentive: bool = True
  left_lane_available: bool = True
  right_lane_available: bool = True
  left_road_edge: bool = False
  right_road_edge: bool = False
  auto_lane_change: bool = False
  lane_change_complete: bool = False
  dt: float = 0.05


@dataclass(frozen=True)
class LCAResult:
  state: LCAState
  direction: LCADirection
  safe_to_start: bool
  desire_suggestion: bool
  control_authority: bool
  blocked_reasons: tuple[str, ...]


class NGPLCA:
  AUTO_DELAY = 3.0

  def __init__(self):
    self.state = LCAState.OFF
    self.direction = LCADirection.NONE
    self._pre_time = 0.0

  def _requested_direction(self, sample: LCAInput):
    if sample.left_blinker == sample.right_blinker:
      return LCADirection.NONE
    return LCADirection.LEFT if sample.left_blinker else LCADirection.RIGHT

  def update(self, sample: LCAInput, zones: RadarZones) -> LCAResult:
    requested = self._requested_direction(sample)
    if not sample.enabled or sample.steering_override or requested is LCADirection.NONE:
      self.state = LCAState.OFF
      self.direction = LCADirection.NONE
      self._pre_time = 0.0
      reason = "disabled_or_cancelled" if not sample.enabled else "no_single_blinker"
      return LCAResult(self.state, self.direction, False, False, False, (reason,))

    if requested is not self.direction:
      self.direction = requested
      self.state = LCAState.PRE_CHANGE
      self._pre_time = 0.0

    self._pre_time += max(0.0, sample.dt)
    blocked = []
    if sample.v_ego < CITY_SPEED_MPS:
      blocked.append("below_city_speed")
    if not sample.driver_attentive:
      blocked.append("driver_monitoring")
    if self.direction is LCADirection.LEFT:
      if not sample.left_lane_available:
        blocked.append("lane_unavailable")
      if sample.left_road_edge:
        blocked.append("road_edge")
      if zones.lca_blocked_left:
        blocked.append("radar_or_blindspot")
    else:
      if not sample.right_lane_available:
        blocked.append("lane_unavailable")
      if sample.right_road_edge:
        blocked.append("road_edge")
      if zones.lca_blocked_right:
        blocked.append("radar_or_blindspot")

    safe = not blocked
    requested_start = sample.driver_nudge or (sample.auto_lane_change and self._pre_time >= self.AUTO_DELAY)
    if self.state is LCAState.PRE_CHANGE and safe and requested_start:
      self.state = LCAState.STARTING
    elif self.state is LCAState.STARTING:
      if not safe:
        self.state = LCAState.PRE_CHANGE
      elif sample.lane_change_complete:
        self.state = LCAState.FINISHING

    return LCAResult(
      state=self.state,
      direction=self.direction,
      safe_to_start=safe,
      desire_suggestion=self.state in (LCAState.STARTING, LCAState.FINISHING),
      control_authority=False,
      blocked_reasons=tuple(blocked),
    )

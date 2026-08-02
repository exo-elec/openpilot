"""NGP10 DLON trigger evaluator (shadow only).

This keeps EOP10's useful trigger policy while leaving stock longitudinal
planning untouched. Inputs are primitive values so the module is replayable on
comma 3 without Params, cereal, radar, or actuator integration.
"""
from dataclasses import dataclass
from enum import Enum


class DLONMode(Enum):
  CHILL = "chill"
  EXPERIMENTAL = "experimental"
  AUTO = "auto"


@dataclass(frozen=True)
class DLONInput:
  v_ego: float
  has_lead: bool = False
  lead_delta_v: float = 0.0
  turn_signal: bool = False
  curve_lat_acc: float = 0.0
  should_stop: bool = False
  traffic_control: bool = False
  nav_distance: float | None = None
  mpc_fcw: bool = False


@dataclass(frozen=True)
class DLONResult:
  mode: DLONMode
  e2e_suggestion: bool
  triggers: tuple[str, ...]
  force_stop_suggestion: bool


class NGP10DLON:
  """Hysteretic, non-controlling E2E/ACC trigger state."""
  LOW_SPEED = 12.0
  HIGHWAY_SPEED = 24.0
  SLOW_LEAD_DELTA = -5.0
  CURVE_LAT_ACC = 1.2
  NAV_DISTANCE = 50.0

  def __init__(self, mode=DLONMode.AUTO, enter_frames=10, exit_frames=40):
    self.mode = mode
    self.enter_frames = enter_frames
    self.exit_frames = exit_frames
    self.e2e_suggestion = False
    self._positive = 0
    self._negative = 0

  def evaluate(self, sample: DLONInput):
    triggers = []
    if sample.has_lead and sample.lead_delta_v < self.SLOW_LEAD_DELTA:
      triggers.append("slow_lead")
    if sample.v_ego < self.LOW_SPEED and not sample.has_lead:
      triggers.append("low_speed")
    if sample.turn_signal and sample.v_ego < self.HIGHWAY_SPEED:
      triggers.append("turn_signal")
    if sample.curve_lat_acc > self.CURVE_LAT_ACC:
      triggers.append("curve")
    if sample.should_stop:
      triggers.append("stop_prediction")
    if sample.traffic_control and sample.should_stop and not sample.has_lead:
      triggers.append("traffic_control")
    if sample.nav_distance is not None and sample.nav_distance < self.NAV_DISTANCE:
      triggers.append("navigation")

    requested = self.mode is DLONMode.EXPERIMENTAL or (self.mode is DLONMode.AUTO and bool(triggers))
    if sample.mpc_fcw:
      requested = True
    if requested:
      self._positive += 1
      self._negative = 0
    else:
      self._negative += 1
      self._positive = 0
    if sample.mpc_fcw or (not self.e2e_suggestion and self._positive >= self.enter_frames):
      self.e2e_suggestion = True
    elif self.e2e_suggestion and self._negative >= self.exit_frames:
      self.e2e_suggestion = False
    force_stop = sample.traffic_control and sample.should_stop and not sample.has_lead
    return DLONResult(self.mode, self.e2e_suggestion, tuple(triggers), force_stop)

import time
from cereal import log
from openpilot.common.realtime import DT_MDL
from openpilot.common.params import Params
from openpilot.selfdrive.controls.lib.dlat import DLAT, LANEFUL_TO_LANELESS_THRESH
from nagaspilot.speed_zones import URBAN_SPEED_MPS
from nagaspilot.controls.eop_lane_change import (Dir, MIN_LANE_WIDTH, blindspot_blocked, evaluate_gap,
                                                 is_road_edge_blinker, validate_lane_width)

LaneChangeState = log.LaneChangeState
LaneChangeDirection = log.LaneChangeDirection

LANE_CHANGE_SPEED_MIN = URBAN_SPEED_MPS
LANE_CHANGE_TIME_MAX = 10.
ALC_CANCEL_DELAY = 1.75  # seconds before a cancelled ALC can restart


DESIRES = {
  LaneChangeDirection.none: {
    LaneChangeState.off: log.Desire.none,
    LaneChangeState.preLaneChange: log.Desire.none,
    LaneChangeState.laneChangeStarting: log.Desire.none,
    LaneChangeState.laneChangeFinishing: log.Desire.none,
  },
  LaneChangeDirection.left: {
    LaneChangeState.off: log.Desire.none,
    LaneChangeState.preLaneChange: log.Desire.none,
    LaneChangeState.laneChangeStarting: log.Desire.laneChangeLeft,
    LaneChangeState.laneChangeFinishing: log.Desire.laneChangeLeft,
  },
  LaneChangeDirection.right: {
    LaneChangeState.off: log.Desire.none,
    LaneChangeState.preLaneChange: log.Desire.none,
    LaneChangeState.laneChangeStarting: log.Desire.laneChangeRight,
    LaneChangeState.laneChangeFinishing: log.Desire.laneChangeRight,
  },
}

TURN_DESIRES = {
  log.Desire.none: log.Desire.none,
  log.Desire.turnLeft: log.Desire.turnLeft,
  log.Desire.turnRight: log.Desire.turnRight,
}


class DesireHelper:
  def __init__(self):
    self.lane_change_state = LaneChangeState.off
    self.lane_change_direction = LaneChangeDirection.none
    self.lane_change_timer = 0.0
    self.lane_change_ll_prob = 1.0
    self.keep_pulse_timer = 0.0
    self.prev_one_blinker = False
    self.desire = log.Desire.none

    # EOP: LCA (Lane Change Assist) parameters
    self.params = Params()
    self.lane_change_delay_timer = 0.0
    self.lane_change_delay_start = 0.0
    self._last_param_update = 0.0
    self._param_update_interval = 1.0  # seconds
    self.lca_enabled = False
    self.auto_lane_change = False
    self.one_lane_change = False
    self.lane_change_delay = 1.0
    self.gap_eval_enabled = False
    self.lane_width_check_enabled = False
    self.min_lane_width = MIN_LANE_WIDTH
    self.lane_change_completed = False
    self.turn_direction = log.Desire.none

    # ALC state guards (road-edge/cancel delay)
    self.last_alc_cancel = 0.0
    self.blinker_below_lane_change_speed = False
    self.prev_blinker = None

  def _load_params(self):
    """Load EOP LCA parameters. Rate-limited to once per second via time.monotonic()."""
    now = time.monotonic()
    if now - self._last_param_update < self._param_update_interval:
      return
    self._last_param_update = now

    self.lca_enabled = self.params.get_bool("EOPLCAControllerEnabled")
    self.auto_lane_change = self.params.get_bool("EOPAutoLaneChange")
    self.one_lane_change = self.params.get_bool("EOPOneLaneChange")
    self.gap_eval_enabled = self.params.get_bool("EOPLCAGapEvalEnabled")
    self.lane_width_check_enabled = self.params.get_bool("EOPLCALaneWidthEnabled")
    try:
      self.lane_change_delay = float(self.params.get("EOPLaneChangeDelay") or 1.0)
      self.min_lane_width = float(self.params.get("EOPMinimumLaneWidth") or MIN_LANE_WIDTH)
    except (ValueError, TypeError):
      self.lane_change_delay = 1.0
      self.min_lane_width = MIN_LANE_WIDTH

  def _evaluate_gap(self, radar_state, model_v2, direction: str, v_ego: float) -> tuple[bool, float]:
    return evaluate_gap(radar_state, model_v2, direction, v_ego)

  def _validate_lane_width(self, model_v2, direction: str) -> bool:
    return validate_lane_width(model_v2, direction, self.min_lane_width)

  def _validate_lane_confidence(self, model_v2) -> bool:
    """DLAT-based initiation safety gate: don't start an automatic/nudged lane
    change while lane-line confidence is too low to trust the geometry.

    Reuses DLAT's own calculate_lane_confidence() formula and its
    LANEFUL_TO_LANELESS_THRESH -- the same threshold DLAT itself uses to
    decide lane lines are unreliable -- rather than inventing a second
    number. Missing/invalid model_v2 resolves to the neutral 0.5 confidence
    built into calculate_lane_confidence(), so this never blocks on absent
    data. Always on, no toggle: pairs with the blindspot check as core
    initiation safety, not an opt-in feature.
    """
    return DLAT.calculate_lane_confidence(model_v2) >= LANEFUL_TO_LANELESS_THRESH

  def _blindspot_blocked(self, carstate, blind_spot_alert, direction) -> bool:
    return blindspot_blocked(carstate, blind_spot_alert, direction)

  def update(self, carstate, lateral_active, lane_change_prob, model_v2=None, radar_state=None, blind_spot_alert=None):
    # Load EOP parameters
    self._load_params()

    current_time = time.monotonic()
    v_ego = carstate.vEgo
    left_blinker = carstate.leftBlinker
    right_blinker = carstate.rightBlinker
    one_blinker = left_blinker != right_blinker
    below_lane_change_speed = v_ego < LANE_CHANGE_SPEED_MIN

    # Track whether the blinker was first activated below ALC speed.
    if one_blinker and self.prev_blinker is None:
      self.blinker_below_lane_change_speed = below_lane_change_speed
    elif not one_blinker:
      self.blinker_below_lane_change_speed = False

    # Direction-change detection for cancelling an in-progress ALC.
    blinker_dir_changed = ((left_blinker and self.prev_blinker == Dir.RIGHT) or
                           (right_blinker and self.prev_blinker == Dir.LEFT))

    # Common guard: road edge on the blinker side means no adjacent lane.
    road_edge_blinker = is_road_edge_blinker(model_v2, right_blinker, left_blinker)

    can_start_lane_change = (one_blinker and not below_lane_change_speed and
                             (current_time - self.last_alc_cancel >= ALC_CANCEL_DELAY) and
                             not road_edge_blinker)

    if not lateral_active or self.lane_change_timer > LANE_CHANGE_TIME_MAX:
      self.lane_change_state = LaneChangeState.off
      self.lane_change_direction = LaneChangeDirection.none
    else:
      # LaneChangeState.off
      if (self.lane_change_state == LaneChangeState.off and can_start_lane_change and
          not self.blinker_below_lane_change_speed):
        self.lane_change_state = LaneChangeState.preLaneChange
        self.lane_change_direction = LaneChangeDirection.left if left_blinker else LaneChangeDirection.right
        self.lane_change_ll_prob = 1.0

      # LaneChangeState.preLaneChange
      elif self.lane_change_state == LaneChangeState.preLaneChange:
        # Set lane change direction
        self.lane_change_direction = LaneChangeDirection.left if left_blinker else LaneChangeDirection.right

        torque_applied = carstate.steeringPressed and \
                         ((carstate.steeringTorque > 0 and self.lane_change_direction == LaneChangeDirection.left) or
                          (carstate.steeringTorque < 0 and self.lane_change_direction == LaneChangeDirection.right))

        blindspot_detected = self._blindspot_blocked(carstate, blind_spot_alert, self.lane_change_direction)

        if not one_blinker or below_lane_change_speed or self.lane_change_completed or not can_start_lane_change:
          self.lane_change_state = LaneChangeState.off
          self.lane_change_direction = LaneChangeDirection.none
          self.lane_change_delay_timer = 0.0
          self.lane_change_delay_start = 0.0
          if not self.lane_change_completed:
            self.last_alc_cancel = current_time
        else:
          # EOP: Human-nudge is the default. Auto lane change (nudgeless) is opt-in.
          should_start = torque_applied and not blindspot_detected

          if self.lca_enabled and self.auto_lane_change and not blindspot_detected:
            # EOP: Nudgeless mode — start after delay timer expires
            if self.lane_change_delay_start == 0.0:
              self.lane_change_delay_start = time.monotonic()
            elapsed = time.monotonic() - self.lane_change_delay_start
            if elapsed >= self.lane_change_delay:
              should_start = True

          # EOP: DLAT lane-confidence gate (always on, no toggle -- see docstring)
          if should_start and not self._validate_lane_confidence(model_v2):
            should_start = False

          # EOP: Gap evaluation (if enabled)
          if should_start and self.gap_eval_enabled:
            direction_str = 'left' if self.lane_change_direction == LaneChangeDirection.left else 'right'
            gap_safe, gap_confidence = self._evaluate_gap(radar_state, model_v2, direction_str, v_ego)
            if not gap_safe:
              should_start = False

          # EOP: Lane width validation (if enabled)
          if should_start and self.lane_width_check_enabled and model_v2 is not None:
            direction_str = 'left' if self.lane_change_direction == LaneChangeDirection.left else 'right'
            if not self._validate_lane_width(model_v2, direction_str):
              should_start = False

          if should_start and not self.blinker_below_lane_change_speed:
            self.lane_change_state = LaneChangeState.laneChangeStarting
            self.lane_change_completed = self.one_lane_change
            self.lane_change_delay_timer = 0.0
            self.lane_change_delay_start = 0.0

      # LaneChangeState.laneChangeStarting
      elif self.lane_change_state == LaneChangeState.laneChangeStarting:
        # EOP: Abort mid-maneuver if BSD detects an object in the target lane.
        # Go to `off` immediately — do NOT flip direction then laneChangeFinishing,
        # which would command a sudden snap-back at highway speed.
        blindspot_detected = self._blindspot_blocked(carstate, blind_spot_alert, self.lane_change_direction)
        if blindspot_detected or (not one_blinker or blinker_dir_changed):
          self.lane_change_state = LaneChangeState.off
          self.lane_change_direction = LaneChangeDirection.none
          self.lane_change_ll_prob = 1.0
          self.lane_change_delay_start = 0.0
          self.lane_change_completed = False
          self.last_alc_cancel = current_time
        else:
          # fade out over .5s
          self.lane_change_ll_prob = max(self.lane_change_ll_prob - 2 * DT_MDL, 0.0)

          # 98% certainty
          if lane_change_prob < 0.02 and self.lane_change_ll_prob < 0.01:
            self.lane_change_state = LaneChangeState.laneChangeFinishing

      # LaneChangeState.laneChangeFinishing
      elif self.lane_change_state == LaneChangeState.laneChangeFinishing:
        # fade in laneline over 1s
        self.lane_change_ll_prob = min(self.lane_change_ll_prob + DT_MDL, 1.0)

        if self.lane_change_ll_prob > 0.99:
          self.lane_change_direction = LaneChangeDirection.none
          if one_blinker and can_start_lane_change:
            self.lane_change_state = LaneChangeState.preLaneChange
          else:
            self.lane_change_state = LaneChangeState.off
            self.last_alc_cancel = current_time

    if self.lane_change_state in (LaneChangeState.off, LaneChangeState.preLaneChange):
      self.lane_change_timer = 0.0
    else:
      self.lane_change_timer += DT_MDL

    self.lane_change_completed &= one_blinker
    self.prev_one_blinker = one_blinker
    self.prev_blinker = None if not one_blinker else (Dir.LEFT if left_blinker else Dir.RIGHT)

    # EOP: Turn desires below lane change speed (FrogPilot proven pattern).
    # When blinker is on below 11 m/s and not stopped, send turnLeft/turnRight
    # to the model so it anticipates the low-speed turn / intersection maneuver.
    if one_blinker and below_lane_change_speed and not carstate.standstill:
      self.turn_direction = log.Desire.turnLeft if left_blinker else log.Desire.turnRight
      self.desire = TURN_DESIRES[self.turn_direction]
    else:
      self.turn_direction = log.Desire.none
      self.desire = DESIRES[self.lane_change_direction][self.lane_change_state]

    if self.lane_change_state in (LaneChangeState.off, LaneChangeState.laneChangeStarting):
      self.keep_pulse_timer = 0.0
    elif self.lane_change_state == LaneChangeState.preLaneChange:
      self.keep_pulse_timer += DT_MDL
      if self.keep_pulse_timer > 1.0:
        self.keep_pulse_timer = 0.0
      elif self.desire in (log.Desire.keepLeft, log.Desire.keepRight):
        self.desire = log.Desire.none

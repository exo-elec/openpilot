from exopilot.selfdrive.controls.lib.radar2d import blindspot_blocked
from cereal import log
from openpilot.common.constants import CV
from openpilot.common.realtime import DT_MDL
from exopilot.selfdrive.controls.lib.turn_desire import TurnDesire
from exopilot.selfdrive.controls.lib.lane_gap import MIN_LANE_WIDTH, evaluate_gap, validate_lane_width

LaneChangeState = log.LaneChangeState
LaneChangeDirection = log.LaneChangeDirection

LANE_CHANGE_SPEED_MIN = 20 * CV.MPH_TO_MS
LANE_CHANGE_TIME_MAX = 10.

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


class DesireHelper:
  def __init__(self, lca_speed_mph=20, lca_auto_sec=0.0, lca_gap_eval=False, lca_lane_width=False, turn_desire_mph=0):
    self.lane_change_state = LaneChangeState.off
    self.lane_change_direction = LaneChangeDirection.none
    self.lane_change_timer = 0.0
    self.lane_change_ll_prob = 1.0
    self.keep_pulse_timer = 0.0
    self.prev_one_blinker = False
    self.desire = log.Desire.none
    self.lca_speed = float(lca_speed_mph) * CV.MPH_TO_MS
    self.lca_auto_sec = max(0.0, float(lca_auto_sec))
    self.lca_auto_timer = 0.0
    self.lca_gap_eval = bool(lca_gap_eval)
    self.lca_lane_width = bool(lca_lane_width)
    self.turn_desire = TurnDesire(float(turn_desire_mph) * CV.MPH_TO_MS) if turn_desire_mph else None

  def _lane_blocked(self, model_v2, direction, v_ego):
    # Opt-in modelV2 guards (adjacent-lane TTC gap, target-lane width). Unknown data never blocks.
    side = 'left' if direction == LaneChangeDirection.left else 'right'
    if self.lca_gap_eval and not evaluate_gap(model_v2, side, v_ego)[0]:
      return True
    return self.lca_lane_width and not validate_lane_width(model_v2, side, MIN_LANE_WIDTH)

  def update(self, carstate, lateral_active, lane_change_prob, left_edge_detected=False, right_edge_detected=False,
             low_lane_confidence=False, model_v2=None):
    v_ego = carstate.vEgo
    one_blinker = carstate.leftBlinker != carstate.rightBlinker
    below_lane_change_speed = self.lca_speed <= 0.0 or v_ego < self.lca_speed

    if not lateral_active or self.lane_change_timer > LANE_CHANGE_TIME_MAX:
      self.lane_change_state = LaneChangeState.off
      self.lane_change_direction = LaneChangeDirection.none
    else:
      # LaneChangeState.off
      if self.lane_change_state == LaneChangeState.off and one_blinker and not self.prev_one_blinker and not below_lane_change_speed:
        self.lane_change_state = LaneChangeState.preLaneChange
        self.lane_change_direction = LaneChangeDirection.left if carstate.leftBlinker else LaneChangeDirection.right
        self.lane_change_ll_prob = 1.0
        self.lca_auto_timer = 0.0

      # LaneChangeState.preLaneChange
      elif self.lane_change_state == LaneChangeState.preLaneChange:
        # Set lane change direction
        self.lane_change_direction = LaneChangeDirection.left if \
          carstate.leftBlinker else LaneChangeDirection.right

        torque_applied = carstate.steeringPressed and \
                         ((carstate.steeringTorque > 0 and self.lane_change_direction == LaneChangeDirection.left) or
                          (carstate.steeringTorque < 0 and self.lane_change_direction == LaneChangeDirection.right))

        blindspot_detected = (((blindspot_blocked(carstate.leftBlindspot, carstate.rightBlindspot, "left") or left_edge_detected) and self.lane_change_direction == LaneChangeDirection.left) or
                              ((blindspot_blocked(carstate.leftBlindspot, carstate.rightBlindspot, "right") or right_edge_detected) and self.lane_change_direction == LaneChangeDirection.right))

        # DLAT lane-confidence gate: don't initiate (or accumulate toward a
        # nudgeless auto-initiate) while lane-line confidence is too low to
        # trust the geometry. Always on, no toggle -- see modeld.py caller.
        blindspot_detected = blindspot_detected or self._lane_blocked(model_v2, self.lane_change_direction, v_ego)

        if blindspot_detected or low_lane_confidence:
          self.lca_auto_timer = 0.0
        else:
          self.lca_auto_timer += DT_MDL
          if self.lca_auto_sec > 0.0 and self.lca_auto_timer >= self.lca_auto_sec:
            torque_applied = True

        if not one_blinker or below_lane_change_speed:
          self.lane_change_state = LaneChangeState.off
          self.lane_change_direction = LaneChangeDirection.none
        elif torque_applied and not blindspot_detected and not low_lane_confidence:
          self.lane_change_state = LaneChangeState.laneChangeStarting

      # LaneChangeState.laneChangeStarting
      elif self.lane_change_state == LaneChangeState.laneChangeStarting:
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
          if one_blinker:
            self.lane_change_state = LaneChangeState.preLaneChange
          else:
            self.lane_change_state = LaneChangeState.off

    if self.lane_change_state in (LaneChangeState.off, LaneChangeState.preLaneChange):
      self.lane_change_timer = 0.0
    else:
      self.lane_change_timer += DT_MDL

    self.prev_one_blinker = one_blinker
    if self.lane_change_state == LaneChangeState.off:
      self.lca_auto_timer = 0.0

    self.desire = DESIRES[self.lane_change_direction][self.lane_change_state]

    # Send keep pulse once per second during LaneChangeStart.preLaneChange
    if self.lane_change_state in (LaneChangeState.off, LaneChangeState.laneChangeStarting):
      self.keep_pulse_timer = 0.0
    elif self.lane_change_state == LaneChangeState.preLaneChange:
      self.keep_pulse_timer += DT_MDL
      if self.keep_pulse_timer > 1.0:
        self.keep_pulse_timer = 0.0
      elif self.desire in (log.Desire.keepLeft, log.Desire.keepRight):
        self.desire = log.Desire.none

    # Opt-in turn desire (TurnDesireSpeed): a turnLeft/turnRight pulse for the model at low speed with a signal on
    if self.turn_desire is not None and self.desire == log.Desire.none:
      turn = self.turn_desire.update(v_ego, carstate.leftBlinker, carstate.rightBlinker, lateral_active,
                                  self.lane_change_state != LaneChangeState.off)
      if turn is not None:
        self.desire = log.Desire.turnLeft if turn == 'left' else log.Desire.turnRight

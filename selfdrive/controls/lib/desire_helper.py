import time
import numpy as np
from cereal import log
from openpilot.common.constants import CV
from openpilot.common.realtime import DT_MDL
from openpilot.common.params import Params
from nagaspilot.speed_zones import URBAN_SPEED_MPS

LaneChangeState = log.LaneChangeState
LaneChangeDirection = log.LaneChangeDirection

LANE_CHANGE_SPEED_MIN = URBAN_SPEED_MPS
LANE_CHANGE_TIME_MAX = 10.

# EOP: LCA Constants
MIN_TIME_GAP = 2.0  # seconds - minimum TTC for safe gap
MIN_LANE_WIDTH = 2.5  # meters - minimum lane width for safe change
FAST_APPROACH_THRESHOLD = -10.0  # m/s (36 km/h faster)


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
    """
    EOP: Evaluate if gap is safe for lane change using TTC-based calculation.
    Pure-camera style: uses both radar (if available) and modelV2 vision leads.

    Args:
      radar_state: Radar data with lead information (may be None on camera-only platforms)
      model_v2: ModelV2 message with vision leads (leadsV3)
      direction: 'left' or 'right'
      v_ego: Current ego speed (m/s)

    Returns:
      (is_safe, confidence) tuple
    """
    adjacent_leads = []

    # 1. Radar leads (if available)
    # yRel sign convention: positive = left (same as modelV2 lead.y[0])
    if radar_state:
      for lead in [radar_state.leadOne, radar_state.leadTwo]:
        if not lead.status:
          continue
        if direction == 'left' and lead.yRel > 1.5:
          adjacent_leads.append((lead.dRel, lead.vRel))
        elif direction == 'right' and lead.yRel < -1.5:
          adjacent_leads.append((lead.dRel, lead.vRel))

    # 2. Vision leads from modelV2 (pure-camera fallback / augmentation)
    if model_v2 and len(model_v2.leadsV3) > 0:
      for lead in model_v2.leadsV3:
        if lead.prob < 0.5:
          continue
        # lead.y[0] is lateral position in ego frame (positive = left)
        # Adjacent lane is roughly |y| > 1.5m from ego center
        if direction == 'left' and lead.y[0] > 1.5:
          d_rel = lead.x[0] - 1.52  # approximate, same as RADAR_TO_CAMERA offset
          v_rel = lead.v[0] - v_ego
          adjacent_leads.append((d_rel, v_rel))
        elif direction == 'right' and lead.y[0] < -1.5:
          d_rel = lead.x[0] - 1.52
          v_rel = lead.v[0] - v_ego
          adjacent_leads.append((d_rel, v_rel))

    # No adjacent leads detected
    if not adjacent_leads:
      return True, 1.0

    # Evaluate each adjacent lead
    for d_rel, v_rel in adjacent_leads:
      # Check for fast-approaching vehicle
      if v_rel < FAST_APPROACH_THRESHOLD:
        return False, 0.0

      # Calculate time to collision (TTC)
      if v_rel < 0:  # Lead is approaching
        time_to_collision = d_rel / abs(v_rel) if abs(v_rel) > 0.1 else float('inf')
        if time_to_collision < MIN_TIME_GAP:
          return False, 0.0
      else:
        # Lead moving away or same speed - check minimum distance
        if d_rel < 10.0:  # 10m minimum
          return False, 0.3

    return True, 1.0

  def _validate_lane_width(self, model_v2, direction: str) -> bool:
    """Validate target lane is physically wide enough and is a real lane (not shoulder).

    Interpolates at multiple forward distances and takes median — robust to close-range
    artifacts where lane lines may be parallel or crossing. np.minimum() on outer_dist
    and edge_dist means the road edge can REDUCE effective lane width, correctly
    rejecting shoulders (edge closer than far lane line → apparent width collapses).
    """
    if not model_v2:
      return True
    try:
      SAMPLE_X = [5.0, 10.0, 20.0, 30.0, 40.0]

      if direction == 'left':
        inner_ll = model_v2.laneLines[1] if len(model_v2.laneLines) > 1 else None
        outer_ll = model_v2.laneLines[0] if len(model_v2.laneLines) > 0 else None
        edge_ll  = model_v2.roadEdges[0]  if len(model_v2.roadEdges) > 0 else None
      else:
        inner_ll = model_v2.laneLines[2] if len(model_v2.laneLines) > 2 else None
        outer_ll = model_v2.laneLines[3] if len(model_v2.laneLines) > 3 else None
        edge_ll  = model_v2.roadEdges[1]  if len(model_v2.roadEdges) > 1 else None

      if inner_ll is None or not len(inner_ll.x):
        return True

      ix = list(inner_ll.x)
      iy = list(inner_ll.y)
      x_max = ix[-1]
      valid_x = [x for x in SAMPLE_X if x <= x_max]
      if not valid_x:
        return True

      inner_y    = np.array([np.interp(x, ix, iy) for x in valid_x])
      inner_dist = np.abs(inner_y)

      if outer_ll is not None and len(outer_ll.x):
        outer_y    = np.array([np.interp(x, list(outer_ll.x), list(outer_ll.y)) for x in valid_x])
        outer_dist = np.abs(outer_y)
      else:
        outer_dist = np.full(len(valid_x), np.inf)

      if edge_ll is not None and len(edge_ll.x):
        edge_y    = np.array([np.interp(x, list(edge_ll.x), list(edge_ll.y)) for x in valid_x])
        edge_dist = np.abs(edge_y)
      else:
        edge_dist = np.full(len(valid_x), np.inf)

      # Road edge caps the effective outer boundary — shoulder detection via np.minimum
      effective_dist = np.minimum(outer_dist, edge_dist)
      widths = effective_dist - inner_dist
      lane_width = float(np.median(widths))
      return lane_width >= self.min_lane_width

    except (IndexError, AttributeError, TypeError, ValueError):
      return True

  def _blindspot_blocked(self, carstate, blind_spot_alert, direction) -> bool:
    """Check if a lane change is blocked for the given direction.

    Priority order:
      1. Vehicle-native BSM (CAN hardware — always authoritative)
      2. Immediate BSD zone (±15m, radar4d/2d/camera → alert + chime)
      3. Wide LCA gate (up to 100m, radar3d TTC-based — no alert/chime)
    """
    if direction == LaneChangeDirection.left and carstate.leftBlindspot:
      return True
    if direction == LaneChangeDirection.right and carstate.rightBlindspot:
      return True
    if blind_spot_alert is None:
      return False
    if direction == LaneChangeDirection.left:
      return (blind_spot_alert.leftDetected
              or blind_spot_alert.leftAlertLevel >= 1
              or blind_spot_alert.lcaBlockedLeft)
    if direction == LaneChangeDirection.right:
      return (blind_spot_alert.rightDetected
              or blind_spot_alert.rightAlertLevel >= 1
              or blind_spot_alert.lcaBlockedRight)
    return False

  def update(self, carstate, lateral_active, lane_change_prob, model_v2=None, radar_state=None, blind_spot_alert=None):
    # Load EOP parameters
    self._load_params()

    v_ego = carstate.vEgo
    one_blinker = carstate.leftBlinker != carstate.rightBlinker
    below_lane_change_speed = v_ego < LANE_CHANGE_SPEED_MIN

    if not lateral_active or self.lane_change_timer > LANE_CHANGE_TIME_MAX:
      self.lane_change_state = LaneChangeState.off
      self.lane_change_direction = LaneChangeDirection.none
    else:
      # LaneChangeState.off
      if self.lane_change_state == LaneChangeState.off and one_blinker and not self.prev_one_blinker and not below_lane_change_speed:
        self.lane_change_state = LaneChangeState.preLaneChange
        self.lane_change_ll_prob = 1.0

      # LaneChangeState.preLaneChange
      elif self.lane_change_state == LaneChangeState.preLaneChange:
        # Set lane change direction
        self.lane_change_direction = LaneChangeDirection.left if \
          carstate.leftBlinker else LaneChangeDirection.right

        torque_applied = carstate.steeringPressed and \
                         ((carstate.steeringTorque > 0 and self.lane_change_direction == LaneChangeDirection.left) or
                          (carstate.steeringTorque < 0 and self.lane_change_direction == LaneChangeDirection.right))

        blindspot_detected = self._blindspot_blocked(carstate, blind_spot_alert, self.lane_change_direction)

        if not one_blinker or below_lane_change_speed or self.lane_change_completed:
          self.lane_change_state = LaneChangeState.off
          self.lane_change_direction = LaneChangeDirection.none
          self.lane_change_delay_timer = 0.0
          self.lane_change_delay_start = 0.0
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

          if should_start:
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
        if blindspot_detected:
          self.lane_change_state = LaneChangeState.off
          self.lane_change_direction = LaneChangeDirection.none
          self.lane_change_ll_prob = 1.0
          self.lane_change_delay_start = 0.0
          self.lane_change_completed = False
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
          if one_blinker:
            self.lane_change_state = LaneChangeState.preLaneChange
          else:
            self.lane_change_state = LaneChangeState.off

    if self.lane_change_state in (LaneChangeState.off, LaneChangeState.preLaneChange):
      self.lane_change_timer = 0.0
    else:
      self.lane_change_timer += DT_MDL

    self.lane_change_completed &= one_blinker
    self.prev_one_blinker = one_blinker

    # EOP: Turn desires below lane change speed (FrogPilot proven pattern).
    # When blinker is on below 11 m/s and not stopped, send turnLeft/turnRight
    # to the model so it anticipates the low-speed turn / intersection maneuver.
    if one_blinker and below_lane_change_speed and not carstate.standstill:
      self.turn_direction = log.Desire.turnLeft if carstate.leftBlinker else log.Desire.turnRight
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

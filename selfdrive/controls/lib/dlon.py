"""
dlon.py - Dynamic Longitudinal Profile (DLON)

Manages longitudinal acceleration strategy, switching between comfortable
highway cruising ("Chill") and intelligent urban driving ("Experimental"/E2E).

Based on Sunnypilot DEC architecture with simplified trigger set.
"""

import time
from enum import Enum
from openpilot.common.realtime import DT_MDL
from openpilot.common.constants import CV
from openpilot.common.params import Params
from openpilot.selfdrive.controls.lib.eop_utils import SmoothEMA
from nagaspilot.speed_zones import HIGHWAY_SPEED_MPS, URBAN_SPEED_MPS


class DLONMode(Enum):
  """DLON operating modes."""
  CHILL = "Chill"
  EXPERIMENTAL = "Experimental"
  AUTO = "Auto"


class DriveMode(Enum):
  """Internal drive mode state."""
  ACC = "acc"
  E2E = "blended"


class ModeTransitionManager:
  """Manages smooth transitions between driving modes with hysteresis."""

  def __init__(self):
    self.current_mode = DriveMode.ACC
    self.mode_confidence = {DriveMode.ACC: 1.0, DriveMode.E2E: 0.0}
    self.transition_timeout = 0
    self.mode_duration = 0
    self.emergency_override = False
    self.min_mode_duration = 10  # frames (~0.5s at 20Hz)

  def request_mode(self, mode: DriveMode, confidence: float = 1.0, emergency: bool = False):
    """Request mode transition with confidence and emergency override."""
    if emergency:
      self.emergency_override = True
      self.current_mode = mode
      self.transition_timeout = 15
      self.mode_duration = 0
      return

    # Update confidence for requested mode
    self.mode_confidence[mode] = min(1.0, self.mode_confidence[mode] + 0.1 * confidence)

    # Decay confidence for other modes
    for m in self.mode_confidence:
      if m != mode:
        self.mode_confidence[m] = max(0.0, self.mode_confidence[m] - 0.05)

    # Check minimum duration (prevent rapid switching)
    if self.mode_duration < self.min_mode_duration and not self.emergency_override:
      return

    # Hysteresis: higher threshold for mode changes, lower to maintain
    threshold = 0.6 if mode != self.current_mode else 0.3

    if self.mode_confidence[mode] > threshold:
      if mode != self.current_mode and self.transition_timeout == 0:
        self.transition_timeout = 15  # frames cooldown
        self.current_mode = mode
        self.mode_duration = 0

  def update(self):
    """Call every frame to update timing."""
    if self.transition_timeout > 0:
      self.transition_timeout -= 1
    self.mode_duration += 1

    # Clear emergency after duration
    if self.emergency_override and self.mode_duration > 20:
      self.emergency_override = False

    # Gradual confidence decay
    for mode in self.mode_confidence:
      self.mode_confidence[mode] *= 0.98

  def get_mode(self) -> DriveMode:
    return self.current_mode


class DLON:
  """
  Dynamic Longitudinal Profile Controller

  Dynamically switches between Chill (ACC) and Experimental (E2E) modes
  based on environmental triggers.
  """

  # Trigger thresholds
  LEAD_SLOW_VELOCITY_THRESHOLD = -5.0  # m/s (~18 km/h slower)
  LOW_SPEED_THRESHOLD = URBAN_SPEED_MPS
  HIGHWAY_SPEED_THRESHOLD = HIGHWAY_SPEED_MPS
  TRAFFIC_LIGHT_THRESHOLD = 0.5  # Probability threshold
  STOP_SIGN_THRESHOLD = 0.5  # Probability threshold
  CURVE_LAT_ACC_THRESHOLD = 1.2  # m/s² - curve threshold (CEM merge)
  STOP_PREDICTION_DISTANCE = 50.0  # meters
  EXIT_DEBOUNCE = 2.0  # seconds
  # E2E handles the deceleration into a lower posted limit more smoothly than
  # stock ACC; only trigger for a meaningfully lower limit, not noise right
  # at the boundary (map/nav speed-limit values are step functions, not
  # continuous, so a small margin avoids chatter at the zone edge).
  SPEED_LIMIT_TRIGGER_MARGIN_MS = 2.0  # m/s (~4.5 mph)

  def __init__(self):
    self.params = Params()
    self.mode = DLONMode.AUTO
    self.last_param_update = 0

    # Mode manager
    self.mode_manager = ModeTransitionManager()

    # Filters for trigger stability (EMA replaces over-engineered KalmanFilter)
    self.lead_filter = SmoothEMA(alpha=0.3)
    self.traffic_filter = SmoothEMA(alpha=0.3)
    self.fcw_filter = SmoothEMA(alpha=0.3)
    self.curve_filter = SmoothEMA(alpha=0.3)
    self.stop_filter = SmoothEMA(alpha=0.3)
    self.nav_filter = SmoothEMA(alpha=0.3)
    self.speed_limit_filter = SmoothEMA(alpha=0.3)

    # State
    self.frame = 0
    self._has_lead_filtered = False
    self._has_traffic_control = False
    self._has_mpc_fcw = False
    self._in_sharp_curve = False
    self._should_stop = False
    self._nav_trigger = False
    self._speed_limit_trigger = False

    # Per-trigger enable toggles (CEM merge)
    self._trigger_enabled = {
      'curves': True,
      'lane_confidence': True,
      'slow_lead': True,
      'low_speed': True,
      'stop_prediction': True,
      'navigation': True,
      'signal': True,
      'speed_limit': True,
    }

    # Exit debounce (CEM merge)
    self._active = False
    self._enter_ts = 0.0
    self._exit_ts = 0.0

    # Force stops (merged from FrogPilot force_stop logic)
    self.force_stops_enabled = False
    self.force_stop_timer = 0.0
    self.override_force_stop = False
    self.override_force_stop_timer = 0.0

  def update_params(self):
    """Update parameters periodically (every 1 second).

    Mode is always AUTO (automatic Chill/Experimental switching) -- a
    default, standard behavior of this branch, not a user-selectable
    choice. There is no param or panel control that forces a fixed CHILL
    or EXPERIMENTAL mode; users cannot force pure E2E driving directly,
    matching dev/NGP10's design.
    """
    current_time = time.monotonic()
    if current_time - self.last_param_update >= 1.0:
      self.mode = DLONMode.AUTO
      # Per-trigger toggles (CEM merge)
      self._trigger_enabled['curves'] = self.params.get_bool("EOPDLONCurvesEnabled")
      self._trigger_enabled['lane_confidence'] = self.params.get_bool("EOPDLONLaneConfidenceEnabled")
      self._trigger_enabled['slow_lead'] = self.params.get_bool("EOPDLONSlowLeadEnabled")
      self._trigger_enabled['low_speed'] = self.params.get_bool("EOPDLONLowSpeedEnabled")
      self._trigger_enabled['stop_prediction'] = self.params.get_bool("EOPDLONStopPredictionEnabled")
      self._trigger_enabled['navigation'] = self.params.get_bool("EOPDLONNavigationEnabled")
      self._trigger_enabled['signal'] = self.params.get_bool("EOPDLONSignalEnabled")
      self._trigger_enabled['speed_limit'] = self.params.get_bool("EOPDLONSpeedLimitEnabled")
      self.force_stops_enabled = self.params.get_bool("EOPDLONForceStopsEnabled")
      self.last_param_update = current_time

  def detect_traffic_control(self, model_v2, radar_state, v_ego) -> bool:
    """Detect traffic lights and stop signs from modelV2.

    NOTE: modelV2.meta does not have trafficLightProbability / stopSignProbability.
    We use a heuristic: model predicts stop + no lead + low speed/standstill.
    This triggers force stops and E2E mode at intersections without requiring
    explicit traffic control metadata from the model.
    """
    if not model_v2:
      return False

    has_lead = radar_state.leadOne.status if radar_state else False
    should_stop = self.detect_stop_prediction(model_v2)

    # Heuristic: traffic control likely when model wants to stop,
    # no lead is present, and we're at low speed (intersection/standstill)
    return should_stop and not has_lead and v_ego < self.LOW_SPEED_THRESHOLD

  def detect_slower_lead(self, radar_state, v_ego) -> bool:
    """Detect significantly slower lead vehicle."""
    if not radar_state or not radar_state.leadOne.status:
      return False

    delta_v = radar_state.leadOne.vLead - v_ego
    return delta_v < self.LEAD_SLOW_VELOCITY_THRESHOLD

  def detect_low_speed_scenario(self, v_ego, has_lead) -> bool:
    """Detect low-speed navigation scenario (empty intersection)."""
    return v_ego < self.LOW_SPEED_THRESHOLD and not has_lead

  def detect_turn_intent(self, car_state, v_ego) -> bool:
    """Detect turn signal at non-highway speeds."""
    if v_ego >= self.HIGHWAY_SPEED_THRESHOLD:
      return False

    return car_state.leftBlinker or car_state.rightBlinker

  def detect_sharp_curve(self, model_v2, v_ego) -> bool:
    """Detect sharp curve ahead using lateral acceleration prediction."""
    if not model_v2 or not hasattr(model_v2, 'orientationRate'):
      return False

    if len(model_v2.orientationRate.z) < 5:
      return False

    # Calculate predicted lateral accel
    max_lat_acc = 0.0
    for i in range(min(10, len(model_v2.orientationRate.z))):
      rate = abs(model_v2.orientationRate.z[i])  # rad/s
      # Assume constant velocity for prediction
      lat_acc = v_ego * rate
      max_lat_acc = max(max_lat_acc, lat_acc)

    return max_lat_acc > self.CURVE_LAT_ACC_THRESHOLD

  def detect_lane_confidence_trigger(self, sm) -> bool:
    """Detect low DLAT lane confidence (controlsd, cross-process via controlsState).

    DLAT already resolves a debounced Laneful/Laneless decision from lane-line
    confidence (selfdrive/controls/lib/dlat.py); we read that decision instead
    of re-deriving our own threshold/hysteresis on the raw confidence value, so
    there is one source of truth and no duplicate debounce state. When DLAT has
    committed to Laneless -- lane lines are unreliable -- E2E's path-only
    prediction is a better fit than lane-line-anchored ACC, mirroring how a
    human driver relies less on lane markings and more on the road/path shape
    itself when markings are faded or absent.

    A stale or not-yet-published controlsState resolves to no-trigger (False),
    matching DLAT's own default-to-laneful-not-laneless convention when its
    input is missing.
    """
    if 'controlsState' not in sm.valid or not sm.valid['controlsState']:
      return False
    return bool(sm['controlsState'].dlatUseLaneless)

  def detect_stop_prediction(self, model_v2) -> bool:
    """Detect stop prediction from modelV2 action (CEM merge)."""
    if not model_v2 or not hasattr(model_v2, 'action'):
      return False
    return getattr(model_v2.action, 'shouldStop', False)

  def detect_nav_trigger(self, sm) -> bool:
    """Detect navigation-based trigger (upcoming maneuver)."""
    if 'navInstruction' not in sm.valid or not sm.valid['navInstruction']:
      return False
    nav = sm['navInstruction']
    # Trigger if approaching a maneuver that benefits from E2E
    if hasattr(nav, 'maneuverDistance'):
      return nav.maneuverDistance < self.STOP_PREDICTION_DISTANCE
    return False

  def detect_speed_limit_trigger(self, sm, v_ego) -> bool:
    """Detect an active mapped/nav speed limit meaningfully lower than
    current speed — E2E's smoother deceleration profile handles the
    transition better than stock ACC. Same map-then-nav source preference
    and unit handling as nslc.py's NSLC.update()."""
    limit_ms = None
    if 'mapData' in sm.valid and sm.valid['mapData']:
      map_limit = sm['mapData'].speedLimit  # km/h
      if map_limit > 0:
        limit_ms = map_limit * CV.KPH_TO_MS
    if limit_ms is None and 'navInstruction' in sm.valid and sm.valid['navInstruction']:
      nav_limit = sm['navInstruction'].speedLimit  # m/s
      if nav_limit > 0:
        limit_ms = nav_limit

    if limit_ms is None:
      return False
    return limit_ms < v_ego - self.SPEED_LIMIT_TRIGGER_MARGIN_MS

  def update(self, sm, mpc_crash_cnt: int = 0) -> dict:
    """
    Main update method.

    Args:
      sm: SubMaster with carState, modelV2, radarState
      mpc_crash_cnt: MPC FCW crash counter

    Returns:
      Dict with mode, e2e_enabled, triggers
    """
    self.update_params()

    CS = sm['carState']
    model_v2 = sm['modelV2']
    radar_state = sm['radarState'] if 'radarState' in sm.valid else None

    v_ego = CS.vEgo

    # Update filters
    has_lead = radar_state.leadOne.status if radar_state else False
    self.lead_filter.update(float(has_lead))
    self._has_lead_filtered = self.lead_filter.get_value() > 0.5

    has_traffic = self.detect_traffic_control(model_v2, radar_state, v_ego)
    self.traffic_filter.update(float(has_traffic))
    self._has_traffic_control = self.traffic_filter.get_value() > 0.5

    self.fcw_filter.update(float(mpc_crash_cnt > 0))
    self._has_mpc_fcw = self.fcw_filter.get_value() > 0.5

    in_curve = self.detect_sharp_curve(model_v2, v_ego)
    self.curve_filter.update(float(in_curve))
    self._in_sharp_curve = self.curve_filter.get_value() > 0.5

    should_stop = self.detect_stop_prediction(model_v2)
    self.stop_filter.update(float(should_stop))
    self._should_stop = self.stop_filter.get_value() > 0.5

    nav_trigger = self.detect_nav_trigger(sm)
    self.nav_filter.update(float(nav_trigger))
    self._nav_trigger = self.nav_filter.get_value() > 0.5

    speed_limit_trigger = self.detect_speed_limit_trigger(sm, v_ego)
    self.speed_limit_filter.update(float(speed_limit_trigger))
    self._speed_limit_trigger = self.speed_limit_filter.get_value() > 0.5

    # Force stops (merged from FrogPilot)
    force_stop = False
    if self.force_stops_enabled:
      CS = sm['carState']
      # Driver override with gas press
      if CS.gasPressed:
        self.override_force_stop = True
        self.override_force_stop_timer = 10.0
      elif self.override_force_stop_timer > 0:
        self.override_force_stop_timer -= DT_MDL
      else:
        self.override_force_stop = False

      # Recommend force stop when traffic control detected + model predicts stop + no lead
      force_stop_recommended = (
        self._has_traffic_control and
        self._should_stop and
        not has_lead
      )

      if force_stop_recommended:
        self.force_stop_timer += DT_MDL
      else:
        self.force_stop_timer = 0.0

      # Require 1.0s of continuous recommendation before activating
      force_stop_active = self.force_stop_timer >= 1.0
      force_stop = force_stop_active and not self.override_force_stop

    # Determine if E2E should be active
    if self.mode == DLONMode.CHILL:
      use_e2e = False
    elif self.mode == DLONMode.EXPERIMENTAL:
      use_e2e = True
    else:  # AUTO mode
      use_e2e = self._evaluate_auto_mode(CS, model_v2, radar_state, sm)

    # Apply exit debounce (CEM merge)
    now = time.monotonic()
    if use_e2e and not self._active:
      if now - self._enter_ts > 0.5:
        self._active = True
      self._exit_ts = now
    elif not use_e2e and self._active:
      if now - self._exit_ts > self.EXIT_DEBOUNCE:
        self._active = False
      self._enter_ts = now
    else:
      self._enter_ts = self._exit_ts = now

    # Update mode manager
    requested_mode = DriveMode.E2E if self._active else DriveMode.ACC
    confidence = self._calculate_confidence()
    emergency = self._has_mpc_fcw

    self.mode_manager.request_mode(requested_mode, confidence, emergency)
    self.mode_manager.update()

    final_mode = self.mode_manager.get_mode()
    is_e2e = final_mode == DriveMode.E2E

    self.frame += 1

    return {
      'mode': self.mode.value,
      'e2e_enabled': is_e2e,
      'internal_mode': final_mode.value,
      'force_stop': force_stop,
      'triggers': {
        'traffic_control': self._has_traffic_control,
        'slower_lead': self.detect_slower_lead(radar_state, v_ego),
        'low_speed': self.detect_low_speed_scenario(v_ego, has_lead),
        'turn_intent': self.detect_turn_intent(CS, v_ego),
        'sharp_curve': self._in_sharp_curve,
        'mpc_fcw': self._has_mpc_fcw,
        'speed_limit': self._speed_limit_trigger,
        'low_lane_confidence': self.detect_lane_confidence_trigger(sm)
      },
      'confidences': {
        'lead': round(self.lead_filter.get_confidence(), 3),
        'traffic': round(self.traffic_filter.get_confidence(), 3),
        'fcw': round(self.fcw_filter.get_confidence(), 3),
        'curve': round(self.curve_filter.get_confidence(), 3),
        'speed_limit': round(self.speed_limit_filter.get_confidence(), 3)
      }
    }

  def _evaluate_auto_mode(self, CS, model_v2, radar_state, sm) -> bool:
    """Evaluate if E2E should be active in AUTO mode."""
    v_ego = CS.vEgo

    # Emergency: highest priority
    if self._has_mpc_fcw:
      return True

    # Stop prediction (CEM merge)
    if self._trigger_enabled['stop_prediction'] and self._should_stop:
      return True

    # Navigation trigger (CEM merge)
    if self._trigger_enabled['navigation'] and self._nav_trigger:
      return True

    # High priority triggers
    if self._has_traffic_control:
      return True

    if self._trigger_enabled['slow_lead'] and self.detect_slower_lead(radar_state, v_ego):
      return True

    # Medium priority triggers
    if self._trigger_enabled['low_speed'] and self.detect_low_speed_scenario(v_ego, radar_state.leadOne.status if radar_state else False):
      return True

    if self._trigger_enabled['speed_limit'] and self._speed_limit_trigger:
      return True

    if self._trigger_enabled['signal'] and self.detect_turn_intent(CS, v_ego):
      return True

    # Lower priority: perception-difficulty signals (curves, unreliable lane lines)
    if self._trigger_enabled['curves'] and self._in_sharp_curve:
      return True

    if self._trigger_enabled['lane_confidence'] and self.detect_lane_confidence_trigger(sm):
      return True

    return False

  def _calculate_confidence(self) -> float:
    """Calculate overall confidence for mode decision."""
    confidences = []

    if self._has_traffic_control:
      confidences.append(self.traffic_filter.get_confidence())
    if self._has_mpc_fcw:
      confidences.append(self.fcw_filter.get_confidence())
    if self._in_sharp_curve:
      confidences.append(self.curve_filter.get_confidence())
    if self._speed_limit_trigger:
      confidences.append(self.speed_limit_filter.get_confidence())

    # Lead confidence is always relevant if present
    if self._has_lead_filtered:
      confidences.append(self.lead_filter.get_confidence())

    return max(confidences) if confidences else 0.5

  # EOP-CLEANUP: Removed get_debug_dict() — no caller consumed it.

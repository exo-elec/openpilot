"""
AEB - Autonomous Emergency Braking Controller

Implements RSS-based forward collision warning and emergency braking.
Operates as an advisory safety layer that can request emergency deceleration.
Actual braking authority is gated by controlsd based on safety validation.

Architecture:
  1. CollisionPredictor: RSS-based TTC calculation from tracked objects
  2. BrakingController: Progressive braking state machine
  3. AEB: Main controller integrating with openpilot's control loop

Inputs:
  - radarState: Lead vehicle data measured by the built-in 77 GHz radar
  - carState: Ego vehicle state (velocity, acceleration, steering torque)

Outputs:
  - aebState: Published via driverAssistance message
  - Emergency deceleration request: Consumed by controlsd

Safety Design:
  - NEVER overrides driver steering or throttle directly
  - Driver override detection (steering >3Nm or throttle during braking)
  - Progressive braking: precharge → partial → full
  - Corner BLE radar and auxiliary cameras are advisory-only and cannot brake

Reference: Mobileye RSS model ( Responsibility-Sensitive Safety )
"""
from __future__ import annotations

import math
import time
from dataclasses import dataclass
from enum import Enum, auto
from collections import deque


from openpilot.common.params import Params
from openpilot.common.realtime import DT_MDL
from openpilot.common.swaglog import cloudlog


class AEBLevel(Enum):
  """AEB activation severity levels."""
  NONE = 0
  CAUTION = 1       # Far object, monitor only
  WARNING = 2       # Medium distance, alert driver (FCW)
  CRITICAL = 3      # Close distance, pre-charge brakes
  EMERGENCY = 4     # Imminent collision, AEB trigger


class AEBState(Enum):
  """AEB controller internal states."""
  IDLE = auto()
  PRECHARGE = auto()
  BRAKING = auto()
  HOLD = auto()
  OVERRIDE = auto()


@dataclass
class TrackedObject:
  """Object for collision prediction."""
  track_id: int
  x: float           # Forward distance (m)
  y: float           # Lateral distance (m, positive=left)
  v_x: float         # Forward velocity (m/s)
  v_y: float         # Lateral velocity (m/s)
  width: float       # Object width (m)
  length: float      # Object length (m)
  obj_type: str      # 'car', 'truck', 'pedestrian', 'cyclist', etc.
  confidence: float  # Detection confidence (0-1)


@dataclass
class EgoState:
  """Ego vehicle state for AEB."""
  v_ego: float           # m/s
  a_ego: float           # m/s²
  brake_pressed: bool
  throttle_pressed: bool
  steering_torque: float # Nm


@dataclass
class RSSParameters:
  """RSS (Responsibility-Sensitive Safety) parameters."""
  reaction_time: float = 1.0      # seconds
  max_accel: float = 2.0          # m/s² comfortable accel
  min_brake: float = -2.5         # m/s² comfortable brake
  max_brake: float = -5.0         # m/s² emergency brake
  # TTC thresholds
  ttc_warning: float = 3.0
  ttc_critical: float = 1.5
  ttc_emergency: float = 0.5
  # Distance ratio thresholds (as fraction of safe distance)
  caution_ratio: float = 2.0
  warning_ratio: float = 1.0
  critical_ratio: float = 0.5
  emergency_ratio: float = 0.25


@dataclass
class AEBResult:
  """AEB controller output."""
  level: AEBLevel
  state: AEBState
  ttc: float
  distance: float
  target_decel: float     # m/s² (negative = braking)
  is_active: bool
  is_overridden: bool
  reason: str
  object_type: str


def _r(level: AEBLevel, state: AEBState, ttc: float, distance: float,
       target_decel: float, is_active: bool, is_overridden: bool,
       reason: str, object_type: str = "") -> AEBResult:
  """Factory helper — eliminates 15+ identical AEBResult() constructor repetitions."""
  return AEBResult(
    level=level, state=state, ttc=ttc, distance=distance,
    target_decel=target_decel, is_active=is_active,
    is_overridden=is_overridden, reason=reason, object_type=object_type
  )


class CollisionPredictor:
  """
  Predicts forward collisions using RSS safety model.

  Consumes tracked objects and calculates TTC (Time-to-Collision)
  using the Mobileye RSS formula for safe longitudinal distance.
  """

  def __init__(self, params: RSSParameters | None = None):
    self.params = params or RSSParameters()
    self._history: deque = deque(maxlen=10)

  def check_collision(
    self,
    ego_state: EgoState,
    objects: list[TrackedObject],
    path_width: float = 2.5
  ) -> TrackedObject | None:
    """
    Check all objects for collision risk.

    Returns:
      Most critical object, or None if no collision risk
    """
    most_critical: TrackedObject | None = None
    min_ttc = float('inf')

    for obj in objects:
      if obj.confidence < 0.5:
        continue
      if obj.x < 0:  # Behind us
        continue

      # Check if object is in path
      if not self._is_in_path(obj, path_width):
        continue

      # Calculate TTC
      v_rel = ego_state.v_ego - obj.v_x
      if v_rel <= 0.5:
        continue  # Not closing

      ttc = obj.x / v_rel
      if ttc < min_ttc:
        min_ttc = ttc
        most_critical = obj

    return most_critical

  def _is_in_path(self, obj: TrackedObject, path_width: float) -> bool:
    """Check if object is within ego path."""
    in_path_now = abs(obj.y) < (path_width / 2 + obj.width / 2)

    # Predict if object will enter path within 3 seconds
    if abs(obj.v_y) > 0.1:
      time_to_center = abs(obj.y / obj.v_y) if obj.v_y != 0 else float('inf')
      if time_to_center < 3.0:
        return True

    return in_path_now

  def calculate_rss_distance(self, v_ego: float, v_obj: float) -> float:
    """
    Calculate RSS safe longitudinal distance.

    d_min = [v_ego * rho + 0.5 * a_max * rho² + (v_ego + rho*a_max)²/(2*b_min)
             - v_obj²/(2*b_max)]_+
    """
    rho = self.params.reaction_time
    a_max = self.params.max_accel
    b_min = abs(self.params.min_brake)
    b_max = abs(self.params.max_brake)

    v_ego_after = max(0, v_ego + a_max * rho)
    d_ego = v_ego * rho + 0.5 * a_max * rho**2 + v_ego_after**2 / (2 * b_min)
    d_obj = v_obj**2 / (2 * b_max) if v_obj > 0 else 0

    return max(0.0, d_ego - d_obj)

  def determine_level(self, ttc: float, distance: float, safe_dist: float) -> AEBLevel:
    """Determine AEB level based on TTC and distance ratio."""
    if safe_dist <= 0:
      return AEBLevel.NONE

    ratio = distance / safe_dist

    if ttc < self.params.ttc_emergency or ratio < self.params.emergency_ratio:
      return AEBLevel.EMERGENCY
    if ttc < self.params.ttc_critical or ratio < self.params.critical_ratio:
      return AEBLevel.CRITICAL
    if ttc < self.params.ttc_warning or ratio < self.params.warning_ratio:
      return AEBLevel.WARNING
    if ratio < self.params.caution_ratio:
      return AEBLevel.CAUTION

    return AEBLevel.NONE


class BrakingController:
  """
  Emergency braking controller with progressive profiles.

  State machine:
    IDLE → PRECHARGE (TTC < 1.5s)
    PRECHARGE → BRAKING (TTC < 0.8s)
    BRAKING → HOLD (near stop, threat cleared)
    Any state → OVERRIDE (driver intervention)
  """

  # Thresholds
  TTC_PARTIAL = 4.0     # Earliest staged-braking envelope
  TTC_FULL = 1.2        # Imminent-collision backstop
  TTC_RELEASE = 4.5     # Release braking threshold

  # Deceleration levels (m/s²)
  DECEL_PARTIAL = -1.5
  DECEL_COMFORT = -2.5  # strongest ordinary cruise/following request
  # Collision-mitigation request ceiling from the Tesla protocol/controller
  # envelope (TC275 backstop: -3.5). This is not a UN R152-qualified AEBS
  # value: R152 emergency braking demands at least 5.0 m/s², and layer-1
  # safety currently keeps ordinary commands above -2.5 m/s². See the safety
  # envelope document before changing either independent safety layer.
  DECEL_FULL = -3.48
  DECEL_MAX = -3.48
  DECEL_HOLD = -1.0     # Hold at standstill

  # Jerk limits (m/s³)
  JERK_NORMAL = 2.0
  # Faster than the normal profile, with margin below TC275's 5.0 m/s³ hard
  # backstop for timestamp and CAN quantization error.
  JERK_EMERGENCY = 4.5
  STOP_BUFFER_M = 2.0
  WARNING_LEAD_S = 0.8  # UN R152 warning lead when the collision is anticipatable

  # Override detection
  STEERING_OVERRIDE_NM = 3.0

  def __init__(self):
    self.state = AEBState.IDLE
    self._current_decel = 0.0
    self._override_active = False
    self._standstill_time = 0.0
    self._precharge_elapsed_s = 0.0

  def update(
    self,
    ego_state: EgoState,
    threat: TrackedObject | None,
    dt: float = 0.05
  ) -> AEBResult:
    """
    Update braking controller and return result.

    Args:
      ego_state: Current vehicle state
      threat: Most critical collision threat (None if no threat)
      dt: Time step (seconds)

    Returns:
      AEBResult with level, target deceleration, and state
    """
    # Check driver override
    if self._check_override(ego_state):
      if self.state not in [AEBState.IDLE, AEBState.OVERRIDE]:
        self._enter_override()
      return _r(AEBLevel.NONE, AEBState.OVERRIDE, float('inf'), 0.0, 0.0, False, True, "Driver override")

    # Handle override recovery
    if self.state == AEBState.OVERRIDE:
      if not self._check_override(ego_state):
        self.state = AEBState.IDLE
        self._override_active = False
      else:
        return _r(AEBLevel.NONE, AEBState.OVERRIDE, float('inf'), 0.0, 0.0, False, True, "Driver override active")

    # State machine
    if self.state == AEBState.IDLE:
      return self._state_idle(ego_state, threat)
    elif self.state == AEBState.PRECHARGE:
      return self._state_precharge(ego_state, threat, dt)
    elif self.state == AEBState.BRAKING:
      return self._state_braking(ego_state, threat, dt)
    elif self.state == AEBState.HOLD:
      return self._state_hold(ego_state, threat)

    return _r(AEBLevel.NONE, AEBState.IDLE, float('inf'), 0.0, 0.0, False, False, "Unknown state")

  def _check_override(self, ego_state: EgoState) -> bool:
    """Check if driver is overriding AEB."""
    if abs(ego_state.steering_torque) > self.STEERING_OVERRIDE_NM:
      return True
    if ego_state.throttle_pressed and self.state == AEBState.BRAKING:
      return True
    return False

  def _enter_override(self):
    """Enter override state."""
    self.state = AEBState.OVERRIDE
    self._override_active = True
    self._current_decel = 0.0

  def _state_idle(self, ego_state: EgoState, threat: TrackedObject | None) -> AEBResult:
    """Idle state - monitoring."""
    if threat is None:
      return _r(AEBLevel.NONE, AEBState.IDLE, float('inf'), 0.0, 0.0, False, False, "No threat")

    ttc = threat.x / max(0.1, ego_state.v_ego - threat.v_x) if ego_state.v_ego > threat.v_x else float('inf')

    if ttc < self.TTC_PARTIAL:
      self.state = AEBState.PRECHARGE
      self._precharge_elapsed_s = 0.0
      return _r(AEBLevel.CRITICAL, AEBState.PRECHARGE, ttc, threat.x, 0.0, True, False,
                "Collision warning before braking", threat.obj_type)

    level = AEBLevel.CAUTION if ttc < self.TTC_RELEASE else AEBLevel.NONE
    return _r(level, AEBState.IDLE, ttc, threat.x, 0.0, False, False, "Monitoring", threat.obj_type)

  def _state_precharge(self, ego_state: EgoState, threat: TrackedObject | None, dt: float) -> AEBResult:
    """Apply smooth partial braking while the threat remains feasible."""
    if threat is None:
      self.state = AEBState.IDLE
      return _r(AEBLevel.NONE, AEBState.IDLE, float('inf'), 0.0, 0.0, False, False, "Threat cleared")

    ttc = threat.x / max(0.1, ego_state.v_ego - threat.v_x) if ego_state.v_ego > threat.v_x else float('inf')
    self._precharge_elapsed_s += dt

    # UN R152 calls for warning before emergency braking when the collision is
    # anticipatable. If detection arrives already imminent, intervene at once
    # for impact mitigation instead of waiting out the warning timer.
    if self._precharge_elapsed_s < self.WARNING_LEAD_S and ttc > self.WARNING_LEAD_S:
      return _r(AEBLevel.CRITICAL, AEBState.PRECHARGE, ttc, threat.x, 0.0, True, False,
                "Collision warning before braking", threat.obj_type)

    target = self._calculate_decel(threat, ttc)
    if ttc < self.TTC_FULL or target < self.DECEL_COMFORT:
      self.state = AEBState.BRAKING
      max_change = self.JERK_EMERGENCY * dt
      self._current_decel = max(target, self._current_decel - max_change)
      return _r(AEBLevel.EMERGENCY, AEBState.BRAKING, ttc, threat.x, self._current_decel, True, False,
                f"Emergency braking: TTC={ttc:.2f}s", threat.obj_type)

    max_change = self.JERK_NORMAL * dt
    self._current_decel = max(target, self._current_decel - max_change)
    return _r(AEBLevel.CRITICAL, AEBState.PRECHARGE, ttc, threat.x, self._current_decel, True, False,
              "Controlled collision braking", threat.obj_type)

  def _state_braking(self, ego_state: EgoState, threat: TrackedObject | None, dt: float) -> AEBResult:
    """Active braking state."""
    # Check if we should release
    if threat is None:
      if ego_state.v_ego < 0.5:
        self.state = AEBState.HOLD
        return _r(AEBLevel.EMERGENCY, AEBState.HOLD, float('inf'), 0.0, self.DECEL_HOLD, True, False, "Hold at standstill")
      else:
        # Gradually release
        self._current_decel = min(0.0, self._current_decel + 2.0 * dt)
        if self._current_decel >= -0.1:
          self.state = AEBState.IDLE
          self._current_decel = 0.0
          return _r(AEBLevel.NONE, AEBState.IDLE, float('inf'), 0.0, 0.0, False, False, "Braking released")
        return _r(AEBLevel.EMERGENCY, AEBState.BRAKING, float('inf'), 0.0, self._current_decel, True, False, "Threat cleared, releasing")

    ttc = threat.x / max(0.1, ego_state.v_ego - threat.v_x) if ego_state.v_ego > threat.v_x else float('inf')

    if ttc > self.TTC_RELEASE:
      # Threat receding, release gradually
      self._current_decel = min(0.0, self._current_decel + 2.0 * dt)
      if self._current_decel >= -0.1:
        self.state = AEBState.IDLE
        self._current_decel = 0.0
        return _r(AEBLevel.NONE, AEBState.IDLE, ttc, threat.x, 0.0, False, False, "Threat receding", threat.obj_type)

    # Calculate target deceleration
    target_decel = self._calculate_decel(threat, ttc)

    # Apply ramp rate limiting
    max_change = self.JERK_EMERGENCY * dt
    if target_decel < self._current_decel:
      self._current_decel = max(target_decel, self._current_decel - max_change)
    else:
      self._current_decel = min(target_decel, self._current_decel + max_change)

    return _r(AEBLevel.EMERGENCY, AEBState.BRAKING, ttc, threat.x, self._current_decel, True, False,
              "Emergency braking active", threat.obj_type)

  def _state_hold(self, ego_state: EgoState, threat: TrackedObject | None) -> AEBResult:
    """Hold at standstill."""
    if ego_state.throttle_pressed:
      self.state = AEBState.IDLE
      return _r(AEBLevel.NONE, AEBState.IDLE, float('inf'), 0.0, 0.0, False, False, "Driver throttle - releasing hold")

    if ego_state.v_ego < 0.5:
      return _r(AEBLevel.EMERGENCY, AEBState.HOLD, float('inf'), 0.0, self.DECEL_HOLD, True, False, "Hold at standstill")
    else:
      self.state = AEBState.IDLE
      return _r(AEBLevel.NONE, AEBState.IDLE, float('inf'), 0.0, 0.0, False, False, "Vehicle moved - releasing hold")

  def _calculate_decel(self, threat: TrackedObject, ttc: float) -> float:
    """Request only the deceleration needed within the bounded envelope."""
    if not (0.0 < ttc < float('inf')):
      return self.DECEL_PARTIAL
    closing_speed = threat.x / ttc
    usable_distance = max(threat.x - self.STOP_BUFFER_M, 0.5)
    required = closing_speed * closing_speed / (2.0 * usable_distance)
    requested = max(abs(self.DECEL_PARTIAL), min(required * 1.1, abs(self.DECEL_MAX)))
    return -requested

  def reset(self):
    """Reset controller state."""
    self.state = AEBState.IDLE
    self._current_decel = 0.0
    self._override_active = False
    self._precharge_elapsed_s = 0.0


class AEB:
  """
  Main AEB controller integrating collision prediction and braking.

  Usage:
    aeb = AEB()
    result = aeb.update(sm, CS)
    if result.level == AEBLevel.EMERGENCY:
      actuators.accel = min(actuators.accel, result.target_decel)
  """

  # radard already treats <=0.75 m as the near-field glitch region. Above that
  # floor, keep braking even when a full stop is impossible: impact mitigation
  # remains valuable.
  MIN_ENTRY_RANGE_M = 0.75
  MAX_ENTRY_RANGE_M = 120.0
  MIN_EGO_SPEED_MS = 2.0
  MIN_CLOSING_SPEED_MS = 1.0
  MIN_RADAR_CONFIDENCE = 0.70
  MAX_ENTRY_TTC_S = BrakingController.TTC_PARTIAL
  MIN_REQUIRED_DECEL_MS2 = 0.8
  ENTRY_CONFIRM_FRAMES = 3

  def __init__(self):
    self.params = Params()
    self.enabled = self.params.get_bool("EOPAEBEnabled")
    self.predictor = CollisionPredictor()
    self.braking = BrakingController()
    self._last_update_time = 0.0
    self._frame_count = 0
    self._candidate_track_id: int | None = None
    self._candidate_frames = 0
    self._candidate_x_m = 0.0
    cloudlog.info(f"AEB initialized: enabled={self.enabled}")

  def update(self, sm, CS) -> AEBResult:
    """
    Main update loop.

    Args:
      sm: SubMaster with radarState, modelV2, monoDetections
      CS: carState

    Returns:
      AEBResult with braking request
    """
    self.enabled = self.params.get_bool("EOPAEBEnabled")
    if not self.enabled:
      return _r(AEBLevel.NONE, AEBState.IDLE, float('inf'), 0.0, 0.0, False, False, "AEB disabled")

    self._frame_count += 1
    current_time = time.monotonic()
    dt = current_time - self._last_update_time if self._last_update_time > 0 else DT_MDL
    self._last_update_time = current_time

    # Build ego state
    ego = EgoState(
      v_ego=max(CS.vEgo, 0.0),
      a_ego=CS.aEgo,
      brake_pressed=CS.brakePressed,
      throttle_pressed=CS.gasPressed,
      steering_torque=getattr(CS, 'steeringTorque', 0.0)
    )

    # Collect tracked objects from all sources
    objects = self._collect_objects(sm)

    # Find most critical threat
    threat = self.predictor.check_collision(ego, objects)
    threat = self._confirmed_entry_threat(threat, ego)

    # Run braking controller
    result = self.braking.update(ego, threat, dt)

    # Log at warning level for actual braking events
    if result.level == AEBLevel.EMERGENCY and self._frame_count % 10 == 0:
      cloudlog.warning(f"AEB EMERGENCY: TTC={result.ttc:.2f}s, decel={result.target_decel:.1f}m/s², type={result.object_type}")

    return result

  def _confirmed_entry_threat(self, threat: TrackedObject | None,
                              ego: EgoState) -> TrackedObject | None:
    """Reject implausible/single-frame targets before AEB state entry."""
    active = self.braking.state in (AEBState.PRECHARGE, AEBState.BRAKING, AEBState.HOLD)
    if threat is None or not self._entry_plausible(threat, ego, active):
      self._candidate_track_id = None
      self._candidate_frames = 0
      return None
    if active:
      return threat

    range_continuous = self._candidate_track_id == threat.track_id and abs(threat.x - self._candidate_x_m) < 8.0
    if range_continuous:
      self._candidate_frames += 1
    else:
      self._candidate_track_id = threat.track_id
      self._candidate_frames = 1
    self._candidate_x_m = threat.x
    return threat if self._candidate_frames >= self.ENTRY_CONFIRM_FRAMES else None

  def _entry_plausible(self, threat: TrackedObject, ego: EgoState, active: bool = False) -> bool:
    """Check the measured target lies inside the plausible AEB envelope."""
    if not all(math.isfinite(value) for value in
               (threat.x, threat.y, threat.v_x, threat.confidence, ego.v_ego)):
      return False
    min_range = 0.1 if active else self.MIN_ENTRY_RANGE_M
    if not (min_range <= threat.x <= self.MAX_ENTRY_RANGE_M):
      return False
    if ego.v_ego < self.MIN_EGO_SPEED_MS or threat.confidence < self.MIN_RADAR_CONFIDENCE:
      return False
    closing_speed = ego.v_ego - threat.v_x
    if closing_speed < self.MIN_CLOSING_SPEED_MS:
      return False
    ttc = threat.x / closing_speed
    usable_distance = max(threat.x - BrakingController.STOP_BUFFER_M, 0.5)
    required_decel = closing_speed * closing_speed / (2.0 * usable_distance)
    return active or (ttc <= self.MAX_ENTRY_TTC_S and required_decel >= self.MIN_REQUIRED_DECEL_MS2)

  def _collect_objects(self, sm) -> list[TrackedObject]:
    """Collect only leads measured by the vehicle's built-in 77 GHz radar.

    This hard boundary prevents BLE corner radar or camera-only tracks from
    acquiring braking authority. Those sources remain available to FCW/RCW/
    FCTA/RCTA advisory logic in RadarZoneMonitor.
    """
    objects: list[TrackedObject] = []

    if sm.valid.get('radarState', False):
      radar = sm['radarState']
      if radar.leadOne.status and radar.leadOne.radar:
        objects.append(TrackedObject(
          track_id=int(radar.leadOne.radarTrackId),
          x=radar.leadOne.dRel,
          y=-radar.leadOne.yRel,  # Convert to left-positive
          v_x=radar.leadOne.vRel + sm['carState'].vEgo,  # Absolute velocity
          v_y=0.0,
          width=1.8,
          length=4.5,
          obj_type='car',
          confidence=radar.leadOne.modelProb
        ))
      if radar.leadTwo.status and radar.leadTwo.radar:
        objects.append(TrackedObject(
          track_id=int(radar.leadTwo.radarTrackId),
          x=radar.leadTwo.dRel,
          y=-radar.leadTwo.yRel,
          v_x=radar.leadTwo.vRel + sm['carState'].vEgo,
          v_y=0.0,
          width=1.8,
          length=4.5,
          obj_type='car',
          confidence=radar.leadTwo.modelProb
        ))

    return objects

  # EOP-CLEANUP: Removed get_fcw_level() — was a placeholder that always
  # returned False. Actual FCW logic lives in update() -> BrakingController.


# Convenience function for controlsd integration
def get_aeb_decel_limit(aeb_result: AEBResult, current_accel: float) -> float:
  """
  Calculate accel limit considering AEB request.

  Returns the minimum (most braking) of current accel and AEB request.
  """
  if not aeb_result.is_active:
    return current_accel
  return min(current_accel, aeb_result.target_decel)

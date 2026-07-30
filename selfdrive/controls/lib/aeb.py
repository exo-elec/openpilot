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
  - radarState: Lead vehicle data from radar
  - modelV2: drive_vision lead predictions
  - monoDetections: Multi-camera detections from monod (pedestrians, cyclists)
  - carState: Ego vehicle state (velocity, acceleration, steering torque)

Outputs:
  - aebState: Published via driverAssistance message
  - Emergency deceleration request: Consumed by controlsd

Safety Design:
  - NEVER overrides driver steering or throttle directly
  - Driver override detection (steering >3Nm or throttle during braking)
  - Progressive braking: precharge → partial → full
  - Vulnerable road users (pedestrians/cyclists) get maximum braking

Reference: Mobileye RSS model ( Responsibility-Sensitive Safety )
"""
from __future__ import annotations

import time
import math
from dataclasses import dataclass
from enum import Enum, auto
from collections import deque

import numpy as np

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
  TTC_PARTIAL = 1.5     # Start partial braking / pre-charge
  TTC_FULL = 0.8        # Start full emergency braking
  TTC_RELEASE = 3.0     # Release braking threshold
  
  # Deceleration levels (m/s²)
  DECEL_PARTIAL = -3.0
  DECEL_FULL = -5.0
  DECEL_MAX = -8.0      # For vulnerable road users
  DECEL_HOLD = -2.0     # Hold at standstill
  
  # Jerk limits (m/s³)
  JERK_NORMAL = 10.0
  JERK_EMERGENCY = 25.0
  
  # Override detection
  STEERING_OVERRIDE_NM = 3.0
  
  def __init__(self):
    self.state = AEBState.IDLE
    self._current_decel = 0.0
    self._override_active = False
    self._standstill_time = 0.0
  
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
      return self._state_precharge(ego_state, threat)
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
      return _r(AEBLevel.CRITICAL, AEBState.PRECHARGE, ttc, threat.x, 0.0, True, False,
                "Pre-charge for imminent braking", threat.obj_type)
    
    level = AEBLevel.CAUTION if ttc < self.TTC_RELEASE else AEBLevel.NONE
    return _r(level, AEBState.IDLE, ttc, threat.x, 0.0, False, False, "Monitoring", threat.obj_type)
  
  def _state_precharge(self, ego_state: EgoState, threat: TrackedObject | None) -> AEBResult:
    """Pre-charge state - ready for braking."""
    if threat is None:
      self.state = AEBState.IDLE
      return _r(AEBLevel.NONE, AEBState.IDLE, float('inf'), 0.0, 0.0, False, False, "Threat cleared")
    
    ttc = threat.x / max(0.1, ego_state.v_ego - threat.v_x) if ego_state.v_ego > threat.v_x else float('inf')
    
    if ttc < self.TTC_FULL:
      self.state = AEBState.BRAKING
      decel = self._calculate_decel(threat, ttc)
      self._current_decel = decel
      return _r(AEBLevel.EMERGENCY, AEBState.BRAKING, ttc, threat.x, decel, True, False,
                f"Emergency braking: TTC={ttc:.2f}s", threat.obj_type)
    
    return _r(AEBLevel.CRITICAL, AEBState.PRECHARGE, ttc, threat.x, 0.0, True, False,
              "Brake pre-charged", threat.obj_type)
  
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
    """Calculate braking deceleration based on threat."""
    # Vulnerable road users get maximum braking
    is_vulnerable = threat.obj_type in ('pedestrian', 'cyclist', 'motorcycle', 'bicycle')
    if is_vulnerable:
      return self.DECEL_MAX
    
    # Progressive braking based on TTC
    if ttc < 0.5:
      return self.DECEL_FULL
    elif ttc < self.TTC_FULL:
      ratio = (self.TTC_FULL - ttc) / (self.TTC_FULL - 0.5)
      return self.DECEL_PARTIAL + (self.DECEL_FULL - self.DECEL_PARTIAL) * ratio
    else:
      return self.DECEL_PARTIAL
  
  def reset(self):
    """Reset controller state."""
    self.state = AEBState.IDLE
    self._current_decel = 0.0
    self._override_active = False


class AEB:
  """
  Main AEB controller integrating collision prediction and braking.
  
  Usage:
    aeb = AEB()
    result = aeb.update(sm, CS)
    if result.level == AEBLevel.EMERGENCY:
      actuators.accel = min(actuators.accel, result.target_decel)
  """
  
  # Radar4D weather severity (0=clear..3=heavy) margin scales.
  # min_brake: less braking grip assumed on wet/slippery surfaces.
  # reaction_time: extra perceived delay in adverse weather.
  # TTC_PARTIAL/TTC_FULL: trigger earlier so braking finishes in time.
  WEATHER_BRAKE_SCALE = (1.0, 0.9, 0.8, 0.65)
  WEATHER_REACTION_ADD_S = (0.0, 0.1, 0.25, 0.5)
  WEATHER_TTC_SCALE = (1.0, 1.1, 1.25, 1.5)

  def __init__(self):
    self.params = Params()
    self.enabled = self.params.get_bool("EOPAEBEnabled")
    self.predictor = CollisionPredictor()
    self.braking = BrakingController()
    self._last_update_time = 0.0
    self._frame_count = 0
    self._base_min_brake = self.predictor.params.min_brake
    self._base_reaction_s = self.predictor.params.reaction_time
    self._base_ttc_partial = BrakingController.TTC_PARTIAL
    self._base_ttc_full = BrakingController.TTC_FULL

    cloudlog.info(f"AEB initialized: enabled={self.enabled}")

  def apply_weather_margins(self, severity: int):
    """Scale braking margins from radar4d weather severity (0=clear..3=heavy)."""
    severity = max(0, min(int(severity), 3))
    self.predictor.params.min_brake = self._base_min_brake * self.WEATHER_BRAKE_SCALE[severity]
    self.predictor.params.reaction_time = self._base_reaction_s + self.WEATHER_REACTION_ADD_S[severity]
    self.braking.TTC_PARTIAL = self._base_ttc_partial * self.WEATHER_TTC_SCALE[severity]
    self.braking.TTC_FULL = self._base_ttc_full * self.WEATHER_TTC_SCALE[severity]
  
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

    severity = int(sm['radar4d'].weatherSeverity) if sm.valid.get('radar4d', False) else 0
    self.apply_weather_margins(severity)
    
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
    
    # Run braking controller
    result = self.braking.update(ego, threat, dt)
    
    # Log at warning level for actual braking events
    if result.level == AEBLevel.EMERGENCY and self._frame_count % 10 == 0:
      cloudlog.warning(f"AEB EMERGENCY: TTC={result.ttc:.2f}s, decel={result.target_decel:.1f}m/s², type={result.object_type}")
    
    return result
  
  def _collect_objects(self, sm) -> list[TrackedObject]:
    """
    Collect tracked objects from all perception sources.
    
    Priority (most accurate distance first):
      1. stereoDetections - 3D objects from stereo depth (most accurate)
      2. radarState - Radar leads (reliable for cars)
      3. modelV2 - drive_vision leads (neural prediction)
      4. monoDetections - monod multi-camera (pedestrians/cyclists)
    """
    objects: list[TrackedObject] = []
    
    # From stereoDetections (stereod - 3D objects with stereo depth, MOST ACCURATE)
    if sm.valid.get('stereoDetections', False):
      stereo = sm['stereoDetections']
      for i, det in enumerate(stereo.detections):
        obj_type = det.className.lower()
        # Map to AEB object types
        if obj_type in ('car', 'truck', 'bus', 'vehicle'):
         aeb_type = 'car'
        elif obj_type in ('pedestrian', 'person'):
          aeb_type = 'pedestrian'
        elif obj_type in ('cyclist', 'bicycle', 'motorcycle', 'motorcyclist'):
          aeb_type = 'cyclist'
        else:
          aeb_type = obj_type
        
        objects.append(TrackedObject(
          track_id=300 + i,
          x=det.center.z,  # Forward distance from stereo depth
          y=-det.center.x,  # Lateral (convert to left-positive)
          v_x=0.0,  # Stereo doesn't provide velocity directly
          v_y=0.0,
          width=det.size.y if hasattr(det, 'size') else 1.8,
          length=det.size.x if hasattr(det, 'size') else 4.5,
          obj_type=aeb_type,
          confidence=det.confidence * getattr(det, 'depthConfidence', 1.0)
        ))
    
    # From radarState (reliable for lead vehicles)
    if sm.valid.get('radarState', False):
      radar = sm['radarState']
      if radar.leadOne.status:
        objects.append(TrackedObject(
          track_id=0,
          x=radar.leadOne.dRel,
          y=-radar.leadOne.yRel,  # Convert to left-positive
          v_x=radar.leadOne.vRel + sm['carState'].vEgo,  # Absolute velocity
          v_y=0.0,
          width=1.8,
          length=4.5,
          obj_type='car',
          confidence=radar.leadOne.modelProb
        ))
      if radar.leadTwo.status:
        objects.append(TrackedObject(
          track_id=1,
          x=radar.leadTwo.dRel,
          y=-radar.leadTwo.yRel,
          v_x=radar.leadTwo.vRel + sm['carState'].vEgo,
          v_y=0.0,
          width=1.8,
          length=4.5,
          obj_type='car',
          confidence=radar.leadTwo.modelProb
        ))
    
    # From modelV2 (drive_vision leads - neural prediction fallback)
    if sm.valid.get('modelV2', False):
      model = sm['modelV2']
      for i, lead in enumerate(model.leadsV3):
        if lead.prob < 0.5:
          continue
        objects.append(TrackedObject(
          track_id=100 + i,
          x=lead.x[0],
          y=lead.y[0],
          v_x=lead.v[0],
          v_y=0.0,
          width=1.8,
          length=4.5,
          obj_type='car',
          confidence=lead.prob
        ))
    
    # From monoDetections (monod - pedestrians, cyclists, vulnerable road users)
    if sm.valid.get('monoDetections', False):
      mono = sm['monoDetections']
      for det in mono.detections:
        obj_type = det.className.lower()
        if obj_type not in ('pedestrian', 'cyclist', 'motorcycle', 'bicycle', 'person'):
          continue
        objects.append(TrackedObject(
          track_id=200 + det.trackId,
          x=det.x,
          y=det.y,
          v_x=det.vx if hasattr(det, 'vx') else 0.0,
          v_y=det.vy if hasattr(det, 'vy') else 0.0,
          width=det.width if hasattr(det, 'width') else 0.5,
          length=det.height if hasattr(det, 'height') else 1.7,
          obj_type='pedestrian' if obj_type in ('pedestrian', 'person') else obj_type,
          confidence=det.confidence
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

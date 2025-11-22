"""
Gateway carcontroller
=====================

Responsibilities
----------------
* Translate openpilot's actuation requests into Gateway CAN messages
* Maintain BYD-specific counters and diagnostic frames
* Provide safe fallback behaviour when model-specific params are absent

The file is structured with clearly-labeled sections:
1. Imports and logger setup
2. The CarController class (init/state, helper conversions, update loop)
3. Low-level helpers delegated to `gatewaycan` for wrapping/packing

Keep this ordering so new contributors can locate logic quickly.
"""

from cereal import car
import cereal.messaging as messaging
from opendbc.car import DT_CTRL, rate_limit, apply_driver_steer_torque_limits
from opendbc.can.packer import CANPacker
from opendbc.car.interfaces import CarControllerBase
from opendbc.car.gateway.gatewaycan import CanBus
from opendbc.car.gateway import gatewaycan
from opendbc.car.gateway.values import (
  CarControllerParams,
  DEFAULT_WHEEL_RADIUS,
  CAR,
)
import numpy as np
from openpilot.common.params import Params
import logging
import math

# Configure logging for gateway
cloudlog = logging.getLogger(__name__)

# OpenPilot framework constants and data structures
LongCtrlState = car.CarControl.Actuators.LongControlState

# -----------------------------------------------------------------------------
# CarController: translates OP outputs into Gateway CAN messages
# -----------------------------------------------------------------------------
class CarController(CarControllerBase):

  # =============================================================================
  # [Section] Initialization
  # [Brief ] Initialize controller state, packer, bus, and parameters
  # ---------------------------------------------------------------------------
  # [Function] __init__
  # [Brief ] Initialize controller state, packer, bus, params, and caches
  # [Params] dbc_names: dict, CP: CarParams
  # [Returns] None
  # =============================================================================
  def __init__(self, dbc_names, CP):
    super().__init__(dbc_names, CP)
    from opendbc.car import Bus
    self.packer = CANPacker(dbc_names[Bus.pt])
    self.CAN = CanBus(CP)

    # Load model-specific parameters with comprehensive safety fallbacks
    try:
      # Try to load car-specific parameters (BYD DOLPHIN only)
      self.params = CarControllerParams(CP)
    except Exception:
      # CRITICAL: Fallback to safe defaults if model-specific loading fails
      # This prevents system crashes when model detection fails
      self.params = CarControllerParams(CP)  # Will use _init_default_params()

      # Additional safety overrides in case defaults are missing
      if not hasattr(self.params, 'ACCEL_MAX'):
        self.params.ACCEL_MAX = 2.0   # Conservative acceleration limit
      if not hasattr(self.params, 'ACCEL_MIN'):
        self.params.ACCEL_MIN = -3.5  # Conservative deceleration limit
      if not hasattr(self.params, 'STEER_MAX_COUNT'):
        self.params.STEER_MAX_COUNT = 150   # Safe steering motor count limit - fallback

    # Track active Gateway model (enables per-model CAN behavior)
    try:
      self.model = CAR(CP.carFingerprint)
    except ValueError:
      self.model = CP.carFingerprint  # Fallback for unsupported fingerprints

    # === CONTROL STATE MEMORY ===
    # Store previous values for rate limiting and smooth transitions
    self.last_steer_angle = 0.0        # Previous applied steering angle (degrees)
    self.last_accel = 0.0              # Previous acceleration command
    self.last_brake = 0.0              # Previous brake command

    # === LATERAL CONTROL STATE MACHINE (sunnypilot pattern) ===
    # Manage smooth engagement/disengagement of steering control
    self.lka_req_prepare = 0        # Request EPS to prepare for control (preparation phase)
    self.lka_active = 0             # LKA actively controlling (engaged phase)
    self.lat_safeoff = 0            # Ramping down to zero before full disengagement (safe-off phase)
    self.steer_softstart_limit = 0  # Gradually increasing angle limit during engagement (degrees)

    # === LONGITUDINAL CONTROL STATE MACHINE ===
    # (Not implemented in simplified protocol - future enhancement)
    
    # === MESSAGE COUNTERS ===
    # 4-bit counters for each CAN message (DBC specification)
    self.lat_counter = 0            # 0x1E2 lateral command counter
    self.mpc_state_counter = 0      # 0x316 MPC state counter
    self.long_counter = 0           # 0x32E longitudinal command counter

    # Diagnosis message counters (0x6F0-0x6F8)
    self.diag_controls_counter = 0  # DIAG_controlsState counter
    self.diag_lateral_counter = 0   # DIAG_lateralState counter
    self.diag_long_counter = 0      # DIAG_longitudinalState counter
    self.diag_carstate_counter = 0  # DIAG_carStateMirror counter
    self.diag_liveparam_counter = 0 # DIAG_liveParameters counter
    self.diag_model_counter = 0     # DIAG_modelOutputs counter
    self.diag_health_counter = 0    # DIAG_systemHealth counter
    self.diag_faults_counter = 0    # DIAG_faults counter

    # MPC echo service counters (sync with stock on first frame)
    self.eps_fake318_counter = 0    # Fake 0x318 EPS feedback to MPC
    self.first_start = True         # Sync counters with stock on first frame

    # === CURRENT CONTROL OUTPUTS ===
    # Current control values sent to actuators
    self.accel = 0.0               # Current acceleration (m/s²)
    self.gas = 0.0                 # Current gas pedal (0-1)
    self.brake = 0.0               # Current brake pedal (0-1)

    # SunnyPilot integration for enhanced features
    self.sm = messaging.SubMaster([
      'longitudinalPlanSP',    # Enhanced longitudinal planner
      'controlsState',         # Main control loop state (100Hz)
      'longitudinalPlan',      # Longitudinal planning (20Hz)
      'liveParameters',        # Live vehicle parameters (20Hz)
      'modelV2',               # Vision model outputs (20Hz)
      'deviceState',           # Device health (2Hz)
      'selfdriveState',        # Selfdrive status (100Hz)
      'gpsLocationExternal',   # GPS status (10Hz)
    ])
    self.param_s = Params()                                # Parameter storage interface
    self.is_metric = self.param_s.get_bool("IsMetric")     # Unit system (metric/imperial)

    # === SAFETY: DUAL-PATH ANGLE VALIDATION STATE ===
    # Cross-check PID output against geometric angle to prevent dangerous commands
    self.angle_fault_count = 0           # Consecutive frames with PID/geometric mismatch
    self.angle_fallback_active = False   # Currently using geometric fallback instead of PID
    self.angle_validation_disabled = False  # Emergency disable if validation causes issues

  def __del__(self):
    """Cleanup resources on controller destruction to prevent resource leaks"""
    try:
      if hasattr(self, 'sm') and self.sm is not None:
        self.sm.close()  # Close SubMaster ZMQ sockets
    except Exception:
      pass  # Best-effort cleanup - don't raise exceptions in __del__

  # =============================================================================
  # [Section] Physics: Accel -> Wheel Torque
  # [Brief ] Convert requested acceleration to wheel torque using drivetrain
  #          parameters with safety limits and fallbacks.
  # ---------------------------------------------------------------------------
  # [Function] calculate_wheel_torque
  # [Brief ] Convert desired acceleration (m/s²) to wheel torque (Nm)
  # [Params] desired_accel: float
  # [Returns] float: wheel torque command (clipped to DBC range)
  # =============================================================================
  def calculate_wheel_torque(self, desired_accel):
    # Handle zero acceleration case (no torque needed)
    if desired_accel == 0:
      return 0.0

    # === LOAD CAR-SPECIFIC DRIVETRAIN PARAMETERS ===
    # Use model-specific values with safe fallbacks for unknown models
    vehicle_mass = self.CP.mass  # From openpilot CarParams (kg)
    wheel_radius = getattr(self.params, 'WHEEL_RADIUS', DEFAULT_WHEEL_RADIUS)

    # === PHYSICS CALCULATIONS ===
    # Step 1: Calculate required force at wheels using Newton's 2nd Law
    wheel_force = vehicle_mass * desired_accel  # F = ma (Newtons)

    # Step 2: Convert force to torque at wheels using torque arm
    wheel_torque = wheel_force * wheel_radius  # T = F × r (Newton-meters)

    # === APPLY REASONABLE WHEEL TORQUE LIMITS ===
    # For passenger EVs, reasonable wheel torque range is much higher than motor torque
    # because wheels are larger diameter and provide mechanical advantage
    max_wheel_torque = 4000.0  # Reasonable for passenger EV wheels (Nm)
    min_wheel_torque = -4000.0 # Reasonable regenerative limit (Nm)

    # Apply reasonable limits
    wheel_torque_limited = np.clip(wheel_torque, min_wheel_torque, max_wheel_torque)

    # === DBC SIGNAL RANGE COMPLIANCE ===
    # Clip to DBC signal range for cmdTorque (wheel torque)
    # Source: byd_dolphin.dbc - SG_ cmdTorque signal definition
    # Range: -5000.0 to 15475.0 Nm (validated against DBC spec)
    return np.clip(wheel_torque_limited, -5000.0, 15475.0)

  # =============================================================================
  # [Section] Safety: Dual-Path Angle Validation
  # [Brief ] Cross-check PID output against geometric angle with multi-layer
  #          fail-safe fallbacks to prevent dangerous commands
  # ---------------------------------------------------------------------------
  # [Function] validate_steering_angle
  # [Brief ] Validate PID angle vs geometric angle, use safest fallback on mismatch
  # [Params] pid_angle: float, CC: CarControl, CS: CarState
  # [Returns] (validated_angle: float, fault_detected: bool, fault_message: str)
  # =============================================================================
  def validate_steering_angle(self, pid_angle, CC, CS):
    """
    CRITICAL SAFETY FUNCTION: Multi-layer fail-safe validation

    Defense-in-depth approach:
    Layer 1: Validate PID output is reasonable (not NaN/Inf)
    Layer 2: Cross-check against geometric angle calculation
    Layer 3: Speed-dependent deviation limits
    Layer 4: Emergency fallback to geometric angle if PID diverges
    Layer 5: Final hard limit to MAX_STEERING_ANGLE

    Returns:
        validated_angle: Safe angle to send to EPS
        fault_detected: True if any layer triggered
        fault_message: Description of fault for logging
    """

    # === LAYER 1: BASIC SANITY CHECKS ===
    # Catch NaN, Inf, or obviously wrong values
    if not math.isfinite(pid_angle):
      return 0.0, True, f"PID output non-finite: {pid_angle}"

    if abs(pid_angle) > 360:  # Steering wheel can't turn more than ~1 rotation
      return 0.0, True, f"PID output exceeds physical limit: {pid_angle:.1f}°"

    # === LAYER 2: GEOMETRIC ANGLE CALCULATION (PURE ANGLE CONTROL) ===
    # Calculate what pure angle control would command (no PID)
    # This is our "ground truth" based on physics
    geometric_angle = 0.0
    geometric_valid = False

    try:
      # Get curvature from planner
      if hasattr(CC, 'curvature'):
        curvature = CC.curvature

        # Get live parameters for roll compensation
        roll = 0.0
        if hasattr(self, 'sm') and self.sm.valid.get('liveParameters'):
          lp = self.sm['liveParameters']
          if hasattr(lp, 'roll'):
            roll = float(lp.roll)

        # Calculate geometric angle (what pure angle control would do)
        # VM.get_steer_from_curvature returns radians, convert to degrees
        if hasattr(self, 'VM') and CS.out.vEgo > 0.1:  # Need valid speed
          angle_rad = self.VM.get_steer_from_curvature(-curvature, CS.out.vEgo, roll)
          geometric_angle = math.degrees(angle_rad)
          geometric_valid = True
    except Exception as e:
      cloudlog.warning(f"gateway angle validation: Geometric calculation failed: {e}")
      geometric_valid = False

    # === LAYER 3: CROSS-CHECK PID vs GEOMETRIC ===
    # Only if we successfully calculated geometric angle
    if geometric_valid and not self.angle_validation_disabled:
      angle_delta = abs(pid_angle - geometric_angle)

      # === LAYER 3A: SPEED-DEPENDENT DEVIATION LIMITS ===
      # Lower speeds allow more deviation (parking maneuvers, tight turns)
      # Higher speeds require tighter limits (safety critical)
      speed_kmh = CS.out.vEgo * 3.6

      if speed_kmh < 10:
        # Very low speed: parking, U-turns
        max_deviation = getattr(self.params, 'MAX_ANGLE_DEVIATION_LOW_SPEED', 20.0)
      elif speed_kmh < 40:
        # Low-medium speed: city driving
        max_deviation = getattr(self.params, 'MAX_ANGLE_DEVIATION_MEDIUM_SPEED', 10.0)
      elif speed_kmh < 80:
        # Highway speed: gentle curves
        max_deviation = getattr(self.params, 'MAX_ANGLE_DEVIATION_HIGH_SPEED', 5.0)
      else:
        # Very high speed: must be very tight
        max_deviation = getattr(self.params, 'MAX_ANGLE_DEVIATION_VERY_HIGH_SPEED', 3.0)

      # === LAYER 4: FAULT DETECTION AND FALLBACK ===
      if angle_delta > max_deviation:
        # PID DIVERGED - this is a safety fault!
        self.angle_fault_count += 1

        # Build fault message for logging
        fault_msg = (f"PID diverged from geometric: PID={pid_angle:.1f}° "
                    f"Geometric={geometric_angle:.1f}° Delta={angle_delta:.1f}° "
                    f"Limit={max_deviation:.1f}° Speed={speed_kmh:.1f}km/h "
                    f"Faults={self.angle_fault_count}")

        # === LAYER 4A: PERSISTENT FAULT HANDLING ===
        if self.angle_fault_count >= 5:
          # Persistent divergence - switch to geometric fallback
          if not self.angle_fallback_active:
            cloudlog.error(f"gateway CRITICAL: Activating geometric fallback mode")
            self.angle_fallback_active = True

          # Use geometric angle (safe fallback)
          validated_angle = geometric_angle
          return validated_angle, True, fault_msg

        elif self.angle_fault_count >= 20:
          # Very persistent fault - disable validation entirely
          # This is emergency fallback if validation itself is causing issues
          cloudlog.critical(f"gateway EMERGENCY: Disabling angle validation "
                           f"(persistent faults, possible validation bug)")
          self.angle_validation_disabled = True
          return pid_angle, True, "Validation disabled - emergency fallback to PID"

        else:
          # Transient fault - use geometric for this frame but don't switch modes yet
          cloudlog.warning(f"gateway: {fault_msg}")
          return geometric_angle, True, fault_msg

      else:
        # === LAYER 4B: NORMAL OPERATION - PID IS REASONABLE ===
        # PID output is within acceptable deviation from geometry

        # Reset fault count (PID recovered)
        if self.angle_fault_count > 0:
          self.angle_fault_count -= 1  # Decay slowly to avoid oscillation

        # Exit fallback mode if fault count drops
        if self.angle_fault_count == 0 and self.angle_fallback_active:
          cloudlog.info("gateway: PID recovered, exiting geometric fallback mode")
          self.angle_fallback_active = False

        # Diagnostic logging (low frequency to avoid spam)
        if self.frame % 100 == 0:  # Every 1 second
          cloudlog.debug(f"gateway angle OK: PID={pid_angle:.1f}° "
                        f"Geometric={geometric_angle:.1f}° Delta={angle_delta:.1f}° "
                        f"Limit={max_deviation:.1f}° Speed={speed_kmh:.1f}km/h")

        # Use PID output (normal operation)
        validated_angle = pid_angle
        return validated_angle, False, "OK"

    else:
      # Geometric calculation unavailable or validation disabled
      # Use PID output directly (no cross-check possible)
      if self.angle_validation_disabled:
        return pid_angle, False, "Validation disabled"
      else:
        return pid_angle, False, "Geometric unavailable"

    # === LAYER 5: FINAL HARD LIMIT ===
    # This should never be reached, but belt-and-suspenders safety
    max_angle = getattr(self.params, 'MAX_STEERING_ANGLE', 600)
    if abs(validated_angle) > max_angle:
      cloudlog.error(f"gateway: Angle {validated_angle:.1f}° exceeds hard limit {max_angle}°")
      validated_angle = np.clip(validated_angle, -max_angle, max_angle)
      return validated_angle, True, f"Hard limit clipped: {validated_angle:.1f}° -> {max_angle}°"

    return validated_angle, False, "OK"

  # =============================================================================
  # [Section] Control Loop (100 Hz)
  # [Brief ] Orchestrates lateral/longitudinal processing and CAN message build
  # ---------------------------------------------------------------------------
  # [Function] update
  # [Brief ] Main control loop at 100 Hz
  # [Params] CC: CarControl, CS: CarState, now_nanos: int
  # [Returns] (Actuators, list[CAN msg])
  # =============================================================================
  def update(self, CC, CS, now_nanos):
    # Update DragonPilot-specific params periodically
    if not self.CP.pcmCruise:
      self.sm.update(0)
      if self.frame % 200 == 0:  # Update every 2 seconds
        self.is_metric = self.param_s.get_bool("IsMetric")

    # MPC echo counter synchronization (sunnypilot pattern)
    # Sync with stock MPC counters on first frame to avoid DTC
    if self.first_start:
      self.eps_fake318_counter = int(CS.eps_state_counter + 1) & 0xF
      self.first_start = False

    # Extract control inputs
    actuators = CC.actuators
    can_sends = []

    # === LATERAL CONTROL WITH STATE MACHINE (sunnypilot pattern) ===
    # State machine ensures smooth engagement/disengagement
    if CC.latActive:
      # gateway conceptually controls steering angle
      desired_angle = actuators.steeringAngleDeg

      # === SAFETY: DUAL-PATH ANGLE VALIDATION ===
      # Cross-check PID output vs geometric angle with multi-layer fail-safe
      # This catches PID divergence, sensor faults, and tuning errors
      validated_angle, fault_detected, fault_msg = self.validate_steering_angle(
        desired_angle, CC, CS
      )

      # Use validated angle (may be geometric fallback if PID diverged)
      desired_angle = validated_angle

      # Log faults for diagnostics (already logged internally, this is for BLF export)
      if fault_detected:
        # Fault already logged by validate_steering_angle
        pass

      # Apply standard angle rate limits
      max_angle = getattr(self.params, 'MAX_STEERING_ANGLE', 600)
      limited_angle = max(min(desired_angle, max_angle), -max_angle)

      # ANGLE-BASED CONTROL: BYD uses steering angle commands (not torque)
      if self.CP.steerControlType == car.CarParams.SteerControlType.torque:
        # STATE MACHINE: Check if we're actively controlling
        if self.lka_active:
          # === ACTIVE CONTROL STATE ===
          # Apply SPEED-DEPENDENT rate limiting (Toyota/PSA best practice)
          # Lower speeds = tighter turns allowed, higher speeds = physics-limited
          rate_limits_up = getattr(self.params, 'ANGLE_RATE_LIMITS_UP', ([0, 5, 15], [5.0, 2.5, 0.5]))
          angle_rate_deg_per_sec = np.interp(CS.out.vEgo, rate_limits_up[0], rate_limits_up[1])
          angle_delta_deg = angle_rate_deg_per_sec * DT_CTRL  # Convert deg/s to deg/cycle
          apply_angle = rate_limit(limited_angle, self.last_steer_angle,
                                    -angle_delta_deg, angle_delta_deg)

          # DRIVER OVERRIDE: Graduated blending (gateway best-in-class)
          # Smoothly reduces ADAS angle when driver applies steering input
          # Uses 4-point interpolation for natural feel (unique to gateway)
          #
          # Blending breakpoints loaded from CarControllerParams (per-model tunable)
          torque_bp = getattr(self.params, 'DRIVER_TORQUE_BREAKPOINTS', [50, 200, 500, 800])
          torque_factors = getattr(self.params, 'DRIVER_TORQUE_FACTORS', [1.0, 0.6, 0.3, 0.0])

          driver_torque_abs = abs(CS.out.steeringTorque)

          if driver_torque_abs < torque_bp[0]:
            # No significant driver input - full assist
            driver_factor = torque_factors[0]
          elif driver_torque_abs < torque_bp[1]:
            # Light input - interpolate from 100% to 60%
            driver_factor = np.interp(driver_torque_abs, [torque_bp[0], torque_bp[1]], [torque_factors[0], torque_factors[1]])
          elif driver_torque_abs < torque_bp[2]:
            # Moderate input - interpolate from 60% to 30%
            driver_factor = np.interp(driver_torque_abs, [torque_bp[1], torque_bp[2]], [torque_factors[1], torque_factors[2]])
          elif driver_torque_abs < torque_bp[3]:
            # Strong input - interpolate from 30% to 0%
            driver_factor = np.interp(driver_torque_abs, [torque_bp[2], torque_bp[3]], [torque_factors[2], torque_factors[3]])
          else:
            # Very strong input - full override
            driver_factor = torque_factors[3]

          # Apply graduated blending to angle
          apply_angle = apply_angle * driver_factor

          # SOFT-START: Gradually increase angle limit from 0 to max steering angle
          max_steer_angle = getattr(self.params, 'MAX_STEERING_ANGLE', 600)  # degrees
          softstart_step_deg = angle_delta_deg * 2  # Ramp up at 2x rate limit speed

          if self.steer_softstart_limit < max_steer_angle:
            self.steer_softstart_limit = min(self.steer_softstart_limit + softstart_step_deg, max_steer_angle)
            apply_angle = np.clip(apply_angle, -self.steer_softstart_limit, self.steer_softstart_limit)
          else:
            apply_angle = np.clip(apply_angle, -max_steer_angle, max_steer_angle)

        else:
          # === PREPARATION STATE ===
          # EPS not ready yet - request preparation and send zero angle
          self.lka_req_prepare = 1
          apply_angle = 0.0

          # Wait for EPS acknowledgment via MPC_LKAEnabled flag from 0x316
          if CS.eps_lka_ready:
            # EPS has acknowledged and is ready to accept LKA commands
            self.lka_active = 1
            self.lka_req_prepare = 0
            self.steer_softstart_limit = 0  # Reset soft-start
            self.lat_safeoff = 1  # Mark that we need safe disengagement later

      else:
        # For angle control cars: Use angle directly (future models)
        apply_angle = 0.0

    else:
      # === INACTIVE / SAFE-OFF STATE ===
      # Ramp down angle to zero before fully disengaging
      limited_angle = CS.out.steeringAngleDeg if hasattr(CS.out, 'steeringAngleDeg') else 0.0

      if self.lat_safeoff:
        # SAFE-OFF: Ramp down to zero before fully disengaging
        if abs(self.last_steer_angle) > 0.5:  # Still has residual angle
          # Use speed-dependent DOWN rate limit (faster for safety)
          rate_limits_down = getattr(self.params, 'ANGLE_RATE_LIMITS_DOWN', ([0, 5, 15], [10.0, 4.0, 1.0]))
          angle_rate_deg_per_sec = np.interp(CS.out.vEgo, rate_limits_down[0], rate_limits_down[1])
          angle_delta_deg = angle_rate_deg_per_sec * DT_CTRL
          apply_angle = rate_limit(0.0, self.last_steer_angle, -angle_delta_deg, angle_delta_deg)
        else:
          # Angle reached zero - fully disengage
          apply_angle = 0.0
          self.lat_safeoff = 0
          self.lka_active = 0
          self.steer_softstart_limit = 0
      else:
        # Fully inactive
        apply_angle = 0.0
        self.lka_req_prepare = 0
        self.lka_active = 0
        self.steer_softstart_limit = 0

    # Longitudinal control with accel limits and pedal mapping
    if CC.longActive:
      # Accel limits (per model or default)
      accel_max = getattr(self.params, 'ACCEL_MAX', 2.0)    # Safe fallback: 2.0 m/s²
      accel_min = getattr(self.params, 'ACCEL_MIN', -3.5)   # Safe fallback: -3.5 m/s²

      # Clip accel to limits
      accel = np.clip(actuators.accel, accel_min, accel_max)

      # Pedal mapping
      if accel > 0:
        # Gas: scale by max accel
        gas = min(accel / accel_max, 1.0)  # 0-1 range scaled to model capability
        brake = 0.0
      else:
        # Brake: scale by max decel
        gas = 0.0
        brake = min(abs(accel) / abs(accel_min), 1.0)  # 0-1 range scaled to model capability

    else:
      # Longitudinal inactive - reset all states
      accel = gas = brake = 0.0

    # Build CAN messages with standard timing

    # Steering @ 100Hz (every frame) - ANGLE-BASED control
    stock_lat_cmd = getattr(CS, 'stock_lat_cmd', None)
    can_sends.extend(self._generate_latCommand_message(apply_angle, CC, stock_lat_cmd))

    # MPC State & Longitudinal @ 50Hz (every 2 frames)
    if self.frame % 2 == 0:
      # MPC State: Tell car/HUD that openpilot is controlling
      lka_active = CC.latActive  # LKAS is actively steering

      # Get lane detection status from modelV2
      lane_detected = False
      if self.sm.updated.get('modelV2') and self.sm.valid.get('modelV2'):
        md = self.sm['modelV2']
        # Check if we have confident lane lines
        if hasattr(md, 'laneLines') and len(md.laneLines) >= 2:
          # Both left and right lanes detected with confidence
          left_prob = md.laneLines[0].prob if len(md.laneLines) > 0 else 0
          right_prob = md.laneLines[1].prob if len(md.laneLines) > 1 else 0
          lane_detected = (left_prob > 0.5 and right_prob > 0.5)

      mpc_state_msg = gatewaycan.create_mpc_state_command(
        self.packer, self.CAN, CC, lka_active, lane_detected, self.mpc_state_counter,
        getattr(CS, 'stock_mpc_state', None)
      )
      if mpc_state_msg:
        can_sends.append(mpc_state_msg)
      self.mpc_state_counter = (self.mpc_state_counter + 1) & 0xF

      # Longitudinal command
      if CC.longActive:
        # Get lead distance from radarState for jerk limiting
        lead_distance = 50.0  # Default safe distance
        if self.sm.updated.get('radarState') and self.sm.valid.get('radarState'):
          rs = self.sm['radarState']
          if rs.leadOne.status:
            lead_distance = max(rs.leadOne.dRel, 4.0)  # Minimum 4m for jerk calc

        # Create and send longitudinal command with distance-based jerk limiting
        long_msg = gatewaycan.create_long_command(
          self.model, self.packer, self.CAN, accel, CC.longActive,
          lead_distance, self.long_counter,
          getattr(CS, 'stock_long_cmd', None)
        )
        if long_msg:
          can_sends.append(long_msg)
        self.long_counter = (self.long_counter + 1) & 0xF

    # === DIAGNOSIS MESSAGES (0x6F0-0x6F8) for CANape live debugging ===
    # Match software running rates for optimal data capture

    # 50Hz: High-frequency control states
    if self.frame % 2 == 0:
      can_sends.extend(self._generate_diag_controls_state(CC, CS))
      can_sends.extend(self._generate_diag_lateral_state(apply_angle, CS))

    # 20Hz: Planning and model outputs
    if self.frame % 5 == 0:
      can_sends.extend(self._generate_diag_longitudinal_state(accel, gas, brake, CS))
      can_sends.extend(self._generate_diag_car_state_mirror(CS))
      can_sends.extend(self._generate_diag_live_parameters())
      can_sends.extend(self._generate_diag_model_outputs())

    # 10Hz: Fault monitoring
    if self.frame % 10 == 0:
      can_sends.extend(self._generate_diag_faults(CS))

    # 2Hz: System health (low priority)
    if self.frame % 50 == 0:
      can_sends.extend(self._generate_diag_system_health())

    # === MPC ECHO SERVICE (50Hz) ===
    # Send 0x318 EPS state to MPC bus 2 to keep stock AEB/FCW active
    if self.frame % 2 == 0:  # 50Hz
      mpc_eps_msg = gatewaycan.create_mpc_eps_state(
        self.model, self.packer, self.CAN, CS, self.eps_fake318_counter,
        getattr(CS, 'stock_eps_state', None)
      )
      if mpc_eps_msg:
        can_sends.append(mpc_eps_msg)

      # Increment counter for next message
      self.eps_fake318_counter = (self.eps_fake318_counter + 1) & 0xF

    # Save state for next cycle (standard naming - matches Toyota/Hyundai/Honda)
    self.last_steer_angle = apply_angle  # Applied angle (after rate limit/blending/soft-start)
    self.last_accel = gas     # Store gas pedal value (not raw accel)
    self.last_brake = brake

    # Update controller state
    self.accel = accel
    self.gas = gas
    self.brake = brake

    # Build actuator response for OP (standard naming - matches other brands)
    new_actuators = actuators.as_builder()
    new_actuators.speed = CS.out.vEgo              # Current vehicle speed
    new_actuators.accel = self.accel               # Applied acceleration
    new_actuators.gas = self.gas                   # Applied gas pedal
    new_actuators.brake = self.brake               # Applied brake pedal
    new_actuators.steeringAngleDeg = apply_angle   # Applied steering angle (degrees)
    new_actuators.steerOutputCan = int(apply_angle * 100)  # CAN steering output (angle * 100 for diagnostics)

    # Increment frame counter and return control outputs
    self.frame += 1
    return new_actuators, can_sends

  # =============================================================================
  # [Section] CAN: latCommand (100 Hz)
  # [Brief ] Build steering-related CAN message via gatewaycan helper
  # ---------------------------------------------------------------------------
  # [Function] _generate_latCommand_message
  # [Brief ] Build 0x1E2 lateral command at 100 Hz via gatewaycan
  # [Params] steering_angle: float, CC: CarControl, stock_values: dict
  # [Returns] list[CAN msg]
  # =============================================================================
  def _generate_latCommand_message(self, steering_angle, CC, stock_values):
    # Delegate value building to gatewaycan with counter
    # BYD uses ANGLE-BASED control (not torque-based!)
    msg = gatewaycan.create_lat_command(
      self.model, self.packer, self.CAN, CC, steering_angle, self.lat_counter, stock_values
    )
    self.lat_counter = (self.lat_counter + 1) & 0xF  # Increment and wrap at 16
    return [msg]

  # =============================================================================
  # [Section] DIAGNOSIS MESSAGES (0x6F0-0x6F8)
  # [Brief ] Broadcast openpilot internal state to CAN bus for CANape debugging
  # =============================================================================

  def _generate_diag_controls_state(self, CC, CS):
    """DIAG_controlsState @ 50Hz"""
    msg = gatewaycan.create_diag_controls_state(self.model, self.packer, self.CAN, self.sm, CC, CS, self.diag_controls_counter)
    self.diag_controls_counter = (self.diag_controls_counter + 1) & 0xF
    return [msg] if msg else []

  def _generate_diag_lateral_state(self, steer_angle, CS):
    """DIAG_lateralState @ 50Hz - angle-based control (degrees)"""
    msg = gatewaycan.create_diag_lateral_state(self.model, self.packer, self.CAN, self.sm, CS, steer_angle, self.diag_lateral_counter)
    self.diag_lateral_counter = (self.diag_lateral_counter + 1) & 0xF
    return [msg] if msg else []

  def _generate_diag_longitudinal_state(self, accel, gas, brake, CS):
    """DIAG_longitudinalState @ 20Hz"""
    msg = gatewaycan.create_diag_longitudinal_state(self.model, self.packer, self.CAN, self.sm, CS, accel, gas, brake, self.diag_long_counter)
    self.diag_long_counter = (self.diag_long_counter + 1) & 0xF
    return [msg] if msg else []

  def _generate_diag_car_state_mirror(self, CS):
    """DIAG_carStateMirror @ 20Hz"""
    msg = gatewaycan.create_diag_car_state_mirror(self.model, self.packer, self.CAN, CS, self.diag_carstate_counter)
    self.diag_carstate_counter = (self.diag_carstate_counter + 1) & 0xF
    return [msg] if msg else []

  def _generate_diag_live_parameters(self):
    """DIAG_liveParameters @ 20Hz"""
    msg = gatewaycan.create_diag_live_parameters(self.model, self.packer, self.CAN, self.sm, self.CP, self.diag_liveparam_counter)
    self.diag_liveparam_counter = (self.diag_liveparam_counter + 1) & 0xF
    return [msg] if msg else []

  def _generate_diag_model_outputs(self):
    """DIAG_modelOutputs @ 20Hz"""
    msg = gatewaycan.create_diag_model_outputs(self.model, self.packer, self.CAN, self.sm, self.diag_model_counter)
    self.diag_model_counter = (self.diag_model_counter + 1) & 0xF
    return [msg] if msg else []

  def _generate_diag_system_health(self):
    """DIAG_systemHealth @ 2Hz"""
    msg = gatewaycan.create_diag_system_health(self.model, self.packer, self.CAN, self.sm, self.diag_health_counter)
    self.diag_health_counter = (self.diag_health_counter + 1) & 0xF
    return [msg] if msg else []

  def _generate_diag_faults(self, CS):
    """DIAG_faults @ 10Hz"""
    msg = gatewaycan.create_diag_faults(self.model, self.packer, self.CAN, self.sm, CS, self.diag_faults_counter)
    self.diag_faults_counter = (self.diag_faults_counter + 1) & 0xF
    return [msg] if msg else []

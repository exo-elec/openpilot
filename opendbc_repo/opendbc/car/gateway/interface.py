# OpenPilot framework imports
"""
Gateway car interface
=====================

Glue between openpilot core and the gateway car port.  This file answers:
- What parameters describe a gateway vehicle?
- Which CAN fingerprint belongs to which model?
- How should BYD-specific tuning be applied?

File layout:
1. Imports and type aliases
2. CarInterface class with clearly marked helper sections
3. Model auto-detection utilities
"""

from cereal import car
from opendbc.car import get_safety_config, scale_tire_stiffness
from opendbc.car.gateway.carcontroller import CarController
from opendbc.car.gateway.carstate import CarState
from opendbc.car.gateway.values import CAR, GatewayFlags, CarControllerParams
from opendbc.car.interfaces import CarInterfaceBase
from opendbc.car.common.conversions import Conversions as CV

# Framework type aliases
SteerControlType = car.CarParams.SteerControlType


# -----------------------------------------------------------------------------
# Gateway CarInterface
# -----------------------------------------------------------------------------
class CarInterface(CarInterfaceBase):
  CarState = CarState
  CarController = CarController

  @staticmethod
  # =============================================================================
  # [Section] Accel Limits
  # [Brief ] Provide PID accel bounds for longitudinal controller
  # ---------------------------------------------------------------------------
  # [Function] get_pid_accel_limits
  # [Brief ] Provide PID accel bounds for longitudinal controller
  # [Params] CP: CarParams, current_speed: float, cruise_speed: float
  # [Returns] (min_accel: float, max_accel: float)
  # =============================================================================
  def get_pid_accel_limits(CP, current_speed, cruise_speed):
    return CarControllerParams(CP).ACCEL_MIN, CarControllerParams(CP).ACCEL_MAX

  @staticmethod
  # =============================================================================
  # [Section] Params
  # [Brief ] Populate Gateway CarParams with platform-specific settings
  # ---------------------------------------------------------------------------
  # [Function] _get_params
  # [Brief ] Populate Gateway CarParams with platform-specific settings
  # [Params] ret: CarParams, candidate: CAR, fingerprint: dict, car_fw: list, experimental_long: bool, docs: bool
  # [Returns] CarParams
  # =============================================================================
  @staticmethod
  def _get_params(ret, candidate, fingerprint, car_fw, experimental_long, docs):
    # Basic vehicle identification
    ret.carName = "Gateway"
    ret.brand = "gateway"

    # Safety configuration (hardware-level, following Honda Bosch pattern)
    ret.safetyConfigs = [get_safety_config(car.CarParams.SafetyModel.gateway)]

    # Extract vehicle-specific specs from CAR_SPECS (DragonPilot pattern)
    from opendbc.car.gateway.values import CAR_SPECS
    specs = CAR_SPECS.get(candidate, {})
    
    # Vehicle physical specs using DragonPilot-style dictionary
    ret.mass = specs.get('mass', 1700.)                               # Vehicle curb weight (kg)
    ret.wheelbase = specs.get('wheelbase', 2.72)                      # Distance between axles (m)
    ret.steerRatio = specs.get('steerRatio', 16.0)                    # Steering wheel to wheel angle ratio
    ret.centerToFrontRatio = specs.get('centerToFrontRatio', 0.4)     # CG position (0.5 = center)
    tire_stiffness_factor = specs.get('tireStiffnessFactor', 0.7)     # Tire grip factor
    # BYD-specific speed thresholds (from sunnypilot-pc proven values)
    if candidate in (CAR.BYD_DOLPHIN, CAR.BYD_ATTO3):
      ret.minSteerSpeed = 0.1 * CV.KPH_TO_MS     # Proven BYD value: 0.1 km/h
      ret.minEnableSpeed = -1.0                  # Proven BYD value: no minimum (stop and go)
    else:
      ret.minSteerSpeed = specs.get('minSteerSpeed', 5.0 * CV.KPH_TO_MS) # Min speed for steering
      ret.minEnableSpeed = specs.get('minEnableSpeed', 5.0 * CV.KPH_TO_MS) # Min speed for ADAS

    ret.centerToFront = ret.wheelbase * ret.centerToFrontRatio        # Distance from CG to front axle
    ret.dashcamOnly = False                                           # Full ADAS capability

    # Control type: BYD uses ANGLE control (SteerControlType.torque is legacy naming)
    ret.steerControlType = SteerControlType.torque  # ANGLE-based control (variable name is legacy)

    # Steering actuator delay - BYD-specific proven values from sunnypilot-pc
    if candidate in (CAR.BYD_DOLPHIN, CAR.BYD_ATTO3):
      ret.steerActuatorDelay = 0.05   # Proven BYD EV response time (from sunnypilot-pc)
      ret.steerLimitTimer = 0.4       # BYD-specific timing
    else:
      # Fallback for unknown models
      if ret.flags & GatewayFlags.EV:
        ret.steerActuatorDelay = 0.08  # Generic EV
      elif ret.flags & GatewayFlags.HYBRID:
        ret.steerActuatorDelay = 0.12  # Generic hybrid
      else:
        ret.steerActuatorDelay = 0.15  # Generic ICE
      ret.steerLimitTimer = 0.4        # Standard automotive steering limit timer

    # Longitudinal control (camera-based)
    ret.openpilotLongitudinalControl = True                                 # Use openpilot for speed control
    ret.stoppingControl = bool(ret.flags & GatewayFlags.STOP_AND_GO)     # Can stop/start automatically
    ret.startingState = bool(ret.flags & GatewayFlags.STOP_AND_GO)       # Automatic starting capability
    ret.radarUnavailable = True                                             # Camera-only system

    # BYD-specific longitudinal parameters (from sunnypilot-pc proven values)
    if candidate in (CAR.BYD_DOLPHIN, CAR.BYD_ATTO3):
      ret.autoResumeSng = True                  # BYD supports stop and go
      ret.startingState = True                  # BYD can start from stop
      ret.startAccel = 0.8                     # BYD starting acceleration
      ret.stopAccel = -0.5                     # BYD stopping deceleration
      ret.vEgoStarting = 0.2 * CV.KPH_TO_MS    # BYD starting speed threshold
      ret.vEgoStopping = 0.1 * CV.KPH_TO_MS    # BYD stopping speed threshold
      ret.longitudinalActuatorDelay = 0.5      # BYD longitudinal delay

    # Low-speed behavior by powertrain
    if ret.flags & GatewayFlags.EV:
      ret.vEgoStopping = 0.3     # EVs can control precisely at low speeds
      ret.vEgoStarting = 0.3     # Smooth EV acceleration from stop
      ret.stopAccel = -2.5       # Strong regenerative braking capability
    else:
      ret.vEgoStopping = 0.5     # ICE less precise at very low speeds
      ret.vEgoStarting = 0.5     # Standard ICE starting threshold
      ret.stopAccel = -2.0       # Standard friction braking

    ret.stoppingDecelRate = 0.8    # Deceleration rate when approaching stop
    ret.maxSteeringAngleDeg = 1080 # Maximum steering wheel angle (3 turns)

    # Longitudinal PID tuning (per model)
    if candidate in (CAR.BYD_DOLPHIN, CAR.BYD_ATTO3):
      # BYD DOLPHIN: City-focused compact EV with smooth, comfortable tuning
      ret.longitudinalTuning.kf = 1.1                                     # Balanced feedforward
      ret.longitudinalTuning.kpBP = [0., 6., 15., 30.]                    # City-optimized breakpoints
      ret.longitudinalTuning.kpV = [1.8, 1.2, 0.8, 0.5]                  # Smooth responsive gains
      ret.longitudinalTuning.kiBP = [0., 6., 15., 30.]                    # Matching integral points
      ret.longitudinalTuning.kiV = [0.24, 0.18, 0.12, 0.08]              # Moderate integral gains
      ret.longitudinalActuatorDelayLowerBound = 0.12                      # Comfortable response
      ret.longitudinalActuatorDelayUpperBound = 0.22
    else:
      # Conservative fallback tuning for unknown vehicles
      ret.longitudinalTuning.kf = 1.0                                     # Safe feedforward
      ret.longitudinalTuning.kpBP = [0., 5., 35.]                         # Simple breakpoints
      ret.longitudinalTuning.kpV = [1.2, 0.8, 0.5]                       # Conservative gains
      ret.longitudinalTuning.kiBP = [0., 35.]                             # Basic integral points
      ret.longitudinalTuning.kiV = [0.18, 0.12]                          # Safe integral gains
      ret.longitudinalActuatorDelayLowerBound = 0.20                      # Conservative delays
      ret.longitudinalActuatorDelayUpperBound = 0.30

    # =============================================================================
    # IMPORTANT: CONFUSING NAMING CLARIFICATION
    # =============================================================================
    # BYD uses ANGLE-BASED control (NOT torque-based like Toyota/Honda)
    # BUT openpilot calls it "torque controller" for historical reasons
    #
    # WHAT THE NAMES ACTUALLY MEAN FOR BYD:
    # - SteerControlType.torque = "use the torque controller algorithm" (NOT torque output!)
    # - lateralTuning.torque.kp = angle response gain (NOT motor torque gain!)
    # - lateralParams.torqueV = max steering correction effort (NOT motor torque Nm!)
    # - Final output = steering ANGLE in degrees (±12.7°), NOT motor torque
    #
    # WHY THIS CONFUSION EXISTS:
    # - Openpilot was designed for Toyota/Honda (true torque-based control)
    # - When angle-based cars were added, the controller was reused
    # - Parameter names stayed the same but meaning changed
    # - We can't rename upstream openpilot structures
    #
    # REMEMBER: For BYD, "torqueV" = "max angle correction aggressiveness"
    # =============================================================================

    # Model-specific lateral control tuning for optimal steering feel
    if ret.steerControlType == SteerControlType.torque:
      ret.lateralTuning.init('torque')
      ret.lateralTuning.torque.useSteeringAngle = True
      ret.lateralTuning.torque.kf = 1.0

      # LATERAL GAIN TUNING - FIXED: Reduced MEDIUM-speed aggression for stability
      # User issue: Excessive steering / oscillation on gentle curves toward barriers at 40-60 km/h
      # BLF analysis showed: Problem at MEDIUM speeds, high driver intervention (3000-5000 Nm)
      # Changes: Reduced kp 2.4→1.6, increased ki 0.20→0.22, deadzone 0°→0.2°, reduced MEDIUM-speed limits
      if candidate in (CAR.BYD_DOLPHIN, CAR.BYD_ATTO3):
        # ANGLE RESPONSE GAINS (misleading names - these control angle, NOT torque!)
        ret.lateralTuning.torque.kp = 1.6  # Angle response gain (was 2.4 - too aggressive)
        ret.lateralTuning.torque.ki = 0.22  # Integral gain (was 0.20 - INCREASED to reduce steady-state error)
        ret.lateralTuning.torque.friction = 0.015  # Friction compensation
        ret.lateralTuning.torque.steeringAngleDeadzoneDeg = 0.2  # Deadzone to prevent oscillation (was 0.1°)

        # MAX STEERING CORRECTION LIMITS (speed-dependent)
        # Confusing name: "torqueV" actually means "max angle correction aggressiveness"
        # BLF data: Worst over-steering at 40-60 km/h (12-22 m/s range)
        ret.lateralParams.torqueBP = [0., 5., 12., 22., 35.]  # Speed breakpoints (m/s)
        ret.lateralParams.torqueV = [0., 70., 100., 180., 350.]  # Max correction effort (NOT Nm!)
        # FIXES APPLIED BASED ON BLF ANALYSIS:
        # At 12 m/s (43 km/h): 140→100 (29% reduction - fixes medium-speed over-correction)
        # At 22 m/s (79 km/h): 250→180 (28% reduction - fixes highway entry oscillation)
        # At 35 m/s (126 km/h): 350 unchanged (high-speed tracking was good in BLF data)
      else:
        # Generic EV/ICE defaults
        if ret.flags & GatewayFlags.EV:
          ret.lateralTuning.torque.kp = 1.2
          ret.lateralTuning.torque.ki = 0.12
          ret.lateralTuning.torque.friction = 0.08
          ret.lateralParams.torqueBP = [0., 8., 15., 25., 40.]
          ret.lateralParams.torqueV = [0., 60., 120., 250., 400.]
        else:
          ret.lateralTuning.torque.kp = 1.0
          ret.lateralTuning.torque.ki = 0.1
          ret.lateralTuning.torque.friction = 0.1
          ret.lateralParams.torqueBP = [0., 5., 10., 20., 30.]
          ret.lateralParams.torqueV = [0., 50., 100., 200., 300.]

    else:
      ret.lateralTuning.init('pid')
      ret.lateralTuning.pid.kf = 0.00008
      ret.lateralTuning.pid.kdBP = [0.]
      ret.lateralTuning.pid.kdV = [0.]

      # PID (angle) tuning (per model)
      if candidate in (CAR.BYD_DOLPHIN, CAR.BYD_ATTO3):
        ret.lateralTuning.pid.kpBP = [0., 9., 20.]
        ret.lateralTuning.pid.kpV = [0.22, 0.38, 0.55]
        ret.lateralTuning.pid.kiBP = [0., 9., 20.]
        ret.lateralTuning.pid.kiV = [0.012, 0.022, 0.032]
      else:
        if ret.flags & GatewayFlags.EV:
          ret.lateralTuning.pid.kpBP = [0., 9., 20.]
          ret.lateralTuning.pid.kpV = [0.2, 0.35, 0.5]
          ret.lateralTuning.pid.kiBP = [0., 9., 20.]
          ret.lateralTuning.pid.kiV = [0.01, 0.02, 0.03]
        else:
          ret.lateralTuning.pid.kpBP = [0., 9., 20.]
          ret.lateralTuning.pid.kpV = [0.15, 0.25, 0.4]
          ret.lateralTuning.pid.kiBP = [0., 9., 20.]
          ret.lateralTuning.pid.kiV = [0.008, 0.015, 0.025]

    # -----------------------------------------------------------------------------
    # [Section] ADAS Feature Configuration
    # [Brief ] Set explicit feature flags and capabilities. Keep minimal; tuning
    #          logic is centralized in values/CarControllerParams.
    # -----------------------------------------------------------------------------
    ret.autoResumeSng = bool(ret.flags & GatewayFlags.STOP_AND_GO)
    ret.enableBsm = bool(ret.flags & GatewayFlags.FOUR_WHEEL_SENSORS)
    ret.enableApgs = False
    ret.enableDsu = False
    ret.enableGasInterceptor = False
    ret.experimentalLongitudinalAvailable = ret.openpilotLongitudinalControl
    ret.pcmCruise = False

    # -----------------------------------------------------------------------------
    # [Section] Camera-based ADAS
    # [Brief ] Inform openpilot that the platform uses a stock camera system.
    # -----------------------------------------------------------------------------
    ret.hasStockCamera = True

    # -----------------------------------------------------------------------------
    # [Section] Safety Parameter
    # [Brief ] Base safety parameter (0..65535). Gateway uses base value.
    # -----------------------------------------------------------------------------
    ret.safetyParam = 0
    assert 0 <= ret.safetyParam <= 65535, f"Invalid safetyParam: {ret.safetyParam}"

    # -----------------------------------------------------------------------------
    # [Section] DragonPilot Integration
    # [Brief ] Integrate with DragonPilot's lateral tuning system
    # -----------------------------------------------------------------------------
    # Apply tire stiffness factor
    ret = scale_tire_stiffness(ret, tire_stiffness_factor)
    
    # DragonPilot lateral tune collection and configuration
    CarInterfaceBase.dp_lat_tune_collection(candidate, ret.latTuneCollection)
    CarInterfaceBase.configure_dp_tune(ret.lateralTuning, ret.latTuneCollection)

    return ret

  def _update(self, c):
    return self.CS.update(self.can_parsers)

  def apply(self, c, now_nanos):
    return self.CC.update(c, self.CS, now_nanos)

  @staticmethod
  def auto_detect_car_model(can_data):
    """Automatically detect car model from modelMarker bits during fingerprinting
    
    Args:
        can_data: Dictionary of CAN message data from fingerprinting process
        
    Returns:
        CAR enum or None if detection fails
        
    Usage:
        This method can be called during openpilot's fingerprinting process to
        automatically determine the car model based on modelMarker values instead
        of relying solely on static message presence/absence patterns.
    """
    return CarState.detect_car_model(can_data)
  
  @staticmethod  
  def get_fingerprint_with_auto_detection(fingerprint_data, timeout=10.0):
    """Enhanced fingerprinting that combines static patterns with dynamic detection
    
    Args:
        fingerprint_data: Raw CAN fingerprint data
        timeout: Maximum time to wait for modelMarker detection
        
    Returns:
        (detected_model, confidence) tuple or (None, 0) if detection fails
        
    This method provides a bridge between openpilot's existing fingerprinting
    system and the new automatic bit-level model detection.
    """
    detected_model = None
    confidence = 0
    
    # Extract modelMarker values from fingerprint data
    try:
      # Look for brand-specific marker messages in fingerprint
      # Each brand has its own dedicated message ID
      for msg_id, msg_data in fingerprint_data.items():
        
        if msg_id == 1776:  # 0x6F0 - BYD brand marker
          if 'modelMarker' in msg_data:
            marker_value = msg_data['modelMarker']

            if marker_value == 1:
              detected_model = CAR.BYD_ATTO3
              confidence = 95
            elif marker_value == 2:
              detected_model = CAR.BYD_DOLPHIN
              confidence = 95
            # Future BYD models: marker_value 3, 4, etc.

        # elif msg_id == 1777:  # 0x6F1 - CHANGAN brand marker (commented out)
        #   if 'modelMarker' in msg_data:
        #     marker_value = msg_data['modelMarker']
        #
        #     if marker_value == 1:
        #       detected_model = CAR.DEEPAL_S05  # CHANGAN (DEEPAL sub-brand)
        #       confidence = 95
        #     # Future CHANGAN models: marker_value 2, 3, etc.
              
        elif msg_id == 1778:  # 0x6F2 - MG brand marker
          if 'modelMarker' in msg_data:
            marker_value = msg_data['modelMarker']
            # Future MG model detection based on marker_value
            confidence = 50  # Lower confidence for unknown models
          
        elif msg_id == 1779:  # 0x6F3 - GAC brand marker  
          if 'modelMarker' in msg_data:
            marker_value = msg_data['modelMarker']
            # Future GAC model detection based on marker_value
            confidence = 50  # Lower confidence for unknown models
          
    except (KeyError, TypeError, AttributeError):
      pass  # Fingerprint data format issues
      
    return detected_model, confidence

  # Radarless compatibility helper removed; rely on standard CarParams fields.

"""
Gateway vehicle definitions
===========================

This module centralizes every static definition for the gateway car port so
each expansion (BYD Dolphin, BYD ATTO3, etc.) lives in one clearly-labeled
place.  The layout is intentionally segmented:

1.  File imports & utility enums/dataclasses
2.  Global constants shared across models (fault codes, torque limits, etc.)
3.  Per-model dictionaries (Toyota-style pattern) grouped in one section
4.  Gateway flags & button enums
5.  Platform definitions (CAR enum) + CAR_SPECS metadata
6.  Firmware/fingerprint/DBC helpers used by the rest of the car port

Additions for new models should follow that structure so future maintainers
can glance at the header comments to find the right area quickly.
"""

# ---------------------------------------------------------------------------
# Imports
# ---------------------------------------------------------------------------
from dataclasses import dataclass, field
from enum import IntFlag

from opendbc.car import Bus, CarSpecs, DbcDict, PlatformConfig, Platforms
from opendbc.car.common.conversions import Conversions as CV
from opendbc.car.structs import CarParams
from opendbc.car.docs_definitions import CarDocs, CarParts, CarHarness
from opendbc.car.fw_query_definitions import FwQueryConfig

Ecu = CarParams.Ecu


# ---------------------------------------------------------------------------
# Dataclasses & specs
# ---------------------------------------------------------------------------

@dataclass
class GatewayCarDocs(CarDocs):
  package: str = "All"
  harness: CarHarness = CarHarness.bosch_b


@dataclass(frozen=True)
class GatewayCarSpecs(CarSpecs):
  minSteerSpeed: float = 0
  minEnableSpeed: float = 0


@dataclass
class GatewayPlatformConfig(PlatformConfig):
  # No default DBC - each model must specify its own dbc_dict (Honda/Toyota pattern)
  def init(self):
    pass


# ---------------------------------------------------------------------------
# Gateway-wide numeric constants
# ---------------------------------------------------------------------------

# Speed thresholds
MIN_STEER_SPEED = 3. * CV.KPH_TO_MS     # Min speed for steering assist (3 km/h)
PEDAL_TRANSITION = 5. * CV.KPH_TO_MS    # Gas/brake transition speed (5 km/h)
STEER_THRESHOLD = 100                   # Driver torque threshold (Nm)

# Default drivetrain parameters
DEFAULT_VEHICLE_MASS = 1500.0          # kg
DEFAULT_WHEEL_RADIUS = 0.31            # m (approx 215/60R16)
DEFAULT_FINAL_DRIVE_RATIO = 3.5        # single-speed ratio
DEFAULT_MOTOR_EFFICIENCY = 0.9         # 0..1

# Wheel torque limits (wheel torque)
MAX_WHEEL_TORQUE = 4000.0              # Nm
MIN_WHEEL_TORQUE = -4000.0             # Nm (regen)

# Steering fault codes
TEMP_STEER_FAULTS = (0, 1, 2, 9, 11, 13, 21, 25)
PERM_STEER_FAULTS = (3, 4, 5, 17, 22, 31)

# Road type
ROAD_TYPE_UNKNOWN = 0
ROAD_TYPE_CITY = 1
ROAD_TYPE_HIGHWAY = 2
ROAD_TYPE_COUNTRY = 3

# Traffic light states
TRAFFIC_LIGHT_UNKNOWN = 0
TRAFFIC_LIGHT_RED = 1
TRAFFIC_LIGHT_YELLOW = 2
TRAFFIC_LIGHT_GREEN = 3

# ---------------------------------------------------------------------------
# Toyota-style per-model dictionaries
# ---------------------------------------------------------------------------

from collections import defaultdict

# NOTE: BYD_ATTO3 entries currently mirror BYD_DOLPHIN behaviour until
# dedicated CAN logs are collected.  Replace placeholders with real values
# when model-specific data becomes available.

# Steering Limits (motor counts - NOT Nm!)
# ALL VALUES FROM BLF ANALYSIS (comprehensive_limits_analysis.py)
# Dataset: 8th_LOG (2025-10-15), 6 logs, 3801 command samples
#
# BLF observed: max=8981, p99=7701, p95=5141, mean=1947
# With DBC scale=1.0, raw CAN value = motor counts directly
STEER_MAX = defaultdict(lambda: 10000,  # Conservative default for unknown models
  {
    CAR.BYD_DOLPHIN: 11500,     # BLF max 8981 + 28% margin (MUST match firmware!)
    CAR.BYD_ATTO3: 11500,       # Initial clone of Dolphin limits (TODO: replace with ATTO3 logs)
    # CAR.BYD_ATTO3: 12000,       # TBD from CAN testing
    # CAR.DEEPAL_S05: 11000,      # TBD from CAN testing
  }
)

# Steering Rate Limits (motor counts per 20ms frame @ 50Hz)
# BLF observed: max=1008, p99=256, p95=256, mean=50
STEER_DELTA_UP = defaultdict(lambda: 400,  # Conservative ramp up default
  {
    CAR.BYD_DOLPHIN: 700,       # BLF analysis (MUST match firmware!)
    CAR.BYD_ATTO3: 700,         # Initial clone of Dolphin ramp
    # CAR.BYD_ATTO3: 400,         # Balanced control
    # CAR.DEEPAL_S05: 400,        # Balanced control
  }
)

STEER_DELTA_DOWN = defaultdict(lambda: 500,  # Fast ramp down for safety default
  {
    CAR.BYD_DOLPHIN: 1400,      # BLF max 1008 + 39% (MUST match firmware!)
    CAR.BYD_ATTO3: 1400,        # Initial clone of Dolphin ramp
    # CAR.BYD_ATTO3: 500,         # Moderate ramp down
    # CAR.DEEPAL_S05: 500,        # Moderate ramp down
  }
)

# Longitudinal Limits (m/s²)
ACCEL_MAX = defaultdict(lambda: 2.0,  # Safe acceleration
  {
    CAR.BYD_DOLPHIN: 2.3,       # 0-100km/h in 7.5s
    CAR.BYD_ATTO3: 2.3,         # Placeholder until ATTO3 data
    # CAR.BYD_ATTO3: 2.0,         # 0-100km/h in 7.3s
    # CAR.DEEPAL_S05: 2.0,        # Similar to ATTO3
  }
)

ACCEL_MIN = defaultdict(lambda: -3.5,  # Safe deceleration
  {
    CAR.BYD_DOLPHIN: -3.2,      # ADAS-tuned for city comfort
    CAR.BYD_ATTO3: -3.2,        # Placeholder until ATTO3 data
    # CAR.BYD_ATTO3: -3.5,        # Standard regen
    # CAR.DEEPAL_S05: -3.5,       # Standard regen
  }
)

# Steering Angle Limits (degrees) - SAFETY CONSTRAINT
# Maximum commanded steering angle from planner (before wrapping to ±12.7° for EPS)
# ±120° is a safe limit to prevent excessive steering requests
MAX_STEERING_ANGLE = defaultdict(lambda: 120,  # Safety limit: ±120° for all gateway
  {
    CAR.BYD_DOLPHIN: 120,       # Safety limit: prevents excessive steering commands
    CAR.BYD_ATTO3: 120,         # Placeholder limit
    # CAR.BYD_ATTO3: 120,         # Safety limit: same for all vehicles
    # CAR.DEEPAL_S05: 120,        # Safety limit: same for all vehicles
  }
)

# Angle Rate Limits (degrees per second) - SPEED-DEPENDENT for physics safety
# Format: ([speed_m/s breakpoints], [rate_limit_deg/s values])
# Lower speeds = higher rate limits (tighter turns allowed)
# Higher speeds = lower rate limits (physics-limited smooth turns)
# Pattern from Toyota/PSA best practices
ANGLE_RATE_LIMITS_UP = defaultdict(lambda: ([0, 5, 15], [5.0, 2.5, 0.5]),  # Conservative default
  {
    CAR.BYD_DOLPHIN: ([0, 5, 15], [8.0, 3.0, 0.8]),  # Tuned for compact city car
    CAR.BYD_ATTO3: ([0, 5, 15], [8.0, 3.0, 0.8]),   # Placeholder clone until ATTO3 data
    # Speed breakpoints: 0 m/s (stopped), 5 m/s (18 km/h), 15 m/s (54 km/h)
    # Rate limits: 8.0 deg/s (parking), 3.0 deg/s (city), 0.8 deg/s (highway)
    # CAR.BYD_ATTO3: ([0, 5, 20], [6.0, 2.5, 0.5]),      # SUV with higher mass
    # CAR.DEEPAL_S05: ([0, 5, 20], [6.0, 2.5, 0.5]),     # Similar to ATTO3
  }
)

ANGLE_RATE_LIMITS_DOWN = defaultdict(lambda: ([0, 5, 15], [10.0, 4.0, 1.0]),  # Faster down ramp for safety
  {
    CAR.BYD_DOLPHIN: ([0, 5, 15], [15.0, 5.0, 1.5]),  # Fast disengagement for safety
    CAR.BYD_ATTO3: ([0, 5, 15], [15.0, 5.0, 1.5]),   # Placeholder clone
    # Down ramp ~2x faster than up ramp for emergency override
    # CAR.BYD_ATTO3: ([0, 5, 20], [12.0, 4.5, 1.0]),     # SUV safety margins
    # CAR.DEEPAL_S05: ([0, 5, 20], [12.0, 4.5, 1.0]),    # Similar to ATTO3
  }
)

# Driver Override Blending Breakpoints (Nm)
# Graduated blending thresholds for smooth driver handover
# Pattern from gateway best practice (4-point interpolation)
DRIVER_TORQUE_BREAKPOINTS = defaultdict(lambda: [50, 200, 500, 800],  # Conservative for unknown
  {
    CAR.BYD_DOLPHIN: [50, 200, 500, 800],  # Tuned for EPS sensitivity
    CAR.BYD_ATTO3: [50, 200, 500, 800],   # Placeholder clone
    # 50 Nm: No driver input detected
    # 200 Nm: Light steering input (lane change)
    # 500 Nm: Moderate steering input (evasive maneuver)
    # 800 Nm: Strong steering input (full override)
    # CAR.BYD_ATTO3: [60, 250, 600, 900],      # Heavier vehicle, higher torques
    # CAR.DEEPAL_S05: [60, 250, 600, 900],     # Similar to ATTO3
  }
)

DRIVER_TORQUE_FACTORS = defaultdict(lambda: [1.0, 0.6, 0.3, 0.0],  # Assist % at each breakpoint
  {
    CAR.BYD_DOLPHIN: [1.0, 0.6, 0.3, 0.0],  # 100% -> 60% -> 30% -> 0%
    CAR.BYD_ATTO3: [1.0, 0.6, 0.3, 0.0],   # Placeholder clone
    # CAR.BYD_ATTO3: [1.0, 0.6, 0.3, 0.0],       # Same graduated blending
    # CAR.DEEPAL_S05: [1.0, 0.6, 0.3, 0.0],      # Same graduated blending
  }
)

# Regenerative Braking Parameters
REGEN_BRAKE_MAX = defaultdict(lambda: 0.3,  # Moderate regen
  {
    CAR.BYD_DOLPHIN: 0.42,      # 40kW max regen power
    CAR.BYD_ATTO3: 0.42,        # Placeholder clone
    # CAR.BYD_ATTO3: 0.38,        # 60kW max regen power
    # CAR.DEEPAL_S05: 0.38,       # Similar to ATTO3
  }
)

REGEN_STRENGTH = defaultdict(lambda: 0.7,  # Balanced feel
  {
    CAR.BYD_DOLPHIN: 0.85,      # Strong city regen
    CAR.BYD_ATTO3: 0.85,        # Placeholder clone
    # CAR.BYD_ATTO3: 0.75,        # Moderate feel
    # CAR.DEEPAL_S05: 0.75,       # Moderate feel
  }
)

# Physical Drivetrain Specs
WHEEL_RADIUS = defaultdict(lambda: DEFAULT_WHEEL_RADIUS,  # Generic default
  {
    CAR.BYD_DOLPHIN: 0.318,     # 205/60R16 tires
    CAR.BYD_ATTO3: 0.318,       # Placeholder clone
    # CAR.BYD_ATTO3: 0.334,       # 215/55R18 tires
    # CAR.DEEPAL_S05: 0.341,      # 235/50R19 tires
  }
)

FINAL_DRIVE_RATIO = defaultdict(lambda: DEFAULT_FINAL_DRIVE_RATIO,
  {
    CAR.BYD_DOLPHIN: 3.89,      # High ratio for city efficiency
    CAR.BYD_ATTO3: 3.89,        # Placeholder clone
    # CAR.BYD_ATTO3: 3.36,        # Single-speed EV ratio
    # CAR.DEEPAL_S05: 3.36,       # Single-speed EV ratio
  }
)

MOTOR_EFFICIENCY = defaultdict(lambda: DEFAULT_MOTOR_EFFICIENCY,
  {
    CAR.BYD_DOLPHIN: 0.93,      # Efficient city motor
    CAR.BYD_ATTO3: 0.93,        # Placeholder clone
    # CAR.BYD_ATTO3: 0.91,        # Front motor efficiency
    # CAR.DEEPAL_S05: 0.91,       # Similar to ATTO3
  }
)

# ---------------------------------------------------------------------------
# Controller parameter container
# ---------------------------------------------------------------------------
class CarControllerParams:

  # Common baseline parameters shared across all models
  STEER_STEP = 1                         # Steering command step size
  STEER_ERROR_MAX = 350                  # Maximum steering error tolerance (Nm)

  # Driver interaction detection (software-level only, not used in firmware)
  STEER_DRIVER_FACTOR = 1                # Factor for proportional driver override
  STEER_DRIVER_INTERACTION_THRESHOLD = 1.0  # Driver torque threshold for interaction detection (Nm)

  TORQUE_WIND_DOWN = 0.96                # Torque reduction factor for smooth deactivation
  

  # =============================================================================
  # [Section] Parameter Selection
  # [Brief ] Select per-model parameters based on fingerprint using dictionaries
  # ---------------------------------------------------------------------------
  # [Function] __init__
  # [Brief ] Load all parameters from dictionaries (Toyota pattern)
  # [Params] CP: CarParams
  # [Returns] None
  # =============================================================================
  def __init__(self, CP):
    """Load per-model parameters from dictionaries (Toyota pattern).

    All parameters are defined in dictionaries above with safe defaults.
    This approach is simple, maintainable, and easy to tune per model.
    """
    # Get model fingerprint for dictionary lookup
    model = CP.carFingerprint

    # ==========================================================================
    # Steering Parameters (motor counts - NOT Nm!)
    # ==========================================================================
    self.STEER_MAX_COUNT = STEER_MAX[model]
    self.STEER_MAX = self.STEER_MAX_COUNT  # Alias for compatibility

    self.STEER_DELTA_UP_COUNT = STEER_DELTA_UP[model]
    self.STEER_DELTA_UP = self.STEER_DELTA_UP_COUNT  # Alias for compatibility

    self.STEER_DELTA_DOWN_COUNT = STEER_DELTA_DOWN[model]
    self.STEER_DELTA_DOWN = self.STEER_DELTA_DOWN_COUNT  # Alias for compatibility

    self.MAX_STEERING_ANGLE = MAX_STEERING_ANGLE[model]

    # Speed-dependent angle rate limits (Toyota/PSA best practice)
    self.ANGLE_RATE_LIMITS_UP = ANGLE_RATE_LIMITS_UP[model]
    self.ANGLE_RATE_LIMITS_DOWN = ANGLE_RATE_LIMITS_DOWN[model]

    # Driver override blending parameters
    self.DRIVER_TORQUE_BREAKPOINTS = DRIVER_TORQUE_BREAKPOINTS[model]
    self.DRIVER_TORQUE_FACTORS = DRIVER_TORQUE_FACTORS[model]

    # ==========================================================================
    # Longitudinal Parameters
    # ==========================================================================
    self.ACCEL_MAX = ACCEL_MAX[model]
    self.ACCEL_MIN = ACCEL_MIN[model]

    # ==========================================================================
    # Regenerative Braking
    # ==========================================================================
    self.REGEN_BRAKE_MAX = REGEN_BRAKE_MAX[model]
    self.REGEN_STRENGTH = REGEN_STRENGTH[model]

    # ==========================================================================
    # Physical Drivetrain Specs
    # ==========================================================================
    self.WHEEL_RADIUS = WHEEL_RADIUS[model]
    self.FINAL_DRIVE_RATIO = FINAL_DRIVE_RATIO[model]
    self.MOTOR_EFFICIENCY = MOTOR_EFFICIENCY[model]

    # ==========================================================================
    # MPC Echo Service Config (model-specific in future)
    # ==========================================================================
    self.MPC_ECHO_LAT = self.MPCEchoMode.DISABLED
    self.MPC_ECHO_LONG = self.MPCEchoMode.DISABLED
    self.STOCK_AEB_AVAILABLE = True
    self.STOCK_FCW_AVAILABLE = True
    self.MPC_LAT_ECHO_MSGS = []
    self.MPC_LONG_ECHO_MSGS = []
    self.MPC_CHECKSUM_KEY = 0xAF  # BYD checksum key

  # =============================================================================
  # [Section] MPC Echo Service Modes
  # [Brief ] Stock MPC integration: send fake messages or not?
  # =============================================================================
  class MPCEchoMode:
    DISABLED = 0        # Don't send anything (no echo at all)
    RAW_FORWARD = 1     # Forward raw bytes as-is (simple copy)
    MODIFY_SIGNALS = 2  # Parse → modify signals → repack (complex)


# NOTE: Old _init_*_params() methods removed - now using dictionaries above
# This follows Toyota pattern: simple, maintainable, easy to tune per model


# =============================================================================
# [Section] OLD CODE REMOVED
# [Brief ] The following methods have been replaced by dictionaries:
#   - _init_byd_atto3_params() → BYD_* dictionaries
#   - _init_byd_dolphin_params() → BYD_* dictionaries
#   - _init_deepal_s05_params() → BYD_* dictionaries
#   - _init_default_params() → defaultdict defaults
# =============================================================================
# ---------------------------------------------------------------------------
# Gateway capability flags
# ---------------------------------------------------------------------------
class GatewayFlags(IntFlag):
  # Powertrain type identification
  EV = 1                        # Battery Electric Vehicle
  HYBRID = 2                    # Hybrid Electric Vehicle

  # Lateral control method (BYD uses ANGLE control only)
  ANGLE_CONTROL = 16            # Steering angle control (BYD default and ONLY method)

  # Advanced driving features
  STOP_AND_GO = 64              # Automatic stop and resume capability

  # Sensor and monitoring capabilities
  FOUR_WHEEL_SENSORS = 2048     # Individual wheel speed sensors available


# -----------------------------------------------------------------------------
# [Section] Cruise Button Mapping
# [Brief ] Standardized mapping of button signals to functions
# -----------------------------------------------------------------------------
# =============================================================================
# [Section] Class
# [Brief ] Cruise/assist button to DBC signal mapping
# ---------------------------------------------------------------------------
# [Class ] CruiseButtons
# [Brief ] Names used in CAN for standard functions
# [Bases ] object
# =============================================================================
class CruiseButtons:
  RESUME_ACCEL = "SPEED_UP_BTN"      # Resume cruise / Increase speed
  DECEL_SET = "SPEED_DOWN_BTN"         # Set cruise / Decrease speed
  CANCEL = "CRUISE_CANCEL"         # Cancel cruise control
  SET = "CRUISE_SET"               # Set current speed as cruise target
  RESUME = "CRUISE_RESUME"         # Resume previously set cruise speed
  GAP_NEAR = "DISTANCE_NEAR_BTN"         # Decrease following distance
  GAP_FAR = "DISTANCE_FAR_BTN"         # Increase following distance


# ---------------------------------------------------------------------------
# Platform metadata & docs helpers
# ---------------------------------------------------------------------------

# -----------------------------------------------------------------------------
# [Section] Documentation Config
# -----------------------------------------------------------------------------
@dataclass
# =============================================================================
# [Section] Class
# [Brief ] Documentation metadata for Gateway platforms
# ---------------------------------------------------------------------------
# [Class ] GatewayCarDocs
# [Brief ] Sets harness/parts details
# [Bases ] CarDocs
# =============================================================================
class GatewayCarDocs(CarDocs):
  package: str = "Gateway ADAS"

  # =============================================================================
  # [Section] Documentation
  # [Brief ] Populate documentation parts/harness details
  # ---------------------------------------------------------------------------
  # [Function] init_make
  # [Brief ] Populate documentation parts/harness details
  # [Params] CP: CarParams
  # [Returns] None
  # =============================================================================
  def init_make(self, CP: CarParams):
    self.car_parts = CarParts.common([CarHarness.bosch_b])  # Standard Bosch harness


# -----------------------------------------------------------------------------
# [Section] Platform Configuration
# -----------------------------------------------------------------------------
# ---------------------------------------------------------------------------
# CAR enum definition (one entry per supported model)
# ---------------------------------------------------------------------------

class CAR(Platforms):
  BYD_ATTO3 = GatewayPlatformConfig(
    [GatewayCarDocs("BYD ATTO3")],
    # BYD ATTO3 specifications - uses actual byd_atto3.dbc (500 kbps CAN classic)
    GatewayCarSpecs(mass=1625, wheelbase=2.720, steerRatio=15.0, minSteerSpeed=0 * CV.KPH_TO_MS),
    dbc_dict={Bus.pt: 'byd_atto3', Bus.cam: 'byd_atto3'}
  )

  # DEEPAL_S05 = GatewayPlatformConfig(
  #   [GatewayCarDocs("DEEPAL S05")],
  #   GatewayCarSpecs(mass=1750, wheelbase=2.72, steerRatio=15.5, minSteerSpeed=3 * CV.KPH_TO_MS)
  # )

  BYD_DOLPHIN = GatewayPlatformConfig(
    [GatewayCarDocs("BYD DOLPHIN")],
    # Official BYD DOLPHIN Thai market specifications
    GatewayCarSpecs(mass=1520, wheelbase=2.700, steerRatio=15.0, minSteerSpeed=0 * CV.KPH_TO_MS),
    # Dolphin uses CAN-FD (2 Mbps data, 500 kbps arbitration) with different payload packing
    dbc_dict={Bus.pt: 'byd_dolphin', Bus.cam: 'byd_dolphin'}
  )

# ---------------------------------------------------------------------------
# Human-readable specs dictionary (used throughout the interface)
# ---------------------------------------------------------------------------
CAR_SPECS = {
  CAR.BYD_ATTO3: {
    # Clone baseline tuned for ATTO3 (shares gateway hardware with Dolphin)
    'mass': 1625,
    'wheelbase': 2.720,
    'steerRatio': 15.0,
    'centerToFrontRatio': 0.44,
    'tireStiffnessFactor': 1.0,
    'minSteerSpeed': 0 * CV.KPH_TO_MS,
    'minEnableSpeed': -1,
  },
  # CAR.DEEPAL_S05: {
  #   'mass': 1750,  # kg
  #   'wheelbase': 2.72,  # m
  #   'steerRatio': 15.5,
  #   'centerToFrontRatio': 0.42,
  #   'tireStiffnessFactor': 0.85,
  #   'minSteerSpeed': 3 * CV.KPH_TO_MS,
  #   'minEnableSpeed': 0,
  # },
  CAR.BYD_DOLPHIN: {
    # Official BYD DOLPHIN Thai market specifications
    'mass': 1520,  # kg (Standard version curb weight from Thai specs)
    'wheelbase': 2.700,  # m (official wheelbase from Thai specs)
    'steerRatio': 15.0,  # (from sunnypilot-pc QIN PLUS DMI - closest BYD compact match)
    'centerToFrontRatio': 0.44,  # (from sunnypilot-pc QIN PLUS DMI - proven BYD compact value)
    'tireStiffnessFactor': 1.0,  # (from sunnypilot-pc QIN PLUS DMI - proven BYD compact value)
    'minSteerSpeed': 0 * CV.KPH_TO_MS,  # BYD proven value (stop and go capability)
    'minEnableSpeed': -1,  # BYD proven value (full stop and go support)
  },
}



# ---------------------------------------------------------------------------
# Firmware-query + DBC helpers (kept at bottom for easy lookup)
# ---------------------------------------------------------------------------
FW_QUERY_CONFIG = FwQueryConfig(
  requests=[],                  # No firmware queries needed
  non_essential_ecus={},        # No non-essential ECUs
  extra_ecus=[],                # No extra ECUs
)

# Firmware version mappings (unified across all models)
FW_VERSIONS: dict = {
  CAR.BYD_ATTO3: {},            # Uses CAN fingerprinting (same baseline as Dolphin)
  # CAR.DEEPAL_S05: {},           # Uses CAN fingerprinting
  CAR.BYD_DOLPHIN: {},          # Uses CAN fingerprinting
}

# DBC file mappings (unified Gateway protocol)
DBC = CAR.create_dbc_map()

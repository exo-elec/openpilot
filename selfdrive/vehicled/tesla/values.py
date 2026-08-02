#!/usr/bin/env python3
"""Tesla vehicle constants and configuration."""
from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum, IntFlag

from cereal import car

# CAN Bus definitions
class CANBUS:
  """Tesla CAN bus mapping."""
  party = 0           # Primary vehicle bus
  vehicle = 1         # Vehicle bus
  autopilot_party = 2  # Autopilot bus


# Gear mapping from Tesla DI_gear to openpilot GearShifter
GEAR_MAP = {
  "DI_GEAR_INVALID": car.CarState.GearShifter.unknown,
  "DI_GEAR_P": car.CarState.GearShifter.park,
  "DI_GEAR_R": car.CarState.GearShifter.reverse,
  "DI_GEAR_N": car.CarState.GearShifter.neutral,
  "DI_GEAR_D": car.CarState.GearShifter.drive,
  "DI_GEAR_SNA": car.CarState.GearShifter.unknown,
}


# Tesla platform configurations
@dataclass
class PlatformConfig:
  """Vehicle platform configuration (size/physics baseline).

  Baselines tuned for EVs — low CoG from floor battery, improved Cd vs ICE.
  Learning adjusts live from CAN data; these are starting points.
  """
  name: str
  mass: float                # kg
  wheelbase: float           # meters
  steer_ratio: float         # steering ratio
  drag_coefficient: float = 0.28
  center_of_mass_z: float = 0.50  # meters, EVs lower than ICE due to floor battery
  dbc_file: str = "tesla_model3_party"


class VEHICLE:
  """Vehicle platform presets."""
  SEDAN_D = PlatformConfig(
    name="D-Sedan",
    mass=2040.0,
    wheelbase=2.920,
    steer_ratio=13.0,
    drag_coefficient=0.235,
    center_of_mass_z=0.50,
  )
  SUV_D = PlatformConfig(
    name="D-SUV",
    mass=2200.0,
    wheelbase=2.900,
    steer_ratio=13.0,
    drag_coefficient=0.250,
    center_of_mass_z=0.52,
  )
  SUV_E = PlatformConfig(
    name="E-SUV",
    mass=2450.0,
    wheelbase=2.970,
    steer_ratio=13.5,
    drag_coefficient=0.270,
    center_of_mass_z=0.53,
  )
  SUV_C = PlatformConfig(
    name="C-SUV",
    mass=1810.0,
    wheelbase=2.800,
    steer_ratio=14.5,
    drag_coefficient=0.270,
    center_of_mass_z=0.50,
  )

  # Generic vehicle size categories tuned for Chinese EVs
  # Averages from popular models: BYD/Aion/Deepal/Xpeng/NIO/Li Auto/Geely/GWM/MG/Changan
  # Learning adjusts live from CAN; these are starting baselines
  HATCH_A = PlatformConfig(
    name="A-Hatch",
    mass=1180.0,
    wheelbase=2.490,
    steer_ratio=16.0,
    drag_coefficient=0.290,
    center_of_mass_z=0.49,
  )
  HATCH_B = PlatformConfig(
    name="B-Hatch",
    mass=1490.0,
    wheelbase=2.670,
    steer_ratio=15.5,
    drag_coefficient=0.280,
    center_of_mass_z=0.49,
  )
  SUV_B = PlatformConfig(
    name="B-SUV",
    mass=1650.0,
    wheelbase=2.710,
    steer_ratio=15.0,
    drag_coefficient=0.280,
    center_of_mass_z=0.50,
  )
  SEDAN_B = PlatformConfig(
    name="B-Sedan",
    mass=1485.0,
    wheelbase=2.650,
    steer_ratio=15.5,
    drag_coefficient=0.270,
    center_of_mass_z=0.49,
  )
  SEDAN_C = PlatformConfig(
    name="C-Sedan",
    mass=1675.0,
    wheelbase=2.790,
    steer_ratio=14.5,
    drag_coefficient=0.250,
    center_of_mass_z=0.50,
  )
  MPV = PlatformConfig(
    name="MPV",
    mass=2700.0,
    wheelbase=3.200,
    steer_ratio=14.0,
    drag_coefficient=0.240,
    center_of_mass_z=0.52,
  )

  @classmethod
  def from_vehicle_config(cls, cfg):
    """Build a PlatformConfig from generic VehicleConfig."""
    return PlatformConfig(
      name=cfg.name,
      mass=cfg.mass,
      wheelbase=cfg.wheelbase,
      steer_ratio=cfg.steer_ratio,
      drag_coefficient=cfg.drag_coefficient,
      center_of_mass_z=cfg.center_of_mass_z,
    )


# Controller parameters
@dataclass
class CarControllerParams:
  """Tesla car controller parameters."""
  # Steering
  STEER_STEP = 2              # Angle command sent at 50 Hz (every 2nd frame at 100Hz)
  STEER_LIMIT_TIMER = 0.4     # seconds
  STEER_ACTUATOR_DELAY = 0.1  # seconds
  STEER_AT_STANDSTILL = True
  
  # Angle limits (degrees)
  MAX_STEER_ANGLE = 360.0
  MAX_ANGLE_RATE = 5.0        # deg/20ms frame, EPS faults at 12 at standstill
  
  # Lateral acceleration limits
  # Add extra tolerance for average banked road (~3.4 degrees, 6% superelevation)
  AVERAGE_ROAD_ROLL = 0.06
  MAX_LATERAL_ACCEL = 3.0 + (9.81 * AVERAGE_ROAD_ROLL)  # ~3.6 m/s^2
  MAX_LATERAL_JERK = 3.0 + (9.81 * AVERAGE_ROAD_ROLL)   # ~3.6 m/s^3
  
  # Longitudinal
  ACCEL_MAX = 2.0             # m/s^2
  ACCEL_MIN = -3.48           # m/s^2
  JERK_LIMIT_MAX = 4.9        # m/s^3, ACC faults at 5.0
  JERK_LIMIT_MIN = -4.9       # m/s^3, ACC faults at 5.0
  
  # Speed thresholds
  V_EGO_STOPPING = 0.1        # m/s
  V_EGO_STARTING = 0.1        # m/s
  STOPPING_DECEL_RATE = 0.3   # m/s^3


class TeslaSafetyFlags(IntFlag):
  """Safety flags for Tesla."""
  LONG_CONTROL = 1


class TeslaFlags(IntFlag):
  """Feature flags for Tesla."""
  LONG_CONTROL = 1


# DBC file mapping
DBC: dict[str, dict[int, str]] = {
  "SEDAN_D": {CANBUS.party: "tesla_model3_party"},
  "SUV_D": {CANBUS.party: "tesla_model3_party"},
  "SUV_E": {CANBUS.party: "tesla_model3_party"},
  "SUV_C": {CANBUS.party: "tesla_model3_party"},
  "SUV_B": {CANBUS.party: "tesla_model3_party"},
  "SEDAN_B": {CANBUS.party: "tesla_model3_party"},
  "SEDAN_C": {CANBUS.party: "tesla_model3_party"},
  "HATCH_A": {CANBUS.party: "tesla_model3_party"},
  "HATCH_B": {CANBUS.party: "tesla_model3_party"},
  "MPV": {CANBUS.party: "tesla_model3_party"},
}

# Steering threshold for detecting driver override
STEER_THRESHOLD = 1.0

# Firmware query configuration (simplified)
FW_QUERY_CONFIG = {
  "requests": [
    {
      "tx": [b"\x02\x3e\x00\x00\x00\x00\x00\x00", b"\x02\x1a\x9f\x00\x00\x00\x00\x00"],
      "rx": [b"\x02\x7e\x00\x00\x00\x00\x00\x00", b"\x06\x5a\x9f\x00\x00\x00\x00"],
      "bus": 0,
    }
  ]
}

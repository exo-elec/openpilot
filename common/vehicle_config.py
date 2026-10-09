"""Shared vehicle configuration for simulation and the socketd vehicle adapter.

Defines baseline parameters for common vehicle categories.
Select via EOPVehicleType param or --vehicle-type CLI argument.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import ClassVar


@dataclass(frozen=True)
class VehicleConfig:
  """Physical vehicle parameters used by simulation + control."""
  name: str
  mass: float              # kg
  wheelbase: float         # meters
  steer_ratio: float       # steering wheel to road wheel
  max_steer_angle: float   # degrees at road wheel
  drag_coefficient: float
  center_of_mass_z: float  # meters

  # Derived / optional
  length: float = 4.5      # meters
  width: float = 1.8       # meters
  height: float = 1.5      # meters


class VehicleType:
  """Preset vehicle size categories (European segment naming)."""

  HATCH_A = VehicleConfig(
    name="A-Hatch",
    mass=1180.0,
    wheelbase=2.50,
    steer_ratio=16.0,
    max_steer_angle=35.0,
    drag_coefficient=0.29,
    center_of_mass_z=0.48,
    length=3.78,
    width=1.71,
    height=1.54,
  )

  HATCH_B = VehicleConfig(
    name="B-Hatch",
    mass=1520.0,
    wheelbase=2.70,
    steer_ratio=15.5,
    max_steer_angle=35.0,
    drag_coefficient=0.275,
    center_of_mass_z=0.48,
    length=4.29,
    width=1.76,
    height=1.57,
  )

  SEDAN_B = VehicleConfig(
    name="B-Sedan",
    mass=1480.0,
    wheelbase=2.65,
    steer_ratio=15.5,
    max_steer_angle=35.0,
    drag_coefficient=0.27,
    center_of_mass_z=0.48,
    length=4.47,
    width=1.78,
    height=1.49,
  )

  SEDAN_C = VehicleConfig(
    name="C-Sedan",
    mass=1680.0,
    wheelbase=2.73,
    steer_ratio=14.5,
    max_steer_angle=35.0,
    drag_coefficient=0.255,
    center_of_mass_z=0.50,
    length=4.76,
    width=1.83,
    height=1.51,
  )

  SEDAN_D = VehicleConfig(
    name="D-Sedan",
    mass=1950.0,
    wheelbase=2.90,
    steer_ratio=12.5,
    max_steer_angle=35.0,
    drag_coefficient=0.235,
    center_of_mass_z=0.48,
    length=4.99,
    width=1.91,
    height=1.50,
  )

  SUV_B = VehicleConfig(
    name="B-SUV",
    mass=1580.0,
    wheelbase=2.65,
    steer_ratio=15.0,
    max_steer_angle=35.0,
    drag_coefficient=0.28,
    center_of_mass_z=0.50,
    length=4.31,
    width=1.83,
    height=1.67,
  )

  SUV_C = VehicleConfig(
    name="C-SUV",
    mass=1780.0,
    wheelbase=2.75,
    steer_ratio=14.5,
    max_steer_angle=35.0,
    drag_coefficient=0.275,
    center_of_mass_z=0.50,
    length=4.46,
    width=1.87,
    height=1.61,
  )

  SUV_D = VehicleConfig(
    name="D-SUV",
    mass=2100.0,
    wheelbase=2.90,
    steer_ratio=13.0,
    max_steer_angle=35.0,
    drag_coefficient=0.25,
    center_of_mass_z=0.50,
    length=4.85,
    width=1.93,
    height=1.70,
  )

  SUV_E = VehicleConfig(
    name="E-SUV",
    mass=2450.0,
    wheelbase=3.05,
    steer_ratio=13.5,
    max_steer_angle=35.0,
    drag_coefficient=0.27,
    center_of_mass_z=0.52,
    length=5.21,
    width=1.99,
    height=1.80,
  )

  MPV = VehicleConfig(
    name="MPV",
    mass=2550.0,
    wheelbase=3.10,
    steer_ratio=14.0,
    max_steer_angle=35.0,
    drag_coefficient=0.23,
    center_of_mass_z=0.52,
    length=5.25,
    width=1.99,
    height=1.82,
  )

  _by_name: ClassVar[dict[str, VehicleConfig]] = {
    "hatch_a": HATCH_A,
    "hatch_b": HATCH_B,
    "sedan_b": SEDAN_B,
    "sedan_c": SEDAN_C,
    "sedan_d": SEDAN_D,
    "suv_b": SUV_B,
    "suv_c": SUV_C,
    "suv_d": SUV_D,
    "suv_e": SUV_E,
    "mpv": MPV,
  }

  @classmethod
  def get(cls, name: str) -> VehicleConfig:
    """Lookup by kebab-case or snake_case name."""
    key = name.lower().replace("-", "_")
    if key not in cls._by_name:
      available = ", ".join(cls._by_name.keys())
      raise ValueError(f"Unknown vehicle type '{name}'. Available: {available}")
    return cls._by_name[key]

  @classmethod
  def list_available(cls) -> list[str]:
    return list(cls._by_name.keys())

"""EOP parameter adapter around the unchanged NGP vehicle-model equations.

Retains the existing EOP fallback coefficients for incomplete CarParams.
Complete parameters pass through numerically unchanged; hardware never selects
an alternative control equation.
"""
from types import SimpleNamespace

from nagaspilot.controls.ngp_vehicle_model import (
  VehicleModel as _VehicleModel,
  ACCELERATION_DUE_TO_GRAVITY, kin_ss_sol, create_dyn_state_matrices,
  dyn_ss_sol, calc_slip_factor,
)

__all__ = ["VehicleModel", "ACCELERATION_DUE_TO_GRAVITY", "kin_ss_sol",
           "create_dyn_state_matrices", "dyn_ss_sol", "calc_slip_factor"]


class VehicleModel(_VehicleModel):
  def __init__(self, CP):
    front, rear = CP.centerToFront, CP.wheelbase - CP.centerToFront
    physical = SimpleNamespace(
      mass=CP.mass, wheelbase=CP.wheelbase, centerToFront=front,
      steerRatioRear=CP.steerRatioRear, steerRatio=CP.steerRatio,
      rotationalInertia=CP.rotationalInertia if CP.rotationalInertia > 0 else CP.mass * (front**2 + rear**2),
      tireStiffnessFront=CP.tireStiffnessFront if CP.tireStiffnessFront > 0 else 150000.0,
      tireStiffnessRear=CP.tireStiffnessRear if CP.tireStiffnessRear > 0 else 180000.0,
    )
    super().__init__(physical)

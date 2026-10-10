"""Shared control math must match the pinned, proven NGP OpenDBC baseline."""
from types import SimpleNamespace as NS

import numpy as np
import pytest

from nagaspilot.controls.ngp_vehicle_model import VehicleModel


@pytest.mark.parametrize("speed", [0.0, 0.1, 0.2, 11.0, 22.0, 33.0, 45.0])
@pytest.mark.parametrize("stiffness,ratio", [(1.0, 12.0), (0.7, 16.0), (1.3, 14.0)])
def test_physics_matches_official_ngp_baseline(speed, stiffness, ratio):
  upstream = pytest.importorskip("opendbc.car.vehicle_model")
  cp = NS(mass=2208., rotationalInertia=4325.73486328125, wheelbase=2.89,
          centerToFront=1.445, steerRatioRear=0.0, tireStiffnessFront=241830.375,
          tireStiffnessRear=382284.53125, steerRatio=12.)
  model, reference = VehicleModel(cp), upstream.VehicleModel(cp)
  model.update_params(stiffness, ratio)
  reference.update_params(stiffness, ratio)
  for steering in (-0.3, 0.0, 0.3):
    for roll in (-0.05, 0.0, 0.05):
      assert model.calc_curvature(steering, speed, roll) == reference.calc_curvature(steering, speed, roll)
      assert model.yaw_rate(steering, speed, roll) == reference.yaw_rate(steering, speed, roll)
      np.testing.assert_array_equal(model.steady_state_sol(steering, speed, roll),
                                    reference.steady_state_sol(steering, speed, roll))

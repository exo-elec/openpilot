import math
import numpy as np

from openpilot.common.constants import CV
from nagaspilot.controls.steering_policy import reduce_steer
from openpilot.system.socketd.vehicle.car.vehicle_model import VehicleModel
from nagaspilot.speed_zones import HIGHWAY_SPEED_MPS, MAX_SPEED_MPS


_A_TOTAL_MAX_V = [1.7, 3.2]
_A_TOTAL_MAX_BP = [HIGHWAY_SPEED_MPS, MAX_SPEED_MPS]


def limit_accel_in_turns(v_ego, angle_steers, a_target, CP, VM=None):
  """Limit longitudinal acceleration using the vehicle model's lateral acceleration."""
  if VM is None:
    VM = VehicleModel(CP)

  a_total_max = np.interp(v_ego, _A_TOTAL_MAX_BP, _A_TOTAL_MAX_V)
  curvature = VM.calc_curvature(angle_steers * CV.DEG_TO_RAD, v_ego, roll=0.0)
  a_y = v_ego ** 2 * abs(curvature)
  a_x_allowed = math.sqrt(max(a_total_max ** 2 - a_y ** 2, 0.))
  return [a_target[0], min(a_target[1], a_x_allowed)]

import numpy as np
from openpilot.system.socketd.vehicle.protocol.kalman import KF1D, get_kalman_gain

class VehicleStateBase:
  def __init__(self, CP):
    self.CP = CP
    self.steering_pressed_cnt = 0
    A = [[1.0, 0.01], [0.0, 1.0]]
    C = [[1.0, 0.0]]
    K = get_kalman_gain(0.01, np.array(A), np.array(C), np.array([[0.0, 0.0], [0.0, 100.0]]), 0.3)
    self.v_ego_kf = KF1D([[0.0], [0.0]], A, C[0], K)

  def update_speed_kf(self, speed):
    if abs(speed - self.v_ego_kf.x[0][0]) > 2:
      self.v_ego_kf.set_x([[speed], [0.0]])
    return tuple(float(value) for value in self.v_ego_kf.update(speed))

  def update_steering_pressed(self, pressed, count):
    self.steering_pressed_cnt += 1 if pressed else -1
    self.steering_pressed_cnt = int(np.clip(self.steering_pressed_cnt, 0, count * 2 + 1))
    return self.steering_pressed_cnt > count

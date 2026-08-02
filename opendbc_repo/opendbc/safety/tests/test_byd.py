#!/usr/bin/env python3
import unittest
import numpy as np

from opendbc.car.byd.carcontroller import get_safety_CP
from opendbc.car.byd.values import CarControllerParams
from opendbc.car.lateral import get_max_angle_delta_vm, get_max_angle_vm
from opendbc.car.structs import CarParams
from opendbc.car.vehicle_model import VehicleModel
from opendbc.safety.tests.libsafety import libsafety_py
import opendbc.safety.tests.common as common
from opendbc.safety.tests.common import CANPackerPanda


MPC_LATERAL_CMD = 0x1E2  # A_0x1E2_MPC_Lateral_Cmd_L8_20ms
MPC_MPC_STATE = 0x316  # A_0x316_MPC_MpcState_L8_20ms


class TestBydSafety(common.PandaCarSafetyTest, common.AngleSteeringSafetyTest):
  TX_MSGS = [[MPC_LATERAL_CMD, 0], [MPC_MPC_STATE, 0]]
  RELAY_MALFUNCTION_ADDRS = {0: (MPC_LATERAL_CMD, MPC_MPC_STATE)}
  FWD_BLACKLISTED_ADDRS = {2: [MPC_LATERAL_CMD, MPC_MPC_STATE]}

  # matches `vehicle_moving = speed > 0.1` in opendbc/safety/modes/byd.h
  STANDSTILL_THRESHOLD = 0.1
  # matches `gas_pressed = msg->data[0] > 10U` in opendbc/safety/modes/byd.h
  GAS_PRESSED_THRESHOLD = 10

  STEER_ANGLE_MAX = 390
  STEER_ANGLE_TEST_MAX = 380
  DEG_TO_CAN = 10
  # CRAWL / WALK / CITY / URBAN / HIGHWAY comfort breakpoints;
  # see byd.h's BYD_STEERING_LIMITS comment for provenance.
  ANGLE_RATE_BP = [0., 12., 24.]
  ANGLE_RATE_UP = [4., 2., .5]
  ANGLE_RATE_DOWN = [4., 3., 1.5]
  LATERAL_FREQUENCY = 50
  cnt_angle_cmd = 0

  def setUp(self):
    self.packer = CANPackerPanda("byd_atto3")
    self.safety = libsafety_py.libsafety
    self.safety.set_safety_hooks(CarParams.SafetyModel.byd, 0)
    self.safety.init_tests()
    self.VM = VehicleModel(get_safety_CP())

  def _get_steer_cmd_angle_max(self, speed):
    return min(self.STEER_ANGLE_MAX, get_max_angle_vm(max(speed, 1.0), self.VM, CarControllerParams))

  def test_angle_cmd_when_enabled(self):
    # BYD uses the continuous ISO vehicle-model checks tested below.
    pass

  def test_iso_lateral_accel_and_jerk_limits(self):
    speed = 24.0
    self.safety.set_controls_allowed(True)
    self._reset_speed_measurement(speed + 1.0)

    max_angle = min(self.STEER_ANGLE_MAX, get_max_angle_vm(speed, self.VM, CarControllerParams))
    max_angle = np.floor(max_angle * self.DEG_TO_CAN) / self.DEG_TO_CAN
    self.safety.set_desired_angle_last(round(max_angle * self.DEG_TO_CAN))
    self.assertTrue(self._tx(self._angle_cmd_msg(max_angle, True)))
    self.assertFalse(self._tx(self._angle_cmd_msg(max_angle + 0.2, True)))

    self.safety.set_controls_allowed(True)
    self._reset_speed_measurement(speed + 1.0)
    self.safety.set_desired_angle_last(0)
    max_delta = np.floor(get_max_angle_delta_vm(speed, self.VM, CarControllerParams) * self.DEG_TO_CAN) / self.DEG_TO_CAN
    self.assertTrue(self._tx(self._angle_cmd_msg(max_delta + 0.1, True)))
    self.safety.set_controls_allowed(True)
    self.safety.set_desired_angle_last(0)
    self.assertFalse(self._tx(self._angle_cmd_msg(max_delta + 0.2, True)))

  def _angle_cmd_msg(self, angle: float, enabled: bool, increment_timer: bool = True):
    if increment_timer:
      self.safety.set_timer(self.cnt_angle_cmd * int(1e6 / self.LATERAL_FREQUENCY))
      self.__class__.cnt_angle_cmd += 1
    values = {
      "MPC_SteerAngleRateUpper": 251 if enabled else 0,
      "MPC_SteerAngleRateLower": -252 if enabled else 0,
      "MPC_SteerRequestActiveLow": int(not enabled),
      "MPC_SteerRequest": int(enabled),
      "MPC_E2EAlive1": 1,
      "MPC_E2EAlive2": 1,
      "MPC_SteeringAngleCmd": angle,
      "SET_ME_FF": 0xFF,
      "SET_ME_F": 0xF,
    }
    return self.packer.make_can_msg_panda("A_0x1E2_MPC_Lateral_Cmd_L8_20ms", 0, values)

  def _angle_meas_msg(self, angle: float):
    return self.packer.make_can_msg_panda("B_0x11F_SAS_SensorState_L5_10ms", 0, {"SAS_SteeringAngle": angle})

  def _speed_msg(self, speed: float):
    return self.packer.make_can_msg_panda("B_0x1F0_VCU_ESP_VehSpeed_L8_20ms", 0, {"ESP_VehicleSpeed": speed * 3.6})

  def _speed_msg_2(self, speed: float):
    # BYD exposes one authoritative wheel-speed frame in this safety mode.
    return None

  def _user_brake_msg(self, brake):
    return self.packer.make_can_msg_panda("B_0x242_VCU_DriveState_L8_20ms", 0, {"VCU_BrakePressed": int(bool(brake))})

  def _user_gas_msg(self, gas):
    # VCU_AccelPedalRaw is unscaled (0-255); byd_rx_hook reads the same raw byte
    return self.packer.make_can_msg_panda("B_0x342_VCU_PedalState_L8_20ms", 0, {"VCU_AccelPedalRaw": gas})

  def _pcm_status_msg(self, enabled):
    return self.packer.make_can_msg_panda("B_0x32D_HUD_AdasState_L8_20ms", 2, {"VCU_ACCState": 3 if enabled else 2})

  def test_valid_angle_requires_stock_acc(self):
    self._reset_angle_measurement(0)
    msg = self._angle_cmd_msg(0, True)
    self.assertFalse(self._tx(msg))

    self._rx(self._pcm_status_msg(True))
    self.assertTrue(self.safety.get_controls_allowed())
    self.assertTrue(self._tx(msg))

  def test_steering_frame_constants_and_checksum_are_enforced(self):
    self._reset_angle_measurement(0)
    self._rx(self._pcm_status_msg(True))

    msg = self._angle_cmd_msg(1, True)
    msg[0].data[5] = 0
    self.assertFalse(self._tx(msg))

    msg = self._angle_cmd_msg(0, True)
    msg[0].data[7] ^= 1
    self.assertFalse(self._tx(msg))


if __name__ == "__main__":
  unittest.main()

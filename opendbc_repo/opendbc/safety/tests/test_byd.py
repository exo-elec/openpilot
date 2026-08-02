#!/usr/bin/env python3
import unittest

from opendbc.car.structs import CarParams
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
  # CITY_SPEED_MPS (12) / HIGHWAY_SPEED_MPS (24) breakpoints; see byd.h's
  # BYD_STEERING_LIMITS comment for provenance.
  ANGLE_RATE_BP = [0., 12., 24.]
  ANGLE_RATE_UP = [4., 2., .5]
  ANGLE_RATE_DOWN = [4., 3., 1.5]

  def setUp(self):
    self.packer = CANPackerPanda("byd_atto3")
    self.safety = libsafety_py.libsafety
    self.safety.set_safety_hooks(CarParams.SafetyModel.byd, 0)
    self.safety.init_tests()

  def _angle_cmd_msg(self, angle: float, enabled: bool):
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

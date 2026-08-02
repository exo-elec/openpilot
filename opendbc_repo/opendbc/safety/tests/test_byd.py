#!/usr/bin/env python3
import unittest

from opendbc.car.structs import CarParams
from opendbc.safety.tests.libsafety import libsafety_py
import opendbc.safety.tests.common as common
from opendbc.safety.tests.common import CANPackerPanda


class TestBydSafety(common.PandaCarSafetyTest, common.AngleSteeringSafetyTest):
  TX_MSGS = [[0x1E2, 0], [0x316, 0]]
  RELAY_MALFUNCTION_ADDRS = {0: (0x1E2, 0x316)}
  FWD_BLACKLISTED_ADDRS = {2: [0x1E2, 0x316]}

  STEER_ANGLE_MAX = 390
  STEER_ANGLE_TEST_MAX = 380
  DEG_TO_CAN = 10
  ANGLE_RATE_BP = [0., 5., 15.]
  ANGLE_RATE_UP = [4., 4., 4.]
  ANGLE_RATE_DOWN = [4., 4., 4.]

  def setUp(self):
    self.packer = CANPackerPanda("byd_atto3")
    self.safety = libsafety_py.libsafety
    self.safety.set_safety_hooks(CarParams.SafetyModel.byd, 0)
    self.safety.init_tests()

  def _angle_cmd_msg(self, angle: float, enabled: bool):
    values = {
      "ANGLE_RATE_LIMIT_UPPER": 251 if enabled else 0,
      "ANGLE_RATE_LIMIT_LOWER": -252 if enabled else 0,
      "STEER_REQ_ACTIVE_LOW": int(not enabled),
      "STEER_REQ": int(enabled),
      "E2E_ALIVE_1": 1,
      "E2E_ALIVE_2": 1,
      "STEER_ANGLE": angle,
      "SET_ME_FF": 0xFF,
      "SET_ME_F": 0xF,
    }
    return self.packer.make_can_msg_panda("STEERING_MODULE_ADAS", 0, values)

  def _angle_meas_msg(self, angle: float):
    return self.packer.make_can_msg_panda("STEER_MODULE_2", 0, {"STEER_ANGLE_2": angle})

  def _speed_msg(self, speed: float):
    return self.packer.make_can_msg_panda("WHEELSPEED_CLEAN", 0, {"WHEELSPEED_CLEAN": speed * 3.6})

  def _speed_msg_2(self, speed: float):
    # BYD exposes one authoritative wheel-speed frame in this safety mode.
    return None

  def _user_brake_msg(self, brake):
    return self.packer.make_can_msg_panda("DRIVE_STATE", 0, {"BRAKE_PRESSED": int(bool(brake))})

  def _user_gas_msg(self, gas):
    return self.packer.make_can_msg_panda("PEDAL", 0, {"GAS_PEDAL": gas})

  def _pcm_status_msg(self, enabled):
    return self.packer.make_can_msg_panda("ACC_HUD_ADAS", 2, {"ACC_STATE": 3 if enabled else 2})

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

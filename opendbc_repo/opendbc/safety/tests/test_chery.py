#!/usr/bin/env python3
import unittest

from opendbc.car.structs import CarParams
from opendbc.safety.tests.libsafety import libsafety_py
from opendbc.safety.tests.common import CANPackerPanda as CANPackerSafety


class TestCherySafety(unittest.TestCase):
  """Chery safety: HUD cruise state on bus 2 gates controls_allowed via pcm_cruise_check."""

  # Must match opendbc/safety/modes/chery.h CHERY_TX_MSGS ([addr, bus]).
  # LANE_KEEP, LKAS_INFO(0), LKAS_INFO(2), HUD, EPS(2), PCM(0), PCM(2).
  TX_MSGS = [[0x345, 0], [0x394, 0], [0x394, 2], [0x387, 0], [0x1D3, 2], [0x360, 0], [0x360, 2]]
  SAFETY_PARAM = 0

  def setUp(self):
    self.safety = libsafety_py.libsafety
    self.safety.set_safety_hooks(CarParams.SafetyModel.chery, self.SAFETY_PARAM)
    self.safety.init_tests()
    self.packer = CANPackerSafety("chery_general_pt")

  def _rx(self, msg):
    return self.safety.safety_rx_hook(msg)

  def _tx(self, msg):
    return self.safety.safety_tx_hook(msg)

  def _hud(self, cruise_state: int, bus: int = 2):
    return self.packer.make_can_msg_panda(
      "HUD",
      bus,
      {
        "AEB": 0,
        "CANCEL_CRUISE_UNCERTAIN": 0,
        "GAS_RESUME_UNCERTAIN": 0,
        "FOLLOW_DISTANCE": 1,
        "NEW_SIGNAL_1": 0,
        "PCW": 0,
        "CRUISE_STATE": cruise_state,
        "GAS_OVERRIDE": 0,
        "AEB_RELATED": 0,
        "SET_SPEED": 80,
      },
    )

  def _lane_keep(self, steer_cmd_deg: float, lkas_enable: int):
    return self.packer.make_can_msg_panda(
      "LANE_KEEP",
      0,
      {
        "STEER_CMD_ANGLE": steer_cmd_deg,
        "LKAS_ENABLE": lkas_enable,
        "SET_ME_XFF": 255,
        "SET_ME_XFC": 252,
        "SET_ME_XF4": 244,
        "SET_ME_X63": 99,
        "SET_ME_XF": 15,
      },
    )

  def test_controls_allowed_follows_hud_cruise_state(self):
    self.assertFalse(self.safety.get_controls_allowed())
    self._rx(self._hud(cruise_state=1))
    self.assertFalse(self.safety.get_controls_allowed())
    self._rx(self._hud(cruise_state=3))
    self.assertTrue(self.safety.get_controls_allowed())
    self._rx(self._hud(cruise_state=3))
    self.assertTrue(self.safety.get_controls_allowed())
    self._rx(self._hud(cruise_state=1))
    self.assertFalse(self.safety.get_controls_allowed())

  def test_lane_keep_tx_allowed_when_whitelisted(self):
    """LANE_KEEP requires controls_allowed when steer_req=1; inactive frames stay allowed."""
    self.safety.set_controls_allowed(False)
    self.assertTrue(self._tx(self._lane_keep(0.0, 0)))
    self.assertFalse(self._tx(self._lane_keep(1.0, 1)))
    self.safety.set_controls_allowed(True)
    self.assertTrue(self._tx(self._lane_keep(1.0, 1)))

  def _lkas_info(self, main_torque: float, lkas_enable: int):
    return self.packer.make_can_msg_panda(
      "LKAS_INFO",
      0,
      {
        "MAIN_TORQUE": main_torque,
        "LKAS_ENABLE": lkas_enable,
        "STEER_RELATED": 0.0,
      },
    )

  def test_lkas_info_tx_allowed_when_whitelisted(self):
    no_spoof = bool(self.SAFETY_PARAM & 2)
    self.safety.set_controls_allowed(False)
    self.assertEqual(not no_spoof, self._tx(self._lkas_info(50.0, 1)))
    self.safety.set_controls_allowed(True)
    self.assertEqual(not no_spoof, self._tx(self._lkas_info(0.0, 0)))

  def test_pcm_buttons_tx_allowed_when_whitelisted(self):
    is_icaur = bool(self.SAFETY_PARAM & 4)
    for bus in (0, 2):
      msg = self.packer.make_can_msg_panda("PCM_BUTTONS", bus, {"RES_BUTTON": 1, "COUNTER": 0})
      self.safety.set_controls_allowed(False)
      self.assertEqual(not is_icaur, self._tx(msg))
    # Moving vehicle must block PCM_BUTTONS injection on every Chery variant.
    if is_icaur:
      self._rx(self.packer.make_can_msg_panda("ICAUR_WHEELSPEED_A", 0, {"WHEEL_FL": 100, "WHEEL_FR": 100}))
    else:
      self._rx(self._wheel_speed(30.0, 30.0))
    msg = self.packer.make_can_msg_panda("PCM_BUTTONS", 0, {"RES_BUTTON": 1, "COUNTER": 0})
    self.assertFalse(self._tx(msg))

  def test_pt_bus_stock_adas_does_not_trigger_relay_malfunction(self):
    """LKAS_INFO and LANE_KEEP on bus 0 are expected on Jaecoo PT; must not trip relayMalfunction."""
    self.assertFalse(self.safety.get_relay_malfunction())
    self._rx(self._lkas_info(0.0, 0))
    self.assertFalse(self.safety.get_relay_malfunction())
    self._rx(self._lane_keep(0.0, 0))
    self.assertFalse(self.safety.get_relay_malfunction())
    self.safety.set_controls_allowed(True)
    self.assertTrue(self._tx(self._lane_keep(1.0, 1)))
    self._rx(self._lane_keep(1.0, 1))
    self.assertFalse(self.safety.get_relay_malfunction())

  def test_hud_tx_allowed_when_whitelisted(self):
    msg = self.packer.make_can_msg_panda(
      "HUD", 0,
      {
        "AEB": 0,
        "HANDS_ON_WHEEL_STEER": 0,
        "CANCEL_CRUISE_UNCERTAIN": 0,
        "GAS_RESUME_UNCERTAIN": 0,
        "FOLLOW_DISTANCE": 1,
        "NEW_SIGNAL_1": 0,
        "PCW": 0,
        "CRUISE_STATE": 3,
        "GAS_OVERRIDE": 0,
        "AEB_RELATED": 0,
        "SET_SPEED": 80,
        "COUNTER": 0,
      },
    )
    self.safety.set_controls_allowed(False)
    is_jaecoo = not (self.SAFETY_PARAM & (1 | 4))
    self.assertEqual(is_jaecoo, self._tx(msg))

  def test_chery_checksums_are_wired(self):
    # LANE_KEEP uses chery_checksum (CRC8J1850 ^ 0x0A) in byte 7; PCM_BUTTONS uses
    # chery_pcm_buttons_checksum in byte 0. Neither should stay 0x00.
    lk = self._lane_keep(5.0, 1)
    self.assertNotEqual(0, lk[0].data[7])
    pcm = self.packer.make_can_msg_panda("PCM_BUTTONS", 0, {"RES_BUTTON": 1, "COUNTER": 2})
    self.assertNotEqual(0, pcm[0].data[0])

  def test_brake_pedal_disengages_controls(self):
    is_omoda = bool(self.SAFETY_PARAM & 1)
    is_icaur = bool(self.SAFETY_PARAM & 4)
    # Must be moving so generic_rx_checks revokes controls_allowed on brake press.
    if is_icaur:
      self._rx(self.packer.make_can_msg_panda("ICAUR_WHEELSPEED_A", 0, {"WHEEL_FL": 200, "WHEEL_FR": 200}))
    else:
      self._rx(self._wheel_speed(30.0, 30.0))
    self._rx(self._hud(3, bus=0 if is_omoda else 2))
    self.assertTrue(self.safety.get_controls_allowed())
    if is_icaur:
      self._rx(self.packer.make_can_msg_panda("ICAUR_BRAKE", 0, {"BRAKE_PRESSURE": 0.25}))
    elif is_omoda:
      self._rx(self.packer.make_can_msg_panda("OMODA_BRAKE", 0, {"BRAKE_PRESSURE": 10 * 0.0188679}))
    else:
      self._rx(self.packer.make_can_msg_panda("BRAKE_PEDAL", 0, {"BRAKE_PRESSURE": 0.2}))
    self.assertFalse(self.safety.get_controls_allowed())

  def test_steering_rate_and_zone_angle_limits(self):
    # At 0 m/s, CHERY_STEERING_LIMITS allows 1.0 deg/20ms (+0.1 tol); 2.0 deg single-frame jump must fail.
    self.safety.set_controls_allowed(True)
    self.safety.set_desired_angle_last(0)
    self.assertTrue(self._tx(self._lane_keep(1.0, 1)))
    self.safety.set_desired_angle_last(0)
    self.assertFalse(self._tx(self._lane_keep(2.0, 1)))

    # At highway speed (36 m/s), active zone angle is 23.7 deg, while inactive manual steering
    # at 45 deg (steer_req=0) must remain allowed.
    is_icaur = bool(self.SAFETY_PARAM & 4)
    for _ in range(6):
      if is_icaur:
        self._rx(self.packer.make_can_msg_panda("ICAUR_WHEELSPEED_A", 0, {"WHEEL_FL": 2050, "WHEEL_FR": 2050}))
        self._rx(self.packer.make_can_msg_panda("STEER_RELATED", 0, {"STEERING_ANGLE": 45.0}))
      else:
        # J7/Omoda wheelSpeedFactor=0.832 -> 156 kph raw ~= 36 m/s
        self._rx(self._wheel_speed(156.0, 156.0))
        self._rx(self.packer.make_can_msg_panda("EPS", 0, {"STEERING_ANGLE": 45.0}))
    self.safety.set_controls_allowed(False)
    self.assertTrue(self._tx(self._lane_keep(45.0, 0)))
    self.safety.set_controls_allowed(True)
    self.safety.set_desired_angle_last(450)
    self.assertFalse(self._tx(self._lane_keep(45.0, 1)))

  def test_fwd_blocks_camera_lane_keep(self):
    # Camera bus -> PT bus blocks: LANE_KEEP, LKAS_INFO, HUD.
    for addr in (0x345, 0x394, 0x387):
      self.assertEqual(-1, self.safety.safety_fwd_hook(2, addr),
                       msg=f"fwd not blocked for cam addr 0x{addr:x}")
    # LKA_STATUS (0x3a5) must be forwarded so the cluster gets LKA-engaged status.
    self.assertEqual(0, self.safety.safety_fwd_hook(2, 0x3A5))
    # PT bus -> cam bus passthrough (destination bus 2) for an addr we don't TX-whitelist.
    self.assertEqual(2, self.safety.safety_fwd_hook(0, 0x999))

  def _eps(self, steering_angle_deg: float, driver_torque: int):
    return self.packer.make_can_msg_panda(
      "EPS", 2,
      {
        "STEERING_ANGLE": steering_angle_deg,
        "DRIVER_TORQUE": driver_torque,
        "COUNTER": 0,
      },
    )

  def test_eps_spoof_tx_allowed_on_cam_bus(self):
    no_spoof = bool(self.SAFETY_PARAM & 2)
    self.safety.set_controls_allowed(False)
    self.assertEqual(not no_spoof, self._tx(self._eps(0.0, 5)))
    self.safety.set_controls_allowed(True)
    self.assertEqual(not no_spoof, self._tx(self._eps(15.0, 5)))

  def _wheel_speed(self, fl_kph: float, fr_kph: float):
    return self.packer.make_can_msg_panda(
      "WHEELSPEED_2", 0,
      {"WHEEL_FL": fl_kph, "WHEEL_FR": fr_kph},
    )

  def test_fwd_blocks_pt_torque_when_engaged(self):
    """Per-message PT->cam blocking matches the spoof loop's gating exactly.

    EPS  (0x1D3): blocked whenever cruise engaged OR vehicle stopped (passthrough is
                  byte-identical to stock so blocking while stopped is safe).
    LKAS (0x394): blocked only while cruise is engaged.
    STEER_RELATED (0xC4): never blocked — cam watchdog cancels LKAS otherwise.
    """
    self._rx(self._wheel_speed(30.0, 30.0))
    self.safety.set_controls_allowed(False)
    for addr in (0x394, 0x1D3, 0x0C4):
      self.assertEqual(2, self.safety.safety_fwd_hook(0, addr),
                       msg=f"PT 0x{addr:x} should fwd to cam when moving and disengaged")

    self._rx(self._wheel_speed(0.0, 0.0))
    self.assertEqual(-1, self.safety.safety_fwd_hook(0, 0x1D3),
                     msg="EPS should be blocked PT->cam when stopped (passthrough TX takes over)")
    self.assertEqual(2, self.safety.safety_fwd_hook(0, 0x394),
                     msg="LKAS_INFO must fwd while stopped/disengaged")
    self.assertEqual(2, self.safety.safety_fwd_hook(0, 0x0C4))

    self._rx(self._wheel_speed(30.0, 30.0))
    self.safety.set_controls_allowed(True)
    for addr in (0x394, 0x1D3):
      self.assertEqual(-1, self.safety.safety_fwd_hook(0, addr),
                       msg=f"PT 0x{addr:x} should be blocked PT->cam when engaged")
    self.assertEqual(2, self.safety.safety_fwd_hook(0, 0x0C4),
                     msg="STEER_RELATED must always forward")


class TestCheryOmodaSafety(TestCherySafety):
  SAFETY_PARAM = 1

  def test_fwd_blocks_camera_lane_keep(self):
    # Omoda leaves native HUD forwarding cam -> PT (see chery_fwd_hook's
    # "Omoda/iCaur: leave native HUD" comment); only LANE_KEEP/LKAS_INFO block.
    self.assertEqual(-1, self.safety.safety_fwd_hook(2, 0x345))
    self.assertEqual(-1, self.safety.safety_fwd_hook(2, 0x394))
    self.assertEqual(0, self.safety.safety_fwd_hook(2, 0x387))

  def test_controls_allowed_follows_hud_cruise_state(self):
    self.assertFalse(self.safety.get_controls_allowed())
    self._rx(self._hud(1, 0))
    self.assertFalse(self.safety.get_controls_allowed())
    self._rx(self._hud(3, 0))
    self.assertTrue(self.safety.get_controls_allowed())
    self._rx(self._hud(3, 2))
    self.assertTrue(self.safety.get_controls_allowed())
    self._rx(self._hud(1, 0))
    self.assertFalse(self.safety.get_controls_allowed())


class TestCheryOmodaNoTorqueSpoofSafety(TestCheryOmodaSafety):
  SAFETY_PARAM = 3

  def test_fwd_blocks_camera_lane_keep(self):
    self.assertEqual(-1, self.safety.safety_fwd_hook(2, 0x345))
    self.assertEqual(0, self.safety.safety_fwd_hook(2, 0x394))
    self.assertEqual(0, self.safety.safety_fwd_hook(2, 0x387))
    self.assertEqual(0, self.safety.safety_fwd_hook(2, 0x3A5))

  def test_fwd_blocks_pt_torque_when_engaged(self):
    self.test_fwd_allows_pt_torque_when_spoof_disabled()

  def test_fwd_allows_pt_torque_when_spoof_disabled(self):
    self._rx(self._wheel_speed(0.0, 0.0))
    self.safety.set_controls_allowed(False)
    for addr in (0x394, 0x1D3):
      self.assertEqual(2, self.safety.safety_fwd_hook(0, addr),
                       msg=f"PT 0x{addr:x} should fwd to cam when spoof disabled")

    self._rx(self._wheel_speed(30.0, 30.0))
    self.safety.set_controls_allowed(True)
    for addr in (0x394, 0x1D3):
      self.assertEqual(2, self.safety.safety_fwd_hook(0, addr),
                       msg=f"PT 0x{addr:x} should fwd to cam when spoof disabled")

  def test_fwd_allows_cam_lkas_to_pt_when_spoof_disabled(self):
    self.assertEqual(0, self.safety.safety_fwd_hook(2, 0x394))
    self.assertEqual(-1, self.safety.safety_fwd_hook(2, 0x345))
    self.assertEqual(0, self.safety.safety_fwd_hook(2, 0x387))


class TestCheryIcaurSafety(TestCherySafety):
  """iCaur 03: standstill from 0x222; HUD forwards like Omoda (no override)."""

  SAFETY_PARAM = 4

  def _icaur_wheel_speed(self, fl_raw: int, fr_raw: int):
    return self.packer.make_can_msg_panda(
      "ICAUR_WHEELSPEED_A", 0,
      {
        "WHEEL_FL": fl_raw,
        "WHEEL_FR": fr_raw,
      },
    )

  def test_fwd_blocks_camera_lane_keep(self):
    # LANE_KEEP still blocked/replaced; HUD + LKAS_INFO (with torque spoof on in this
    # test param) follow Jaecoo torque path for LKAS, but HUD must forward (Omoda-style).
    self.assertEqual(-1, self.safety.safety_fwd_hook(2, 0x345))
    self.assertEqual(-1, self.safety.safety_fwd_hook(2, 0x394))  # param 4: spoof enabled
    self.assertEqual(0, self.safety.safety_fwd_hook(2, 0x387),
                     msg="iCaur must forward cam HUD to PT (no HUD override)")
    self.assertEqual(0, self.safety.safety_fwd_hook(2, 0x3A5))

  def test_fwd_blocks_pt_torque_when_engaged(self):
    # 13-bit raw: 960 ↔ former 8-bit byte0=30 (high 8 bits); must leave byte0 >= 15.
    self._rx(self._icaur_wheel_speed(960, 960))
    self.safety.set_controls_allowed(False)
    for addr in (0x394, 0x1D3, 0x0C4):
      self.assertEqual(2, self.safety.safety_fwd_hook(0, addr),
                       msg=f"PT 0x{addr:x} should fwd to cam when moving and disengaged")

    self._rx(self._icaur_wheel_speed(0, 0))
    self.assertEqual(-1, self.safety.safety_fwd_hook(0, 0x1D3),
                     msg="EPS should be blocked PT->cam when stopped")
    self.assertEqual(2, self.safety.safety_fwd_hook(0, 0x394))
    self.assertEqual(2, self.safety.safety_fwd_hook(0, 0x0C4))

    self._rx(self._icaur_wheel_speed(960, 960))
    self.safety.set_controls_allowed(True)
    for addr in (0x394, 0x1D3):
      self.assertEqual(-1, self.safety.safety_fwd_hook(0, addr),
                       msg=f"PT 0x{addr:x} should be blocked PT->cam when engaged")
    self.assertEqual(2, self.safety.safety_fwd_hook(0, 0x0C4))


class TestCheryIcaurNoTorqueSpoofSafety(TestCheryIcaurSafety):
  """Matches production iCaur safetyParam (ICAUR | NO_TORQUE_SPOOF)."""

  SAFETY_PARAM = 4 | 2  # CHERY_ICAUR_SAFETY_PARAM | CHERY_OMODA_NO_TORQUE_SPOOF_PARAM

  def test_fwd_blocks_camera_lane_keep(self):
    self.assertEqual(-1, self.safety.safety_fwd_hook(2, 0x345))
    self.assertEqual(0, self.safety.safety_fwd_hook(2, 0x394))
    self.assertEqual(0, self.safety.safety_fwd_hook(2, 0x387))
    self.assertEqual(0, self.safety.safety_fwd_hook(2, 0x3A5))

  def test_fwd_blocks_pt_torque_when_engaged(self):
    self._rx(self._icaur_wheel_speed(0, 0))
    self.safety.set_controls_allowed(True)
    for addr in (0x394, 0x1D3, 0x0C4):
      self.assertEqual(2, self.safety.safety_fwd_hook(0, addr),
                       msg=f"PT 0x{addr:x} should fwd when torque spoof disabled")


if __name__ == "__main__":
  unittest.main()

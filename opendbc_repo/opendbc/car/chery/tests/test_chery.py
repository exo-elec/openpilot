from opendbc.can import CANPacker
from opendbc.car import gen_empty_fingerprint, structs
from opendbc.car.chery.interface import CarInterface
from opendbc.car.chery.values import CAR
from opendbc.safety.tests.libsafety import libsafety_py


def test_chery_platforms_disable_unparsed_bsm_and_emit_valid_crc():
  packer = CANPacker("chery_general_pt")
  for platform in (CAR.CHERY_JAECOO_J7_PHEV, CAR.CHERY_OMODA_5, CAR.CHERY_ICAUR_03):
    cp = CarInterface.get_params(str(platform), gen_empty_fingerprint(), [], False, False, 0, False)
    assert not cp.enableBsm, f"{platform} must keep enableBsm=False until BSM_LEFT/RIGHT are parsed"

    ci = CarInterface(cp)
    safety = libsafety_py.libsafety
    safety.set_safety_hooks(cp.safetyConfigs[0].safetyModel.raw, cp.safetyConfigs[0].safetyParam)
    safety.init_tests()
    safety.set_controls_allowed(True)

    cc = structs.CarControl()
    cc.enabled = True
    cc.latActive = True
    cc.actuators.steeringAngleDeg = 0.5
    cc_reader = cc.as_reader()

    ci.update([])
    ci.CS.out.vEgo = 10.0
    ci.CS.out.standstill = False
    ci.CS.out.cruiseState.enabled = True
    ci.CS.out.steeringAngleDeg = 0.0
    _, sends = ci.apply(cc_reader, 0)
    assert sends
    for addr, dat, bus in sends:
      if addr == 0x345:  # LANE_KEEP
        assert dat[7] != 0, "Chery CRC-8 checksum must be wired in opendbc.can.dbc"
      msg = libsafety_py.make_CANPacket(addr, bus, dat)
      assert safety.safety_tx_hook(msg), (platform, hex(addr), bus, list(dat))

  # Verify PCM_BUTTONS uses chery_pcm_buttons_checksum in byte 0
  _, pcm_dat, _ = packer.make_can_msg("PCM_BUTTONS", 0, {"RES_BUTTON": 1, "COUNTER": 2})
  assert pcm_dat[0] != 0


def test_chery_inactive_lane_keep_clamps_parking_turns_and_stationary_steer_dir():
  cp = CarInterface.get_params(str(CAR.CHERY_ICAUR_03), gen_empty_fingerprint(), [], False, False, 0, False)
  ci = CarInterface(cp)
  packer = CANPacker("chery_general_pt")

  # Quantized-flat steering angle must produce steer_dir=0 (no right-turn directional bias).
  steer_msg = packer.make_can_msg("STEER_RELATED", 0, {"STEERING_ANGLE": 15.0, "DRIVER_TORQUE": 15})
  ci.update([(0, [(steer_msg[0], steer_msg[1], 0)])])
  cs2 = ci.update([(0, [(steer_msg[0], steer_msg[1], 0)])])
  assert cs2.steeringTorqueEps == 0.0

  # Parking turn past 120 deg while disengaged must be clamped to +/-120 deg in LANE_KEEP.
  ci.CS.out.steeringAngleDeg = 180.0
  ci.CS.out.standstill = True
  cc_off = structs.CarControl().as_reader()
  new_act, sends = ci.apply(cc_off, 0)
  assert abs(new_act.steeringAngleDeg) <= 120.0
  assert any(addr == 0x345 for addr, _, _ in sends)

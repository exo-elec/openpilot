from opendbc.can import CANPacker, CANParser
from opendbc.car import gen_empty_fingerprint, structs
from opendbc.car.byd.fingerprints import FINGERPRINTS, FW_VERSIONS
from opendbc.car.byd.interface import CarInterface
from opendbc.car.byd.values import CAR, FW_QUERY_CONFIG


def test_platform_is_passive():
  cp = CarInterface.get_params(str(CAR.BYD_ATTO_3), gen_empty_fingerprint(), [], False, False, 0, False)
  assert cp.dashcamOnly
  assert cp.safetyConfigs[0].safetyModel == structs.CarParams.SafetyModel.noOutput
  assert not cp.alphaLongitudinalAvailable
  assert not cp.openpilotLongitudinalControl
  assert cp.radarUnavailable

  ci = CarInterface(cp)
  _, sends = ci.apply(structs.CarControl().as_reader())
  assert sends == []


def test_reference_evidence_registered():
  assert len(FINGERPRINTS[CAR.BYD_ATTO_3]) == 1
  assert len(FINGERPRINTS[CAR.BYD_ATTO_3][0]) == 109
  assert len(FW_VERSIONS[CAR.BYD_ATTO_3]) == 6
  assert FW_QUERY_CONFIG.requests[0].bus == 0


def test_dbc_messages_and_checksum():
  parser = CANParser("byd_atto3", [], 0)
  required = (
    "B_0x1F0_VCU_ESP_VehSpeed_L8_20ms", "B_0x11F_SAS_SensorState_L5_10ms", "B_0x1FC_EPS_MotorState_L8_20ms",
    "B_0x242_VCU_DriveState_L8_20ms", "B_0x342_VCU_PedalState_L8_20ms", "B_0x133_BCM_StalkState_L8_50ms",
    "B_0x418_VCU_BsdState_L8_50ms", "B_0x294_BCM_CabinState_L8_50ms", "B_0x32D_HUD_AdasState_L8_20ms",
    "A_0x32E_MPC_Long_Cmd_L8_20ms", "A_0x316_MPC_MpcState_L8_20ms", "A_0x1E2_MPC_Lateral_Cmd_L8_20ms",
  )
  assert all(name in parser.dbc.name_to_msg for name in required)

  packer = CANPacker("byd_atto3")
  msg = packer.make_can_msg("A_0x1E2_MPC_Lateral_Cmd_L8_20ms", 0, {
    "MPC_SteerRequestActiveLow": 1,
    "MPC_E2EAlive1": 1,
    "MPC_E2EAlive2": 1,
    "SET_ME_FF": 0xFF,
    "SET_ME_F": 0xF,
    "COUNTER": 7,
  })
  assert msg[0] == 0x1E2
  assert msg[1][7] == ((~sum(msg[1][:7])) & 0xFF)


def _lateral_cmd_msg(packer, angle, active, counter):
  values = {
    "MPC_SteerAngleRateUpper": 251 if active else 0,
    "MPC_SteerAngleRateLower": -252 if active else 0,
    "MPC_SteerRequestActiveLow": int(not active),
    "MPC_SteerRequest": int(active),
    "MPC_E2EAlive1": 1,
    "MPC_E2EAlive2": 1,
    "MPC_SteeringAngleCmd": angle,
    "SET_ME_FF": 0xFF,
    "SET_ME_F": 0xF,
    "COUNTER": counter,
  }
  return packer.make_can_msg("A_0x1E2_MPC_Lateral_Cmd_L8_20ms", 0, values)


def test_lateral_cmd_matches_firmware_wire_layout():
  # Byte-level cross-check against the real, car-tested firmware's WriteRaw
  # sequence for 0x1E2 (tc275_freertos/TC275_BrownPanda DBC/byd_atto3.c,
  # lines 1173-1201). This is the only check that would have caught the
  # COUNTER (55->52) and Motorola->Intel bit-numbering fixes made to
  # opendbc/dbc/byd_atto3.dbc: opendbc/safety/tests/test_byd.py only exercises
  # the fields opendbc/safety/modes/byd.h reads directly (bits 20-23, the
  # angle, data[5]/data[6] constants, and the checksum byte); it never reads
  # COUNTER, so a wrong counter position was invisible to that suite.
  packer = CANPacker("byd_atto3")

  for active in (True, False):
    addr, dat, bus = _lateral_cmd_msg(packer, angle=12.3, active=active, counter=7)
    assert addr == 0x1E2
    assert bus == 0

    # steer_req (bit 21) / steer_req_active_low (bit 20) are mutually exclusive
    assert bool(dat[2] & (1 << 5)) == active           # bit 21 -> byte 2, bit 5
    assert bool(dat[2] & (1 << 4)) == (not active)      # bit 20 -> byte 2, bit 4
    # E2E alive bits (22, 23) are always set
    assert dat[2] & (1 << 6)
    assert dat[2] & (1 << 7)
    # fixed sentinel bytes the EPS module requires unconditionally
    assert dat[5] == 0xFF
    assert dat[6] & 0x0F == 0x0F
    # counter occupies the high nibble of byte 6 (bits 52-55)
    assert (dat[6] >> 4) == 7
    # checksum is the inverted sum of the first 7 bytes
    assert dat[7] == ((~sum(dat[:7])) & 0xFF)

  # angle rate fields (bits 0-9, 10-19) are fixed sentinels, not a computed
  # rate: 251 / -252 while active, 0 / 0 while inactive
  _, active_dat, _ = _lateral_cmd_msg(packer, angle=0, active=True, counter=0)
  active_rate_upper = active_dat[0] | ((active_dat[1] & 0x03) << 8)
  assert active_rate_upper == 251
  _, idle_dat, _ = _lateral_cmd_msg(packer, angle=0, active=False, counter=0)
  idle_rate_upper = idle_dat[0] | ((idle_dat[1] & 0x03) << 8)
  assert idle_rate_upper == 0

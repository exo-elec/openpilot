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

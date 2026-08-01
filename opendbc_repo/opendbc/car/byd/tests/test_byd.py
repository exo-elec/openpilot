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
    "WHEELSPEED_CLEAN", "STEER_MODULE_2", "STEERING_TORQUE", "DRIVE_STATE",
    "PEDAL", "STALKS", "BSD_RADAR", "METER_CLUSTER", "ACC_HUD_ADAS",
    "ACC_CMD", "LKAS_HUD_ADAS", "STEERING_MODULE_ADAS",
  )
  assert all(name in parser.dbc.name_to_msg for name in required)

  packer = CANPacker("byd_atto3")
  msg = packer.make_can_msg("STEERING_MODULE_ADAS", 0, {
    "STEER_REQ_ACTIVE_LOW": 1,
    "E2E_ALIVE_1": 1,
    "E2E_ALIVE_2": 1,
    "SET_ME_FF": 0xFF,
    "SET_ME_F": 0xF,
    "COUNTER": 7,
  })
  assert msg[0] == 0x1E2
  assert msg[1][7] == ((~sum(msg[1][:7])) & 0xFF)

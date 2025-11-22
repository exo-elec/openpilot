import pytest

from opendbc.can.packer import CANPacker
from opendbc.car import Bus
from opendbc.car.gateway import gatewaycan
from opendbc.car.gateway.values import CAR, DBC


class DummyCC:
  def __init__(self, lat_active=True, long_active=True):
    self.latActive = lat_active
    self.longActive = long_active


def _checksum_last_byte(dat: bytes) -> int:
  return gatewaycan.inverted_sum_checksum(dat[:7])


@pytest.mark.parametrize("model,lat_msg_name", [
  (CAR.BYD_ATTO3, "A_0x1E2_MPC_Lateral_Cmd_L8_20ms"),
  (CAR.BYD_DOLPHIN, "A_0x1E2_M2E_Lateral_Cmd_L8_20ms"),
])
def test_create_lat_command_checksum_and_bus(model, lat_msg_name):
  assert gatewaycan.get_model_messages(model).lat_cmd == lat_msg_name
  packer = CANPacker(DBC[model][Bus.pt])
  CAN = gatewaycan.CanBus()

  addr, dat, bus = gatewaycan.create_lat_command(
    model, packer, CAN, DummyCC(lat_active=True), steering_angle_deg=1.5, counter=3,
    stock_values=None
  )

  assert addr == 0x1E2
  assert bus == CAN.pt
  assert dat[-1] == _checksum_last_byte(dat)


@pytest.mark.parametrize("model,long_msg_name", [
  (CAR.BYD_ATTO3, "A_0x32E_MPC_Long_Cmd_L8_50ms"),
  (CAR.BYD_DOLPHIN, "A_0x32E_M2V_Long_Cmd_L8_50ms"),
])
def test_create_long_command_checksum_and_counter(model, long_msg_name):
  assert gatewaycan.get_model_messages(model).long_cmd == long_msg_name
  packer = CANPacker(DBC[model][Bus.pt])
  CAN = gatewaycan.CanBus()

  addr, dat, bus = gatewaycan.create_long_command(
    model, packer, CAN, accel=0.5, enabled=True, lead_distance=30.0, counter=5,
    stock_values=None
  )

  assert addr == 0x32E
  assert bus == CAN.pt
  assert dat[-1] == _checksum_last_byte(dat)

"""Simulated vehicle for EOP vehicled.

EOP standardizes on Tesla DBC for all vehicle types. This simulator publishes
Tesla-format CAN messages regardless of the selected VehicleType (which only
affects physics tuning, not the CAN protocol).
"""
import traceback
import cereal.messaging as messaging

from openpilot.common.params import Params
from openpilot.system.socketd import can_list_to_can_capnp
from openpilot.tools.sim.lib.common import SimulatorState
from openpilot.system.socketd.vehicle.protocol.packer import CANPacker
from openpilot.system.socketd.vehicle.tesla.values import CANBUS


class SimulatedCar:
  """Simulates a vehicle to EOP's vehicled daemon using the standard Tesla DBC protocol."""

  def __init__(self):
    self.pm = messaging.PubMaster(['can', 'pandaStates'])
    self.sm = messaging.SubMaster(['carControl', 'controlsState', 'carParams', 'selfdriveState'])
    self.idx = 0
    self.params = Params()
    self.obd_multiplexing = False
    self._counter = 0
    self.packer = CANPacker("tesla_model3_party")

  @staticmethod
  def _set_value(data: bytearray, start_bit: int, size: int, value: int, little_endian: bool = True) -> None:
    """Set a raw integer value in CAN data bytes at the given start bit."""
    value &= (1 << size) - 1  # Mask to valid bit width
    if little_endian:
      for i in range(size):
        bit = start_bit + i
        byte_idx = bit // 8
        bit_idx = bit % 8
        if value & (1 << i):
          data[byte_idx] |= (1 << bit_idx)
        else:
          data[byte_idx] &= ~(1 << bit_idx)
    else:
      # Motorola big-endian snake pattern (descend within byte, jump to MSB of next byte)
      bit = start_bit
      for i in range(size):
        byte_idx = bit // 8
        bit_idx = bit % 8
        if value & (1 << (size - 1 - i)):
          data[byte_idx] |= (1 << bit_idx)
        else:
          data[byte_idx] &= ~(1 << bit_idx)
        if bit_idx == 0:
          bit = (byte_idx + 1) * 8 + 7
        else:
          bit -= 1

  def _pack_speed(self, speed_kph: float) -> bytes:
    """DI_speed (0x257) - Vehicle speed in kph with valid Tesla counter & checksum."""
    return bytes(self.packer.pack(0x257, {"DI_vehicleSpeed": max(-40.0, min(287.6, float(speed_kph)))}))

  def _pack_system_status(self, gear: int, accel_pedal: float) -> bytes:
    """DI_systemStatus (0x118) - Gear, accel pedal, brake state, system state with valid Tesla checksum."""
    return bytes(self.packer.pack(0x118, {
      "DI_accelPedalPos": max(0.0, min(100.0, float(accel_pedal))),
      "DI_gear": int(gear),
      "DI_brakePedalState": 0,
      "DI_systemState": 4,
    }))

  def _pack_brake_status(self, braking: bool) -> bytes:
    """ESP_status (0x145) - Brake apply with valid Tesla counter & checksum."""
    return bytes(self.packer.pack(0x145, {"ESP_driverBrakeApply": 2 if braking else 0}))

  def _pack_esp_b(self, speed_kph: float) -> bytes:
    """ESP_B (0x155) - Standstill status with valid Tesla counter & checksum."""
    return bytes(self.packer.pack(0x155, {
      "ESP_vehicleSpeed": max(0.0, float(speed_kph)),
      "ESP_vehicleStandstillSts": 1 if speed_kph < 0.2 else 0,
    }))

  def _pack_epas(self, steering_angle: float, torque: float) -> bytes:
    """EPAS3S_sysStatus (0x370) - Steering angle and torque with valid Tesla counter & checksum."""
    return bytes(self.packer.pack(0x370, {
      "EPAS3S_internalSAS": max(-819.2, min(819.1, float(steering_angle))),
      "EPAS3S_torsionBarTorque": max(-20.5, min(20.47, float(torque))),
      "EPAS3S_handsOnLevel": 0,
      "EPAS3S_eacStatus": 1,  # 1=ACTIVE
    }))

  def _pack_di_state(self, cruise_state: int, digital_speed: float) -> bytes:
    """DI_state (0x286) - Cruise state, speed units, digital speed, autopark, park brake with valid Tesla checksum."""
    return bytes(self.packer.pack(0x286, {
      "DI_cruiseState": int(cruise_state),
      "DI_speedUnits": 0,  # 0=KPH
      "DI_digitalSpeed": max(0.0, min(255.5, float(digital_speed))),
      "DI_autoparkState": 0,
      "DI_parkBrakeState": 1,
    }))

  def _pack_ui_warning(self, left_blinker: bool, right_blinker: bool, seatbelt: bool) -> bytes:
    """UI_warning (0x311) - Doors, blinkers, seatbelt with valid Tesla counter & checksum."""
    return bytes(self.packer.pack(0x311, {
      "anyDoorOpen": 0,
      "leftBlinkerBlinking": 1 if left_blinker else 0,
      "rightBlinkerBlinking": 1 if right_blinker else 0,
      "buckleStatus": 1 if seatbelt else 0,
    }))

  def _pack_sccm(self, steering_rate: float) -> bytes:
    """SCCM_steeringAngleSensor (0x129) - Steering angle speed with valid Tesla counter & CRC."""
    return bytes(self.packer.pack(0x129, {
      "SCCM_steeringAngleSpeed": max(-4096.0, min(4095.5, float(steering_rate))),
      "SCCM_steeringAngleValidity": 1,
    }))

  def _pack_das_status(self) -> bytes:
    """DAS_status (0x39B) - Blindspot (inactive in sim) with valid Tesla counter & checksum."""
    return bytes(self.packer.pack(0x39B, {
      "DAS_blindSpotRearLeft": 0,
      "DAS_blindSpotRearRight": 0,
    }))

  def send_can_messages(self, simulator_state: SimulatorState):
    if not simulator_state.valid:
      return

    speed_kph = simulator_state.speed * 3.6
    msg = []

    # Party bus (0) - vehicle sensors
    cruise_state = 2 if (simulator_state.is_engaged or simulator_state.cruise_button > 0) else 1
    msg.append((0x257, self._pack_speed(speed_kph), CANBUS.party))
    msg.append((0x118, self._pack_system_status(gear=4, accel_pedal=simulator_state.user_gas * 100), CANBUS.party))
    msg.append((0x145, self._pack_brake_status(simulator_state.user_brake > 0), CANBUS.party))
    msg.append((0x155, self._pack_esp_b(speed_kph), CANBUS.party))
    msg.append((0x370, self._pack_epas(simulator_state.steering_angle, simulator_state.user_torque / 100.0), CANBUS.party))
    msg.append((0x286, self._pack_di_state(cruise_state=cruise_state, digital_speed=speed_kph), CANBUS.party))
    msg.append((0x311, self._pack_ui_warning(simulator_state.left_blinker, simulator_state.right_blinker, seatbelt=True), CANBUS.party))
    # Autopilot party bus (2) - SCCM + DAS messages expected by CarState
    msg.append((0x129, self._pack_sccm(0.0), CANBUS.autopilot_party))
    msg.append((0x39B, self._pack_das_status(), CANBUS.autopilot_party))
    msg.append(self.packer.make_can_msg("DAS_control", CANBUS.autopilot_party, {"DAS_aebEvent": 0}))
    msg.append(self.packer.make_can_msg("DAS_steeringControl", CANBUS.autopilot_party, {"DAS_steeringControlType": 0}))
    msg.append(self.packer.make_can_msg("DAS_settings", CANBUS.autopilot_party, {"DAS_autosteerEnabled": 0}))

    self.pm.send('can', can_list_to_can_capnp(msg))

  def send_panda_state(self, simulator_state: SimulatorState):
    self.sm.update(0)

    if self.params.get_bool("ObdMultiplexingEnabled") != self.obd_multiplexing:
      self.obd_multiplexing = not self.obd_multiplexing
      self.params.put_bool("ObdMultiplexingChanged", True)

    dat = messaging.new_message('pandaStates', 1)
    dat.valid = True
    dat.pandaStates[0] = {
      'ignitionLine': simulator_state.ignition,
      'pandaType': "blackPanda",
      'controlsAllowed': True,
      'safetyModel': 'tesla',
      'alternativeExperience': self.sm["carParams"].alternativeExperience if self.sm.valid["carParams"] else 0,
      'safetyParam': 0,
    }
    self.pm.send('pandaStates', dat)

  def update(self, simulator_state: SimulatorState):
    try:
      self.send_can_messages(simulator_state)

      if self.idx % 50 == 0:  # only send panda states at 2hz
        self.send_panda_state(simulator_state)

      self.idx += 1
    except Exception:
      traceback.print_exc()
      raise

  def close(self):
    """Release messaging resources."""
    self.pm = None
    self.sm = None

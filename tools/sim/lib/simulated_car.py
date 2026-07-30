"""Simulated vehicle for EOP vehicled.

EOP standardizes on Tesla DBC for all vehicle types. This simulator publishes
Tesla-format CAN messages regardless of the selected EOPVehicleType (which only
affects physics tuning, not the CAN protocol).
"""
import traceback
import cereal.messaging as messaging

from openpilot.common.params import Params
from openpilot.system.socketd import can_list_to_can_capnp
from openpilot.tools.sim.lib.common import SimulatorState
from openpilot.selfdrive.vehicled.tesla.values import CANBUS


class SimulatedCar:
  """Simulates a vehicle to EOP's vehicled daemon using the standard Tesla DBC protocol."""

  def __init__(self):
    self.pm = messaging.PubMaster(['can', 'pandaStates'])
    self.sm = messaging.SubMaster(['carControl', 'controlsState', 'carParams', 'selfdriveState'])
    self.idx = 0
    self.params = Params()
    self.obd_multiplexing = False
    self._counter = 0

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
    """DI_speed (0x257) - Vehicle speed in kph, scale 0.08, offset -40."""
    data = bytearray(8)
    # DI_vehicleSpeed: Intel, start_bit=12, size=12, scale=0.08, offset=-40, signed=False
    raw = int(round((speed_kph - (-40.0)) / 0.08))
    raw = max(0, min(4095, raw))  # 12-bit unsigned
    self._set_value(data, 12, 12, raw)
    return bytes(data)

  def _pack_system_status(self, gear: int, accel_pedal: float) -> bytes:
    """DI_systemStatus (0x118) - Gear, accel pedal, brake state, system state."""
    data = bytearray(8)
    # DI_accelPedalPos: Intel, start_bit=32, size=8, scale=0.4, offset=0, signed=False
    self._set_value(data, 32, 8, int(accel_pedal / 0.4))
    # DI_gear: Intel, start_bit=21, size=3, scale=1, offset=0, signed=False
    self._set_value(data, 21, 3, gear)  # 4=drive
    # DI_brakePedalState: Intel, start_bit=19, size=2, scale=1, offset=0, signed=False
    self._set_value(data, 19, 2, 0)  # 0=not pressed
    # DI_systemState: Intel, start_bit=16, size=3, scale=1, offset=0, signed=False
    self._set_value(data, 16, 3, 4)  # 4=normal driving
    return bytes(data)

  def _pack_brake_status(self, braking: bool) -> bytes:
    """IBST_status (0x39d) - Brake apply."""
    data = bytearray(8)
    val = 2 if braking else 0
    # IBST_driverBrakeApply: Intel, start_bit=0, size=2, scale=1, offset=0, signed=False
    self._set_value(data, 0, 2, val)
    return bytes(data)

  def _pack_epas(self, steering_angle: float, torque: float) -> bytes:
    """EPAS3S_sysStatus (0x370) - Steering angle and torque."""
    data = bytearray(8)
    # EPAS3S_internalSAS: Motorola, start_bit=37, size=14, scale=0.1, offset=-819.2, signed=True
    sas_raw = int(round((steering_angle - (-819.2)) / 0.1))
    sas_raw = max(-8192, min(8191, sas_raw))
    if sas_raw < 0:
      sas_raw += 16384  # 2^14
    self._set_value(data, 37, 14, sas_raw, little_endian=False)

    # EPAS3S_torsionBarTorque: Motorola, start_bit=19, size=12, scale=0.01, offset=-20.5, signed=True
    torsion_raw = int(round((torque - (-20.5)) / 0.01))
    torsion_raw = max(-2048, min(2047, torsion_raw))
    if torsion_raw < 0:
      torsion_raw += 4096  # 2^12
    self._set_value(data, 19, 12, torsion_raw, little_endian=False)

    # EPAS3S_handsOnLevel: Motorola, start_bit=39, size=2, scale=1, offset=0, signed=False
    self._set_value(data, 39, 2, 0, little_endian=False)  # 0=hands off

    # EPAS3S_eacStatus: Intel, start_bit=55, size=3, scale=1, offset=0, signed=False
    self._set_value(data, 55, 3, 1)  # 1=ACTIVE

    # EPAS3S_sysStatusCounter: Intel, start_bit=48, size=4, scale=1, offset=0, signed=False
    self._set_value(data, 48, 4, self._counter % 16)

    return bytes(data)

  def _pack_di_state(self, cruise_state: int, digital_speed: float) -> bytes:
    """DI_state (0x286) - Cruise state, speed units, digital speed, autopark, park brake."""
    data = bytearray(8)
    # DI_cruiseState: Intel, start_bit=12, size=3, scale=1, offset=0, signed=False
    self._set_value(data, 12, 3, cruise_state)
    # DI_speedUnits: Intel, start_bit=24, size=1, scale=1, offset=0, signed=False
    self._set_value(data, 24, 1, 0)  # 0=KPH
    # DI_digitalSpeed: Intel, start_bit=15, size=9, scale=0.5, offset=0, signed=False
    spd = int(digital_speed / 0.5)
    spd = max(0, min(511, spd))
    self._set_value(data, 15, 9, spd)
    # DI_autoparkState: Intel, start_bit=25, size=4, scale=1, offset=0, signed=False
    self._set_value(data, 25, 4, 0)  # 0=inactive
    # DI_parkBrakeState: Intel, start_bit=32, size=4, scale=1, offset=0, signed=False
    self._set_value(data, 32, 4, 1)  # 1=park brake released
    return bytes(data)

  def _pack_ui_warning(self, left_blinker: bool, right_blinker: bool, seatbelt: bool) -> bytes:
    """UI_warning (0x311) - Doors, blinkers, seatbelt."""
    data = bytearray(8)
    # anyDoorOpen: Intel, start_bit=28, size=1, scale=1, offset=0, signed=False
    self._set_value(data, 28, 1, 0)
    # leftBlinkerBlinking: Intel, start_bit=25, size=2, scale=1, offset=0, signed=False
    left_val = 1 if left_blinker else 0
    self._set_value(data, 25, 2, left_val)
    # rightBlinkerBlinking: Intel, start_bit=26, size=2, scale=1, offset=0, signed=False
    right_val = 1 if right_blinker else 0
    self._set_value(data, 26, 2, right_val)
    # buckleStatus: Intel, start_bit=13, size=1, scale=1, offset=0, signed=False
    buckle = 1 if seatbelt else 0
    self._set_value(data, 13, 1, buckle)
    return bytes(data)

  def _pack_sccm(self, steering_rate: float) -> bytes:
    """SCCM_steeringAngleSensor (0x129) - Steering angle speed."""
    data = bytearray(8)
    # SCCM_steeringAngleSpeed: Intel, start_bit=32, size=14, scale=0.5, offset=-4096, signed=True
    rate_raw = int(round((steering_rate - (-4096)) / 0.5))
    rate_raw = max(-8192, min(8191, rate_raw))
    if rate_raw < 0:
      rate_raw += 16384  # 2^14
    self._set_value(data, 32, 14, rate_raw)
    return bytes(data)

  def _pack_das_status(self) -> bytes:
    """DAS_status (0x39B) - Blindspot (inactive in sim)."""
    data = bytearray(8)
    # DAS_blindSpotRearLeft: Intel, start_bit=4, size=2, scale=1, offset=0, signed=False
    self._set_value(data, 4, 2, 0)
    # DAS_blindSpotRearRight: Intel, start_bit=6, size=2, scale=1, offset=0, signed=False
    self._set_value(data, 6, 2, 0)
    # DAS_speedLimit: Intel, start_bit=32, size=8, scale=1, offset=0, signed=False
    self._set_value(data, 32, 8, 0)
    return bytes(data)

  def send_can_messages(self, simulator_state: SimulatorState):
    if not simulator_state.valid:
      return

    speed_kph = simulator_state.speed * 3.6
    msg = []

    # Party bus (0) - vehicle sensors
    cruise_state = 2 if (simulator_state.is_engaged or simulator_state.cruise_button > 0) else 1
    msg.append((0x257, self._pack_speed(speed_kph), CANBUS.party))
    msg.append((0x118, self._pack_system_status(gear=4, accel_pedal=simulator_state.user_gas * 100), CANBUS.party))
    msg.append((0x39d, self._pack_brake_status(simulator_state.user_brake > 0), CANBUS.party))
    msg.append((0x370, self._pack_epas(simulator_state.steering_angle, simulator_state.user_torque / 100.0), CANBUS.party))
    msg.append((0x286, self._pack_di_state(cruise_state=cruise_state, digital_speed=speed_kph), CANBUS.party))
    msg.append((0x311, self._pack_ui_warning(simulator_state.left_blinker, simulator_state.right_blinker, seatbelt=True), CANBUS.party))
    msg.append((0x129, self._pack_sccm(0.0), CANBUS.party))
    msg.append((0x39B, self._pack_das_status(), CANBUS.autopilot_party))

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

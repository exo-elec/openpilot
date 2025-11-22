"""
Gateway CAN helpers
===================

This module documents the BYD-specific CAN transport layer that sits between
openpilot's generic controller and the hardware.  For now both BYD DOLPHIN and
BYD ATTO3 share an identical transport pattern, but the two DBC files use
slightly different message names (M2E/M2V vs MPC/FAKE_ prefixes).  Helpers
below normalize those differences so the controller can address either model
with the same code path.
"""

from dataclasses import dataclass

from cereal import car
from opendbc.car import CanBusBase
from opendbc.car.gateway.values import CAR


def _normalize_model(model) -> CAR | None:
  """Return CAR enum instance or None if unsupported."""
  if isinstance(model, CAR):
    return model
  try:
    return CAR(model)
  except Exception:
    return None


@dataclass(frozen=True)
class ModelMessages:
  lat_cmd: str
  mpc_state: str
  long_cmd: str
  eps_state: str


# Message name differences between Dolphin and Atto3 DBCs
MODEL_MESSAGES = {
  CAR.BYD_DOLPHIN: ModelMessages(
    lat_cmd="A_0x1E2_M2E_Lateral_Cmd_L8_20ms",
    mpc_state="A_0x316_M2X_MpcState_L8_20ms",
    long_cmd="A_0x32E_M2V_Long_Cmd_L8_50ms",
    eps_state="B_0x318_E2X_EpsState_L8_20ms",
  ),
  CAR.BYD_ATTO3: ModelMessages(
    lat_cmd="A_0x1E2_MPC_Lateral_Cmd_L8_20ms",
    mpc_state="A_0x316_MPC_MpcState_L8_20ms",
    long_cmd="A_0x32E_MPC_Long_Cmd_L8_50ms",
    eps_state="C_0x318_EPS_EpsState_L8_20ms",
  ),
}


def get_model_messages(model) -> ModelMessages:
  """Expose resolved message names for CarState/CarController."""
  model_enum = _normalize_model(model)
  return MODEL_MESSAGES.get(model_enum, MODEL_MESSAGES[CAR.BYD_DOLPHIN])


def inverted_sum_checksum(dat):
  """Calculate inverted sum checksum for all gateway messages."""
  return (~sum(dat[:7])) & 0xFF


class CanBus(CanBusBase):
  def __init__(self, CP=None, fingerprint=None) -> None:
    super().__init__(CP if fingerprint is None else None, fingerprint)
    # Three-bus architecture (BYD pattern):
    # - Bus 0 (ESC): Powertrain/EPS - control commands sent here
    # - Bus 2 (MPC): Stock camera - 0x318 sent here for MPC happiness
    self._pt = self.offset       # Powertrain/ESC bus
    self._lkas = self.offset     # LKAS commands go to ESC bus
    self._mpc = self.offset + 2  # MPC camera bus

  @property
  def pt(self) -> int:
    return self._pt

  @property
  def lkas(self) -> int:
    return self._lkas

  @property
  def mpc(self) -> int:
    return self._mpc


VisualAlert = car.CarControl.HUDControl.VisualAlert


# Steering angle wrapping limits (matches EPS feedback 8-bit signed range)
STEER_ANGLE_MAX = 12.7  # Maximum steering angle before wrap (127 × 0.1 = 12.7°)
STEER_ANGLE_MIN = -12.7  # Minimum steering angle before wrap (-127 × 0.1 = -12.7°)


def _wrap_steering_angle(angle_deg: float) -> float:
  """Wrap steering angle to ±12.7° range to match EPS feedback behavior."""
  raw_counts = angle_deg / 0.1
  raw_int = int(round(raw_counts))

  # Apply 8-bit signed wrapping (-128 to +127)
  if raw_int > 127:
    wrapped_int = ((raw_int + 128) % 256) - 128
  elif raw_int < -128:
    wrapped_int = ((raw_int + 128) % 256) - 128
  else:
    wrapped_int = raw_int

  return wrapped_int * 0.1


class GatewayCanBase:
  """Per-model CAN helper implementation."""

  def __init__(self, msgs: ModelMessages):
    self.msgs = msgs

  def create_lat_command(self, packer, CAN: CanBusBase, CC, steering_angle_deg: float,
                         counter: int = 0, stock_values: dict | None = None):
    values = dict(stock_values) if stock_values else {}
    wrapped_angle = _wrap_steering_angle(steering_angle_deg)

    # Handle both Dolphin and Atto3 signal naming (FAKE_ prefix on Atto3)
    for signal in ("MPC_SteeringAngleCmd", "FAKE_MPC_SteeringAngleCmd"):
      values[signal] = wrapped_angle
    for signal in ("MPC_LCCActivated", "FAKE_MPC_LCCActivated"):
      values[signal] = 1 if CC.latActive else 0

    values["MPC_RollingCounter_1E2"] = counter & 0xF

    dat = packer.make_can_msg(self.msgs.lat_cmd, CAN.pt, values)[1]
    values["MPC_inverted_checksum_1E2"] = inverted_sum_checksum(dat[:7])
    return packer.make_can_msg(self.msgs.lat_cmd, CAN.pt, values)

  def create_mpc_state_command(self, packer, CAN: CanBusBase, CC, lka_active: bool,
                               lane_detected: bool, counter: int = 0,
                               stock_values: dict | None = None):
    values = dict(stock_values) if stock_values else {}

    values["MPC_LKAEnabled"] = 1 if lka_active else 0
    values["MPC_ACCEnabled"] = 1 if CC.longActive else 0
    for signal in ("MPC_LCCEnabled", "FAKE_MPC_LCCEnabled"):
      values[signal] = 1 if lka_active else 0
    values.setdefault("MPC_LKAWarning", 0)
    values.setdefault("MPC_LDWActivated", 1 if lane_detected else 0)

    values["MPC_RollingCounter_316"] = counter & 0xF

    dat = packer.make_can_msg(self.msgs.mpc_state, CAN.mpc, values)[1]
    values["MPC_inverted_checksum_316"] = inverted_sum_checksum(dat[:7])
    return packer.make_can_msg(self.msgs.mpc_state, CAN.mpc, values)

  def create_long_command(self, packer, CAN: CanBusBase, accel: float, enabled: bool,
                          lead_distance: float = 50.0, counter: int = 0,
                          stock_values: dict | None = None):
    values = dict(stock_values) if stock_values else {}

    # Requested accel (m/s^2) with Atto3 FAKE_ prefix compatibility
    for signal in ("MPC_AccelerationCmd", "FAKE_MPC_AccelerationCmd"):
      values[signal] = accel

    # Boolean flags (best-effort defaults)
    for signal in ("MPC_AccEngaged", "FAKE_MPC_AccEngaged", "MPC_AccelRequest", "FAKE_MPC_AccelRequest"):
      values[signal] = 1 if enabled else 0

    values["MPC_RollingCounter_32E"] = counter & 0xF

    dat = packer.make_can_msg(self.msgs.long_cmd, CAN.pt, values)[1]
    values["MPC_inverted_checksum_32E"] = inverted_sum_checksum(dat[:7])
    return packer.make_can_msg(self.msgs.long_cmd, CAN.pt, values)

  def create_mpc_eps_state(self, packer, CAN: CanBusBase, counter: int = 0,
                           stock_values: dict | None = None):
    values = dict(stock_values) if stock_values else {}
    values.setdefault("EPS_Ready", 1)
    values.setdefault("EPS_CruiseEngaged", 1)
    values["EPS_RollingCounter_0D5"] = counter & 0xF

    dat = packer.make_can_msg(self.msgs.eps_state, CAN.mpc, values)[1]
    values["EPS_inverted_checksum_318"] = inverted_sum_checksum(dat[:7])
    return packer.make_can_msg(self.msgs.eps_state, CAN.mpc, values)

  # Diagnostic frames (not defined in the Atto3 DBC) – return None so callers skip
  def create_diag_controls_state(self, *args, **kwargs):
    return None

  def create_diag_lateral_state(self, *args, **kwargs):
    return None

  def create_diag_longitudinal_state(self, *args, **kwargs):
    return None

  def create_diag_car_state_mirror(self, *args, **kwargs):
    return None

  def create_diag_live_parameters(self, *args, **kwargs):
    return None

  def create_diag_model_outputs(self, *args, **kwargs):
    return None

  def create_diag_system_health(self, *args, **kwargs):
    return None

  def create_diag_faults(self, *args, **kwargs):
    return None


# Instantiate per-model helpers
MODEL_IMPL = {model: GatewayCanBase(msgs) for model, msgs in MODEL_MESSAGES.items()}


def _get_model_impl(model: CAR) -> GatewayCanBase:
  model_enum = _normalize_model(model)
  if model_enum is None or model_enum not in MODEL_IMPL:
    raise ValueError(f"Unsupported gateway model: {model}")
  return MODEL_IMPL[model_enum]


# Module-level wrappers used by CarController
def create_lat_command(model, packer, CAN: CanBusBase, CC, steering_angle_deg: float,
                       counter: int = 0, stock_values: dict | None = None):
  return _get_model_impl(model).create_lat_command(packer, CAN, CC, steering_angle_deg, counter, stock_values)


def create_mpc_state_command(model, packer, CAN: CanBusBase, CC, lka_active: bool,
                             lane_detected: bool, counter: int = 0,
                             stock_values: dict | None = None):
  return _get_model_impl(model).create_mpc_state_command(
    packer, CAN, CC, lka_active, lane_detected, counter, stock_values)


def create_long_command(model, packer, CAN: CanBusBase, accel: float, enabled: bool,
                        lead_distance: float = 50.0, counter: int = 0,
                        stock_values: dict | None = None):
  return _get_model_impl(model).create_long_command(packer, CAN, accel, enabled, lead_distance, counter, stock_values)


def create_mpc_eps_state(model, packer, CAN: CanBusBase, CS, counter: int = 0,
                         stock_values: dict | None = None):
  return _get_model_impl(model).create_mpc_eps_state(packer, CAN, counter, stock_values)


def create_diag_controls_state(model, packer, CAN: CanBusBase, sm, CC, CS, counter: int = 0):
  return _get_model_impl(model).create_diag_controls_state(packer, CAN, sm, CC, CS, counter)


def create_diag_lateral_state(model, packer, CAN: CanBusBase, sm, CS, steer_torque_motor, counter: int = 0):
  return _get_model_impl(model).create_diag_lateral_state(packer, CAN, sm, CS, steer_torque_motor, counter)


def create_diag_longitudinal_state(model, packer, CAN: CanBusBase, sm, CS, accel, gas, brake, counter: int = 0):
  return _get_model_impl(model).create_diag_longitudinal_state(packer, CAN, sm, CS, accel, gas, brake, counter)


def create_diag_car_state_mirror(model, packer, CAN: CanBusBase, CS, counter: int = 0):
  return _get_model_impl(model).create_diag_car_state_mirror(packer, CAN, CS, counter)


def create_diag_live_parameters(model, packer, CAN: CanBusBase, sm, CP, counter: int = 0):
  return _get_model_impl(model).create_diag_live_parameters(packer, CAN, sm, CP, counter)


def create_diag_model_outputs(model, packer, CAN: CanBusBase, sm, counter: int = 0):
  return _get_model_impl(model).create_diag_model_outputs(packer, CAN, sm, counter)


def create_diag_system_health(model, packer, CAN: CanBusBase, sm, counter: int = 0):
  return _get_model_impl(model).create_diag_system_health(packer, CAN, sm, counter)


def create_diag_faults(model, packer, CAN: CanBusBase, sm, CS, counter: int = 0):
  return _get_model_impl(model).create_diag_faults(packer, CAN, sm, CS, counter)

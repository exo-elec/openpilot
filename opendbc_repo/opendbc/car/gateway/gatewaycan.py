"""
Gateway CAN helpers
===================

This module documents the BYD-specific CAN transport layer that sits between
openpilot's generic controller and the hardware.  For now both BYD DOLPHIN and
BYD ATTO3 share an identical DBC/can layout, so every helper applies to both
models.  If a future variant diverges, add a dedicated branch or helper so
each model remains clearly labeled.

Topics covered:

* Why steering angle wrapping is needed (EPS only reports +/-12.7°)
* The CAN bus layout used by gateway devices
* Helper functions to pack 0x1E2, 0x32E, 0x316, etc.

Use the large comment blocks below as a reference when touching these helpers.
"""

# ============================================================================
# BYD Dolphin / ATTO3 CAN protocol overview
# ============================================================================

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


def _get_model_impl(model: CAR):
  model_enum = _normalize_model(model)
  if model_enum is None or model_enum not in MODEL_IMPL:
    raise ValueError(f"Unsupported gateway model: {model}")
  return MODEL_IMPL[model_enum]

# =============================================================================
# CHECKSUM - Single algorithm for all messages
# =============================================================================

def inverted_sum_checksum(dat):
  """Calculate inverted sum checksum for all gateway messages

  Simple algorithm: ~Sum(bytes 0-6) & 0xFF
  Used by ALL messages: 0x1E2, 0x32E, 0x316, 0x318, 0x6F0-0x6F8

  Args:
    dat: bytes of CAN message data (7 bytes, excluding checksum field)

  Returns:
    int: Inverted sum checksum (0-255)
  """
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


# =============================================================================
# CONTROL MESSAGES - Openpilot → Car
# =============================================================================

# Steering angle wrapping limits (matches EPS feedback 8-bit signed range)
STEER_ANGLE_MAX = 12.7  # Maximum steering angle before wrap (127 × 0.1 = 12.7°)
STEER_ANGLE_MIN = -12.7  # Minimum steering angle before wrap (-127 × 0.1 = -12.7°)


def _wrap_steering_angle(angle_deg: float) -> float:
  """Wrap steering angle to ±12.7° range to match EPS feedback behavior.

  BYD uses angle-based control (NOT torque-based). The MPC_SteeringAngleCmd
  signal in 0x1E2 is a 16-bit signed value, but we wrap in the degree domain
  to match the EPS feedback signal wrapping pattern (B_0x1FC EPS_SteeringAngle).

  EPS feedback wraps at ±12.7° because it uses 8-bit signed encoding:
  - B_0x1FC EPS_SteeringAngle: 8-bit signed, scale 0.1 → ±12.8/12.7°
  - A_0x1E2 MPC_SteeringAngleCmd: 16-bit signed, scale 0.000387 → ±12.7°

  By wrapping our command to ±12.7° in the degree domain, we ensure the
  command matches the EPS feedback wrapping pattern, even though the raw
  signal encoding differs.

  Args:
    angle_deg: Desired steering angle in degrees (any range)

  Returns:
    float: Wrapped angle in ±12.7° range

  Example:
    _wrap_steering_angle(0.0)    →   0.0° (no wrap)
    _wrap_steering_angle(12.7)   →  12.7° (max positive)
    _wrap_steering_angle(12.8)   → -12.7° (wraps to negative)
    _wrap_steering_angle(-12.8)  → -12.8° (min negative)
    _wrap_steering_angle(-12.9)  →  12.6° (wraps to positive)
  """
  # Simulate 8-bit wrapping in degree domain to match EPS feedback
  # Convert to 0.1° resolution (EPS feedback scale)
  raw_counts = angle_deg / 0.1
  raw_int = int(round(raw_counts))

  # Apply 8-bit signed wrapping (-128 to +127)
  if raw_int > 127:
    wrapped_int = ((raw_int + 128) % 256) - 128
  elif raw_int < -128:
    wrapped_int = ((raw_int + 128) % 256) - 128
  else:
    wrapped_int = raw_int

  # Convert back to degrees
  return wrapped_int * 0.1


# -----------------------------------------------------------------------------
# Per-model CAN helper implementations
# -----------------------------------------------------------------------------
class GatewayCanBase:
  """Default BYD Dolphin implementation."""

  def create_lat_command(self, packer, CAN: CanBusBase, CC, steering_angle_deg: float,
                         counter: int = 0, stock_values: dict | None = None):
    values = dict(stock_values) if stock_values else {}
    wrapped_angle = _wrap_steering_angle(steering_angle_deg)

    values.update({
      "MPC_SteeringAngleCmd": wrapped_angle,
      "MPC_LCCActivated": 1 if CC.latActive else 0,
      "MPC_RollingCounter_1E2": counter & 0xF,
    })

    dat = packer.make_can_msg("A_0x1E2_M2E_Lateral_Cmd_L8_20ms", CAN.pt, values)[1]
    values["MPC_inverted_checksum_1E2"] = inverted_sum_checksum(dat[:7])
    return packer.make_can_msg("A_0x1E2_M2E_Lateral_Cmd_L8_20ms", CAN.pt, values)

  def create_mpc_state_command(model, packer, CAN: CanBusBase, CC, lka_active: bool,
                             lane_detected: bool, counter: int = 0,
                             stock_values: dict | None = None):
  """Create MPC camera state message (0x316 @ 50Hz)."""
  return _get_model_impl(model).create_mpc_state_command(
    packer, CAN, CC, lka_active, lane_detected, counter, stock_values)

# =============================================================================
# LONGITUDINAL

# =============================================================================
# LONGITUDINAL CONTROL MESSAGE
# =============================================================================

def create_long_command(model, packer, CAN: CanBusBase, accel: float, enabled: bool,
                       lead_distance: float = 50.0, counter: int = 0,
                       stock_values: dict | None = None):
  """Create longitudinal acceleration command (0x32E @ 50Hz)."""
  return _get_model_impl(model).create_long_command(
    packer, CAN, accel, enabled, lead_distance, counter, stock_values)

# =============================================================================
# MPC ECHO SERVICE - Keep stock AEB/FCW active

# =============================================================================
# MPC ECHO SERVICE - Keep stock AEB/FCW active
# =============================================================================

def create_mpc_eps_state(model, packer, CAN: CanBusBase, CS, counter: int = 0,
                         stock_values: dict | None = None):
  """Create 0x318 EPS state for MPC bus 2 (NOT echo - generate from scratch)."""
  return _get_model_impl(model).create_mpc_eps_state(packer, CAN, counter, stock_values)

# =============================================================================
# DIAGNOSIS MESSAGES (0x6F0-0x6F8) - Internal params broadcast for CANape

# =============================================================================
# DIAGNOSIS MESSAGES (0x6F0-0x6F8) - Internal params broadcast for CANape
# =============================================================================

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

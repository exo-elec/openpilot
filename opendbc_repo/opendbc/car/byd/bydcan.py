"""BYD CAN checksum and TX message helpers.

byd_checksum is adapted from shemps/byd-atto3-openpilot-port commit
5b34194240bb831719629d2fd095fae5daaed1e0. create_steering_control's field
layout matches the tc275_freertos/TC275_BrownPanda dev/BYD_ATTO3 firmware's
real, car-tested WriteRaw sequence for 0x1E2
(DBC/byd_atto3.c:1173-1201) and opendbc/safety/modes/byd.h's byd_tx_hook,
which enforces every field written here.
"""


def byd_checksum(address: int, sig, dat: bytearray) -> int:
  del address, sig
  return (~sum(dat[:7])) & 0xFF


def create_steering_control(packer, angle: float, active: bool, counter: int):
  # angle rate fields are fixed sentinels the EPS module requires, not a
  # computed rate; real rate limiting happens in apply_std_steer_angle_limits
  # before this is called (byd_tx_hook.BYD_STEERING_LIMITS enforces the same
  # limits independently on the wire value)
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

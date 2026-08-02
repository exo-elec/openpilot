"""BYD CAN checksum and TX message helpers.

byd_checksum and create_steering_control's sentinel/rate fields are adapted
from shemps/byd-atto3-openpilot-port commit 5b34194240bb831719629d2fd095fae5daaed1e0
and cross-checked against the tc275_freertos/TC275_BrownPanda dev/BYD_ATTO3
firmware's real, car-tested WriteRaw sequence for 0x1E2 (DBC/byd_atto3.c:1173-1201)
and opendbc/safety/modes/byd.h's byd_tx_hook, which enforces every field written
here.

create_lkas_hud and create_acc_cmd are ported from the same fork's later,
CarrotPilot-derived carcontroller.py/bydcan.py (the "carrot-era" revision, not
the initial 5b34194 commit) - field names translated to the tc275/BYD_Atto3
naming convention used elsewhere in this DBC, bit patterns and comments
preserved as-is. That revision's comments cite specific on-car routes/measurements
(EPS state transitions, stock ADAS cross-check behavior); those citations are
the strongest evidence this port has for 0x316/0x32E and are kept verbatim.
They describe behavior observed on the reference fork's own vehicle, not this
project's target car - unvalidated here pending a target-car capture (see
nagaspilot/docs/MIGRATION_PLAN.md).
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


def create_lkas_hud(packer, lat_active: bool, counter: int, stock_lkas_hud: dict):
  # 0x316 is a validated safety frame: the ADAS modules cross-check its exact bit
  # pattern every frame and fail-safe on any mismatch (AEB/ACC/LKS/LDWS "limited" +
  # latched DTCs across 8 modules, measured on a 2024 Atto 3 MVS4 by the reference
  # fork). Pass every stock bit through untouched - including
  # MPC_HandsOnWheelRequest, the camera's hands-on nag.
  values = {**stock_lkas_hud, "COUNTER": counter}
  if lat_active:
    # Assert ONLY the on-car-proven active bits (reference fork pattern
    # a000ff1f29), preserving every other stock bit inside these multi-bit
    # fields:
    # - bit 37 set / bit 36 cleared (low 2 bits of MPC_LkasState): the
    #   EPS-arming pair; without them the EPS sees "LKAS disabled" and
    #   refuses to actuate. Bits 38-39 (stock state, e.g. fault flags) pass
    #   through.
    # - bits 5/35 (high bit of *_LANE_STATE): lane bits the stock camera sets
    #   when it steers (this DBC's GREEN=1/ORANGE=2 labels don't match the
    #   reference fork's car). Bits 4/34 pass through. No other bit may
    #   change until proven on this project's target car.
    values["MPC_LkasState"] = (int(stock_lkas_hud["MPC_LkasState"]) & 0b1100) | 0b0010
    values["MPC_LeftLaneState"] = int(stock_lkas_hud["MPC_LeftLaneState"]) | 2
    values["MPC_RightLaneState"] = int(stock_lkas_hud["MPC_RightLaneState"]) | 2

  return packer.make_can_msg("A_0x316_MPC_MpcState_L8_20ms", 0, values)


def create_acc_cmd(packer, accel: float, long_active: bool, counter: int,
                   standstill: bool = False, resume: bool = False):
  # 0x32E ACC_CMD - openpilot longitudinal trial. Reproduces the stock camera's
  # "actively commanding" bit pattern (bits ON1 ON2 CTRL=1, REQ_NOT_STANDSTILL=1,
  # CMD_REQ_ACTIVE_LOW=0), reverse-engineered by the reference fork from its own
  # drive captures. When not long_active this emits the disengaged pattern; the
  # safety fwd hook forwards the camera's own ACC_CMD in that case.
  # standstill: hold at a full stop - the stock choreography (per the reference
  # fork's captures) drops REQ_NOT_STANDSTILL and raises
  # OVERRIDE_OR_STANDSTILL + STANDSTILL_STATE.
  # resume: pulse STANDSTILL_RESUME to pull away from the hold.
  # ACCEL_FACTOR/DECEL_FACTOR are a paired regime selector telling the IPB which
  # gain profile to apply: coast (0,0), soft accel (12,5), soft decel (13,1),
  # sustained hard brake (1,1) - the reference fork's modal pair for every
  # accel bin observed in its own stock-ACC survey. Unvalidated against this
  # project's target car.
  holding = long_active and standstill and not resume
  if not long_active or abs(accel) < 0.1:
    accel_fac, decel_fac = 0, 0
  elif accel > 0:
    accel_fac, decel_fac = 12, 5
  elif accel > -1.5:
    accel_fac, decel_fac = 13, 1
  else:
    accel_fac, decel_fac = 1, 1
  values = {
    "MPC_AccelerationCmd": accel if long_active else 0.0,
    "MPC_AccOnPrimary": 1 if long_active else 0,
    "MPC_AccOnSecondary": 1 if long_active else 0,
    "MPC_AccControllableOn": 1 if long_active else 0,
    "MPC_AccRequestNotStandstill": 0 if holding else (1 if long_active else 0),
    "MPC_CommandRequestActiveLow": 0 if long_active else 1,
    "MPC_AccOverrideStandstill": 1 if holding else 0,
    "MPC_StandstillResume": 1 if (long_active and resume) else 0,
    "MPC_StandstillState": 1 if holding else 0,
    "MPC_ComfortAccelFactor": accel_fac,
    "MPC_ComfortDecelFactor": decel_fac,
    "SET_ME_25_1": 25,
    "SET_ME_25_2": 25,
    "SET_ME_1": 1,
    "SET_ME_X8": 8,
    "SET_ME_XF": 15,
    "COUNTER": counter,
  }
  return packer.make_can_msg("A_0x32E_MPC_Long_Cmd_L8_20ms", 0, values)

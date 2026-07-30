#!/usr/bin/env python3
"""Tesla CAN message creation — DBC-compliant format for TC275 BrownPanda gateway.

All messages match tesla_model3_party.dbc as implemented by TC275 firmware.
Reference: tc275_freertos/DBC/comma.c COMMA_DecodeMessage* functions.
"""
from __future__ import annotations

from openpilot.selfdrive.vehicled.tesla.values import CANBUS


class TeslaCAN:
  """Tesla CAN message generator matching TC275 BrownPanda protocol."""

  def __init__(self):
    self._steer_counter: int = 0
    self._long_counter: int = 0
    self._eac_counter: int = 0

  # ------------------------------------------------------------------ helpers

  @staticmethod
  def _set_le(data: bytearray, start_bit: int, size: int, value: int) -> None:
    """Write an Intel (little-endian) field. start_bit is the LSB position."""
    for i in range(size):
      bit = start_bit + i
      byte_idx = bit >> 3
      bit_idx  = bit & 7
      if byte_idx < len(data):
        if value & (1 << i):
          data[byte_idx] |= (1 << bit_idx)
        else:
          data[byte_idx] &= ~(1 << bit_idx)

  @staticmethod
  def _checksum(addr: int, data: bytes | bytearray, length: int) -> int:
    """TC275 Tesla checksum: sum(ID_high + ID_low + data[0..length-1]) & 0xFF.
    Matches COMMA_CalculateChecksumWithLength() in TC275 comma.c."""
    csum = ((addr >> 8) & 0xFF) + (addr & 0xFF)
    for i in range(length):
      csum += data[i]
    return csum & 0xFF

  # --------------------------------------------------------------- TX messages

  def create_steering_control(self, angle: float, lat_active: bool) -> tuple[int, bytes, int]:
    """DAS_steeringControl 0x488 — DBC: 4-byte Motorola format.

    DBC signals (tesla_model3_party.dbc):
      DAS_steeringAngleRequest  : 6|15@0+  (0.1, -1638.35)  — 15-bit Motorola
      DAS_steeringHapticRequest : 7|1@0+
      DAS_steeringControlType   : 23|2@0+  (0=NONE, 1=ANGLE_CONTROL)
      DAS_steeringControlCounter: 19|4@0+  — 4-bit, byte[2] bits[3:0]
      DAS_steeringControlChecksum: 31|8@0+ — byte[3]
    """
    data = bytearray(4)

    # Angle — 15-bit Motorola: byte[0] bits[6:0] = upper 7 bits, byte[1] = lower 8 bits
    # Negated to match sunnypilot convention: openpilot positive=LEFT, DAS wire positive=RIGHT
    raw = int((-angle + 1638.35) * 10)
    raw = max(0, min(32767, raw))
    data[0] = (raw >> 8) & 0x7F   # bit 7 reserved for haptic (0)
    data[1] = raw & 0xFF

    # Control type — Motorola 23|2@0+ = byte[2] bits[7:6]
    data[2] = ((1 if lat_active else 0) & 0x03) << 6

    # Counter — Motorola 19|4@0+ = byte[2] bits[3:0]
    data[2] |= self._steer_counter & 0x0F
    self._steer_counter = (self._steer_counter + 1) % 16

    # Checksum — byte[3], sum bytes 0-2
    data[3] = self._checksum(0x488, data, 3)

    return (0x488, bytes(data), CANBUS.party)

  def create_steering_allowed(self) -> tuple[int, bytes, int]:
    """Heartbeat: 0x488 with angle=0 and control_type=NONE."""
    return self.create_steering_control(0.0, False)

  def create_longitudinal_command(
    self,
    state: int,
    accel: float,
    cntr: int,
    v_ego: float,
  ) -> tuple[int, bytes, int]:
    """DAS_control 0x2B9 — DBC: 8-byte Intel format.

    DBC signals:
      DAS_setSpeed       :  0|12@1+ (0.1, 0)        — speed in kph
      DAS_accState       : 12|4@1+                  — 0..15
      DAS_aebEvent       : 16|2@1+                  — 0=INACTIVE
      DAS_accelMin       : 35|9@1+ (0.04, -15)      — m/s²
      DAS_accelMax       : 44|9@1+ (0.04, -15)      — m/s²
      DAS_controlCounter : 53|3@1+                  — 0..7
      DAS_controlChecksum: 56|8@1+                  — byte[7]

    accel is used as both accelMin and accelMax (TC275 takes the active one).
    """
    data = bytearray(8)

    # Speed: 0|12@1+ (0.1, 0) → raw = kph / 0.1 = m/s * 36
    speed_raw = int(v_ego * 36.0)
    speed_raw = max(0, min(4095, speed_raw))
    self._set_le(data, 0, 12, speed_raw)

    # ACC state: 12|4@1+
    self._set_le(data, 12, 4, state & 0xF)

    # aebEvent: 16|2@1+ — always INACTIVE
    # (leave 0)

    # DAS_jerkMin : 18|9@1+ (0.018, -9.1) → raw = (value + 9.1) / 0.018
    # DAS_jerkMax : 27|8@1+ (0.034,  0  ) → raw = value / 0.034
    # sunnypilot CarControllerParams: JERK_LIMIT_MIN = -4.9, JERK_LIMIT_MAX = 4.9 m/s³
    jerk_min_raw = max(0, min(511, int((-4.9 + 9.1) / 0.018)))  # ≈ 233
    jerk_max_raw = max(0, min(255, int(4.9 / 0.034)))           # ≈ 144
    self._set_le(data, 18, 9, jerk_min_raw)   # DAS_jerkMin
    self._set_le(data, 27, 8, jerk_max_raw)   # DAS_jerkMax

    # accelMin = actual target; accelMax = max(accel, 0) — sunnypilot convention
    accel_min_raw = max(0, min(511, int((accel + 15.0) / 0.04)))
    accel_max_raw = max(0, min(511, int((max(accel, 0.0) + 15.0) / 0.04)))
    self._set_le(data, 35, 9, accel_min_raw)  # DAS_accelMin
    self._set_le(data, 44, 9, accel_max_raw)  # DAS_accelMax

    # Counter: 53|3@1+  (caller supplies cntr for sync with its own sequence)
    self._set_le(data, 53, 3, cntr & 0x7)

    # Checksum: byte[7], sum bytes 0-6
    data[7] = self._checksum(0x2B9, data, 7)

    return (0x2B9, bytes(data), CANBUS.party)

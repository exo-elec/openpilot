#!/usr/bin/env python3
"""Minimal Tesla CAN message parser.

Replaces opendbc.can.CANParser for Tesla-specific messages.
Hardcodes the Tesla Model 3/Y message formats.
"""
from __future__ import annotations

from typing import Any
from dataclasses import dataclass


@dataclass
class ParsedSignal:
  """A parsed CAN signal value."""
  name: str
  value: Any
  

class TeslaParser:
  """Minimal CAN parser for Tesla messages."""
  
  def __init__(self):
    # Message definitions for Tesla Model 3/Y
    # Format: (address, name, signals)
    # Signals: (name, start_bit, size, scale, offset, is_signed)
    self.msg_defs = {
      # DI_speed - Vehicle speed
      0x257: {
        'name': 'DI_speed',
        'size': 8,
        'signals': {
          'DI_vehicleSpeed': (12, 12, 0.08, -40.0, False),  # DBC 12|12@1+ (0.08,-40) kph
        }
      },
      # DI_systemStatus - System status
      0x118: {
        'name': 'DI_systemStatus',
        'size': 8,
        'signals': {
          'DI_accelPedalPos': (32, 8, 0.4, 0, False),  # scale 0.4%
          'DI_gear': (21, 3, 1, 0, False),  # gear position
          'DI_brakePedalState': (19, 2, 1, 0, False),
          'DI_systemState': (16, 3, 1, 0, False),
        }
      },
      # IBST_status - Brake status
      0x39d: {
        'name': 'IBST_status',
        'size': 8,
        'signals': {
          'IBST_driverBrakeApply': (0, 2, 1, 0, False),  # 2=braking
        }
      },
      # EPAS3S_sysStatus - Steering status (duplicate removed, 0x370 is canonical)
      # DI_state - Vehicle state
      0x286: {
        'name': 'DI_state',
        'size': 8,
        'signals': {
          'DI_cruiseState': (12, 3, 1, 0, False),  # Cruise state
          'DI_speedUnits': (24, 1, 1, 0, False),  # 0=KPH, 1=MPH
          'DI_digitalSpeed': (15, 9, 0.5, 0, False),  # Digital speed
          'DI_autoparkState': (25, 4, 1, 0, False),  # Autopark state
          'DI_parkBrakeState': (32, 4, 1, 0, False),
        }
      },
      # UI_warning - UI warnings
      0x311: {
        'name': 'UI_warning',
        'size': 7,
        'signals': {
          'anyDoorOpen': (28, 1, 1, 0, False),
          'leftBlinkerBlinking': (25, 2, 1, 0, False),
          'rightBlinkerBlinking': (26, 2, 1, 0, False),
          'buckleStatus': (13, 1, 1, 0, False),  # 1=latched
        }
      },
      # SCCM_steeringAngleSensor - Steering angle from SCCM
      0x129: {
        'name': 'SCCM_steeringAngleSensor',
        'size': 8,
        'signals': {
          'SCCM_steeringAngleSpeed': (32, 14, 0.5, -4096, True),  # deg/s
        }
      },
      # DAS_status - DAS status (duplicate removed, 0x39B is canonical)
      # DAS_control - DAS control (for reading stock ACC)
      0x2b9: {
        'name': 'DAS_control',
        'size': 8,
        'signals': {
          'DAS_aebEvent': (0, 1, 1, 0, False),
          'DAS_controlCounter': (36, 3, 1, 0, False),
        }
      },
      # DAS_steeringControl - DAS steering control (for reading stock LKAS)
      0x488: {
        'name': 'DAS_steeringControl',
        'size': 8,
        'signals': {
          'DAS_steeringControlType': (32, 2, 1, 0, False),
        }
      },
      # DAS_settings - DAS settings
      0x293: {
        'name': 'DAS_settings',
        'size': 8,
        'signals': {
          'DAS_autosteerEnabled': (38, 1, 1, 0, False),
        }
      },
      # ESP_status - ESP/brake status
      0x145: {
        'name': 'ESP_status',
        'size': 8,
        'signals': {
          'ESP_driverBrakeApply': (29, 2, 1, 0, False),
        }
      },
      # EPAS3S_sysStatus (TC275 actual TX ID) - Steering angle and torque
      # Motorola (big-endian) signals per tesla_model3_party.dbc
      0x370: {
        'name': 'EPAS3S_sysStatus',
        'size': 8,
        'signals': {
          'EPAS3S_internalSAS':      (37, 14, 0.1,    -819.2, True,  True),
          'EPAS3S_torsionBarTorque': (19, 12, 0.01,   -20.5,  True,  True),
          'EPAS3S_handsOnLevel':     (39,  2, 1,       0,     False, True),
          'EPAS3S_eacStatus':        (55,  3, 1,       0,     False, True),
          'EPAS3S_sysStatusCounter': (48,  4, 1,       0,     False),
        }
      },
      # DAS_status (TC275 actual TX ID) - Blindspot warnings
      # TC275 encodes: leftBlindspot at bits 4-5, rightBlindspot at bits 6-7 (LE)
      0x39B: {
        'name': 'DAS_status',
        'size': 8,
        'signals': {
          'DAS_blindSpotRearLeft':  (4, 2, 1, 0, False),
          'DAS_blindSpotRearRight': (6, 2, 1, 0, False),
          'DAS_speedLimit':         (32, 8, 1, 0, False),
        }
      },
      # ALCC_state (TC275 custom 0x700) - Active Lane Change Control state
      0x700: {
        'name': 'ALCC_state',
        'size': 8,
        'signals': {
          'ALCC_confidence':    (0,  8, 1, 0, False),
          'ALCC_lanesDetected': (8,  8, 1, 0, False),
          'ALCC_available':     (16, 1, 1, 0, False),
          'ALCC_active':        (24, 1, 1, 0, False),
          'ALCC_state':         (32, 1, 1, 0, False),
          'ALCC_counter':       (52, 4, 1, 0, False),
        }
      },
      # Continental ARS4-xx radar (TC275 CAN3 → comma bus 1)
      # 0x401: RadarStatus — trigger frame, presence = sensor OK
      0x401: {
        'name': 'RadarStatus',
        'size': 8,
        'signals': {}   # Zeroed payload; presence is the only check
      },
      # Object_A frames: 0x410 + slot*2 (slots 0..39)
      # Object_B frames: 0x411 + slot*2 (slots 0..39)
      # Active: slot 0 = left BSD, slot 1 = right BSD; others empty (Tracked=0)
      # Signals (all Intel @1+):
      #   Object_A: LongDist(0|12,0.0625), LongSpeed(12|12,0.0625,-128),
      #             LatDist(24|11,0.125,-128), Valid(55|1), Tracked(62|1), Index(63|1)
      #   Object_B: LatSpeed(0|10,0.125,-64), Index2(63|1)
      # Trigger: 0x45F (slot-39 Object_B arrives last)
      **{
        0x410 + s * 2: {
          'name': f'Continental_A_{s}',
          'size': 8,
          'signals': {
            'CA_LongDist':   (0,  12, 0.0625,   0.0,   False),
            'CA_LongSpeed':  (12, 12, 0.0625, -128.0,  False),
            'CA_LatDist':    (24, 11, 0.125,  -128.0,  False),
            'CA_Valid':      (55,  1, 1.0,      0.0,   False),
            'CA_Tracked':    (62,  1, 1.0,      0.0,   False),
            'CA_Index':      (63,  1, 1.0,      0.0,   False),
          }
        }
        for s in range(40)
      },
      **{
        0x411 + s * 2: {
          'name': f'Continental_B_{s}',
          'size': 8,
          'signals': {
            'CB_LatSpeed':   (0,  10, 0.125, -64.0, False),
            'CB_Index2':     (63,  1, 1.0,    0.0,  False),
          }
        }
        for s in range(40)
      },
    }
    
    # Current values
    self.vl: dict[str, dict[str, Any]] = {}
    
  def _extract_signal(self, data: bytes, start_bit: int, size: int,
                      scale: float, offset: float, is_signed: bool) -> float:
    """Extract a little-endian (Intel) CAN signal. start_bit is LSB position."""
    value = 0
    for i in range(size):
      bit = start_bit + i
      byte_idx = bit // 8
      bit_idx = bit % 8
      if byte_idx < len(data):
        if data[byte_idx] & (1 << bit_idx):
          value |= (1 << i)
    if is_signed and (value & (1 << (size - 1))):
      value = value - (1 << size)
    return value * scale + offset

  def _extract_signal_motorola(self, data: bytes, start_bit: int, size: int,
                               scale: float, offset: float, is_signed: bool) -> float:
    """Extract a Motorola (big-endian) CAN signal. start_bit is MSB in Intel bit numbering.
    Follows the standard Motorola snake: descend within byte, then jump to MSB of next byte."""
    value = 0
    bit = start_bit
    for i in range(size):
      byte_idx = bit // 8
      bit_idx = bit % 8
      if byte_idx < len(data):
        if data[byte_idx] & (1 << bit_idx):
          value |= (1 << (size - 1 - i))
      if bit_idx == 0:
        bit = (byte_idx + 1) * 8 + 7
      else:
        bit -= 1
    if is_signed and (value & (1 << (size - 1))):
      value = value - (1 << size)
    return value * scale + offset

  def parse_message(self, address: int, data: bytes) -> dict[str, Any | None]:
    """Parse a single CAN message."""
    msg_def = self.msg_defs.get(address)
    if msg_def is None:
      return None
    result = {'name': msg_def['name']}
    for sig_name, sig_def in msg_def['signals'].items():
      start_bit, size, scale, offset, is_signed = sig_def[:5]
      is_motorola = sig_def[5] if len(sig_def) > 5 else False
      if is_motorola:
        result[sig_name] = self._extract_signal_motorola(data, start_bit, size, scale, offset, is_signed)
      else:
        result[sig_name] = self._extract_signal(data, start_bit, size, scale, offset, is_signed)
    return result
    
  def update(self, can_messages: list[tuple[int, bytes, int]]) -> None:
    """Update parser with new CAN messages.
    
    Args:
      can_messages: List of (address, data, bus) tuples
    """
    for addr, data, bus in can_messages:
      parsed = self.parse_message(addr, data)
      if parsed:
        name = parsed.pop('name')
        self.vl[name] = parsed
        
  def update_strings(self, can_strings: list[list[tuple[int, bytes, int]]]) -> None:
    """Update from can_strings format (list of lists)."""
    for can_list in can_strings:
      self.update(can_list)


class SimpleCANParser:
  """Simplified CAN parser that mimics opendbc.can.CANParser interface."""
  
  def __init__(self, dbc_name: str, signals: list[Any], bus: int):
    """Initialize parser.
    
    Args:
      dbc_name: Ignored (for compatibility)
      signals: Ignored (for compatibility)
      bus: CAN bus number
    """
    self.bus = bus
    self.parser = TeslaParser()
    self.vl = self.parser.vl
    
  def update_strings(self, can_strings: list[list[tuple[int, bytes, int]]]) -> None:
    """Update parser with CAN data."""
    # Filter messages for our bus
    for can_list in can_strings:
      bus_messages = [(addr, data, bus) for addr, data, bus in can_list if bus == self.bus]
      self.parser.update(bus_messages)
      self.vl = self.parser.vl

#!/usr/bin/env python3
"""Continental ARS4-xx Radar Interface for TC275 BrownPanda gateway.

TC275 emits Continental ARS4-B frames on CAN3 (comma bus 1):

  0x401              RadarStatus     — trigger, 100ms
  0x410 + slot*2     Object_A[0..39] — LongDist, LongSpeed, LatDist, Valid, Tracked, Index
  0x411 + slot*2     Object_B[0..39] — LatSpeed, Index2

Active slots: 0 = left BSD, 1 = right BSD. Slots 2-39 are always empty (Tracked=0).
0x45F (slot 39 Object_B) arrives last and is used as the update trigger.

Object pair valid when: Tracked==1 AND Valid==1 AND Index==Index2.

Reference: tc275_freertos/DBC/comma.c COMMA_SendContinentalSlot()
           tc275_freertos/docs/architecture.md "Continental ARS4-xx Radar"
"""
from __future__ import annotations

from cereal import car

# Radar-to-camera offset (m)
RADAR_TO_CAMERA = 1.52

# ID constants
_STATUS_ID  = 0x401
_OBJ_A_BASE = 0x410   # Object_A for slot s = 0x410 + s*2
_OBJ_B_BASE = 0x411   # Object_B for slot s = 0x411 + s*2
_NUM_SLOTS   = 40
_TRIGGER_ID  = _OBJ_B_BASE + (_NUM_SLOTS - 1) * 2   # 0x45F — last Object_B

# Active BSD slots
_LEFT_SLOT  = 0
_RIGHT_SLOT = 1


def _le(data: bytes, start_bit: int, size: int, scale: float = 1.0, offset: float = 0.0) -> float:
  """Extract a little-endian (Intel) CAN field with bounds checking."""
  v = 0
  for i in range(size):
    bit = start_bit + i
    byte_idx = bit >> 3
    if byte_idx >= len(data):
      return 0.0
    if data[byte_idx] & (1 << (bit & 7)):
      v |= (1 << i)
  return v * scale + offset


class ContinentalRadarInterface:
  """Parses TC275 Continental ARS4-xx radar frames and produces car.RadarData.

  - Triggered by 0x45F (last Object_B in the 100ms burst).
  - Two slots: 0 = left BSD, 1 = right BSD.
  - A and B frames of the same slot must have matching Index / Index2.
  """

  def __init__(self, CP: car.CarParams):
    self.CP = CP
    self.pts: dict[int, car.RadarData.RadarPoint] = {}
    self.frame = 0

    # Pending A-frame data keyed by slot
    self._obj_a: dict[int, dict] = {}
    self._obj_b: dict[int, dict] = {}
    self._sensor_ok = False
    self._updated: set[int] = set()

  def update(self, can_list: list) -> car.RadarData | None:
    self.frame += 1
    triggered = False
    self._sensor_ok = False  # reset each cycle — absence of status = fault

    for m in can_list:
      addr = m.address
      dat  = bytes(m.dat)
      # TC275 Continental radar is on CAN3 → comma bus 1
      if getattr(m, 'src', 0) != 1:
        continue
      self._updated.add(addr)

      if addr == _STATUS_ID:
        # RadarStatus — zeroed payload; presence = sensor OK
        self._sensor_ok = True

      elif (_OBJ_A_BASE <= addr < _OBJ_A_BASE + _NUM_SLOTS * 2) and (addr & 1) == 0:
        # Object_A
        slot = (addr - _OBJ_A_BASE) // 2
        if len(dat) >= 8:
          tracked = bool(_le(dat, 62, 1))
          valid   = bool(_le(dat, 55, 1))
          index   = int(_le(dat, 63, 1))
          self._obj_a[slot] = {
            'tracked': tracked,
            'valid':   valid,
            'index':   index,
            'dRel':    _le(dat,  0, 12, scale=0.0625),
            'vRel':    _le(dat, 12, 12, scale=0.0625, offset=-128.0),
            'yRel':    _le(dat, 24, 11, scale=0.125,  offset=-128.0),
          }

      elif (_OBJ_B_BASE <= addr < _OBJ_B_BASE + _NUM_SLOTS * 2) and (addr & 1) == 1:
        # Object_B
        slot = (addr - _OBJ_B_BASE) // 2
        if len(dat) >= 8:
          index2  = int(_le(dat, 63, 1))
          yvRel   = _le(dat, 0, 10, scale=0.125, offset=-64.0)
          self._obj_b[slot] = {'index2': index2, 'yvRel': yvRel}

        if addr == _TRIGGER_ID:
          triggered = True

    if not triggered:
      return None

    ret = car.RadarData.new_message()
    ret.errors = [] if self._sensor_ok else ['fault']

    for slot in (_LEFT_SLOT, _RIGHT_SLOT):
      track_id = slot + 1   # 1-based track IDs
      a = self._obj_a.get(slot)
      b = self._obj_b.get(slot)

      if (a is None or b is None
          or not a['tracked']
          or not a['valid']
          or a['index'] != b['index2']):
        self.pts.pop(track_id, None)
        continue

      if track_id not in self.pts:
        self.pts[track_id] = car.RadarData.RadarPoint.new_message()
        self.pts[track_id].trackId = track_id

      pt = self.pts[track_id]
      pt.dRel     = a['dRel'] - RADAR_TO_CAMERA
      pt.yRel     = -a['yRel']   # positive left in openpilot
      pt.vRel     = a['vRel']
      pt.yvRel    = b['yvRel']
      pt.aRel     = float('nan')
      pt.measured = True

    # Clear Object_A/B buffers for next cycle
    self._obj_a.clear()
    self._obj_b.clear()

    ret.points = list(self.pts.values())
    return ret

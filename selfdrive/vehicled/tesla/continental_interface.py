#!/usr/bin/env python3
"""Continental ARS4-B compatibility interface for TC375 BrownPanda.

TC375 emits converted Continental ARS4-B frames on Tesla party bus 0:

  0x401              RadarStatus     — status, ~16Hz
  0x410 + slot*2     Object_A[0..39] — LongDist, LongSpeed, LatDist, Valid, Tracked, Index
  0x411 + slot*2     Object_B[0..39] — LatSpeed, Index2

BYD MVS4 slots 0..9 are mapped directly; slots 10..39 are empty (Tracked=0).
Unproven BYD acceleration and lateral-speed fields are transmitted as neutral.
0x45F (slot 39 Object_B) arrives last and is used as the update trigger.

Object pair valid when: Tracked==1 AND Valid==1 AND Index==Index2.

Signal semantics are copied from commaai/opendbc and cross-checked against
sunnypilot and dragonpilot. EOP10 intentionally receives the converted stream
on its two-bus BrownPanda party bus rather than stock Tesla radar bus 1.
"""
from __future__ import annotations

from collections.abc import Callable
from time import monotonic

from cereal import car
from openpilot.selfdrive.vehicled.tesla.values import CANBUS

# ID constants
_STATUS_ID  = 0x401
_OBJ_A_BASE = 0x410   # Object_A for slot s = 0x410 + s*2
_OBJ_B_BASE = 0x411   # Object_B for slot s = 0x411 + s*2
_NUM_SLOTS   = 40
_TRIGGER_ID  = _OBJ_B_BASE + (_NUM_SLOTS - 1) * 2   # 0x45F — last Object_B
_STATUS_TIMEOUT_S = 0.2
_FAULT_REPORT_PERIOD_S = 0.1

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
  """Parse the BrownPanda ARS4-B stream into ``car.RadarData``.

  - Triggered by 0x45F (last Object_B in each paced ~16 Hz set).
  - Processes all 40 slots, matching the stock Tesla radar interface.
  - A and B frames of the same slot must have matching Index / Index2.
  """

  def __init__(self, CP: car.CarParams, time_fn: Callable[[], float] = monotonic):
    self.CP = CP
    self._time_fn = time_fn
    self.pts: dict[int, car.RadarData.RadarPoint] = {}
    self.frame = 0

    # Pending A-frame data keyed by slot
    self._obj_a: dict[int, dict] = {}
    self._obj_b: dict[int, dict] = {}
    self._sensor_ok = False
    self._status_mono = self._time_fn()
    self._last_fault_mono = self._status_mono - _FAULT_REPORT_PERIOD_S
    self._have_status = False

  def update(self, can_list: list) -> car.RadarData | None:
    self.frame += 1
    triggered = False

    for m in can_list:
      addr = m.address
      dat  = bytes(m.dat)
      # Radar shares the Tesla party wire; no third comma CAN is required.
      if getattr(m, 'src', None) != CANBUS.party:
        continue
      if addr == _STATUS_ID and len(dat) >= 8:
        # Status is the start-of-set marker. Drop incomplete halves from a
        # previous set before the one-bit pair index can wrap and match stale data.
        self._obj_a.clear()
        self._obj_b.clear()
        short_term_unavailable = bool(_le(dat, 23, 1))
        sensor_blocked = bool(_le(dat, 26, 1))
        vehicle_dynamics_error = bool(_le(dat, 27, 1))
        self._sensor_ok = not (short_term_unavailable or sensor_blocked or vehicle_dynamics_error)
        self._status_mono = self._time_fn()
        self._have_status = True

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
            'aRel':    _le(dat, 40, 10, scale=0.03125, offset=-16.0),
            'measured': bool(_le(dat, 61, 1)),
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

    now = self._time_fn()
    if not triggered:
      if now - self._status_mono <= _STATUS_TIMEOUT_S:
        return None
      if now - self._last_fault_mono < _FAULT_REPORT_PERIOD_S:
        return None

      # Absence of the TC375 stream is itself the unavailable signal. Clear
      # cached points and publish a bounded-rate fault without requiring the
      # gateway to synthesize status frames.
      self._last_fault_mono = now
      self._sensor_ok = False
      self.pts.clear()
      self._obj_a.clear()
      self._obj_b.clear()
      ret = car.RadarData.new_message()
      ret.errors = ['fault']
      ret.points = []
      return ret

    ret = car.RadarData.new_message()
    status_fresh = self._have_status and (now - self._status_mono) <= _STATUS_TIMEOUT_S
    ret.errors = [] if self._sensor_ok and status_fresh else ['fault']

    for slot in range(_NUM_SLOTS):
      track_id = slot + 1
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
      # Match official opendbc exactly. radard owns radar/camera geometry.
      pt.dRel     = a['dRel']
      pt.yRel     = a['yRel']
      pt.vRel     = a['vRel']
      pt.yvRel    = b['yvRel']
      pt.aRel     = a['aRel']
      pt.measured = a['measured']

    # Clear Object_A/B buffers for next cycle
    self._obj_a.clear()
    self._obj_b.clear()

    ret.points = list(self.pts.values())
    return ret

"""NGP10 consumer for the BrownPanda Continental radar stream.

BrownPanda places Tesla ARS4-B-compatible classical CAN frames on logical party
bus 0 because BrownPanda exposes only two comma-facing channels. Signal layout
comes from the official ``tesla_radar_continental`` OpenDBC.
"""
from __future__ import annotations

from collections.abc import Callable, Iterator
from time import monotonic
from typing import TypedDict

from opendbc.car import structs
from opendbc.car.can_definitions import CanData
from opendbc.car.interfaces import RadarInterfaceBase
from opendbc.car.tesla.values import CANBUS, CAR

BROWNPANDA_RADAR_CARS = frozenset((CAR.TESLA_MODEL_3, CAR.TESLA_MODEL_Y))

_STATUS_ID = 0x401
_OBJECT_A_BASE = 0x410
_OBJECT_B_BASE = 0x411
_NUM_SLOTS = 40
_TRIGGER_ID = _OBJECT_B_BASE + (_NUM_SLOTS - 1) * 2  # 0x45F
_STATUS_TIMEOUT_S = 0.2
_FAULT_REPORT_PERIOD_S = 0.1


class _ObjectA(TypedDict):
  tracked: bool
  valid: bool
  measured: bool
  index: int
  dRel: float
  vRel: float
  yRel: float


class _ObjectB(TypedDict):
  index2: int


def _read_le(data: bytes, start_bit: int, size: int, scale: float = 1.0, offset: float = 0.0) -> float:
  """Read an Intel signal using the official Continental DBC bit layout."""
  value = 0
  for i in range(size):
    bit = start_bit + i
    byte_index = bit >> 3
    if byte_index >= len(data):
      return 0.0
    if data[byte_index] & (1 << (bit & 7)):
      value |= 1 << i
  return value * scale + offset


def _messages(can_packets: list[tuple[int, list[CanData]] | list[CanData] | CanData]) -> Iterator[CanData]:
  for packet in can_packets:
    # Card supplies timestamped packet groups. Accept a plain message list too
    # so focused tests and replay tools can exercise the parser directly.
    if isinstance(packet, CanData):
      yield packet
    else:
      messages = packet[1] if isinstance(packet, tuple) else packet
      yield from messages


class BrownPandaRadarInterface(RadarInterfaceBase):
  """Parse BrownPanda's complete 40-pair stream from NGP10 party bus 0."""

  def __init__(self, CP: structs.CarParams, time_fn: Callable[[], float] = monotonic):
    super().__init__(CP)
    self._time_fn = time_fn
    self._object_a: dict[int, _ObjectA] = {}
    self._object_b: dict[int, _ObjectB] = {}
    self._sensor_fault = False
    self._sensor_unavailable = False
    self._have_status = False
    self._status_time = self._time_fn()
    self._last_fault_time = self._status_time - _FAULT_REPORT_PERIOD_S

  def update(self, can_packets: list[tuple[int, list[CanData]] | list[CanData] | CanData]) -> structs.RadarDataT | None:
    self.frame += 1
    triggered = False

    for message in _messages(can_packets):
      if message.src != CANBUS.party:
        continue

      address = message.address
      data = bytes(message.dat)
      if address == _STATUS_ID and len(data) == 8:
        # Status starts a new set. Never combine an old half-pair after the
        # one-bit pair index wraps.
        self._object_a.clear()
        self._object_b.clear()
        self._sensor_unavailable = bool(_read_le(data, 23, 1) or _read_le(data, 26, 1))
        self._sensor_fault = bool(_read_le(data, 27, 1))
        self._status_time = self._time_fn()
        self._have_status = True

      elif _OBJECT_A_BASE <= address < _OBJECT_A_BASE + _NUM_SLOTS * 2 and (address & 1) == 0:
        if len(data) == 8:
          slot = (address - _OBJECT_A_BASE) // 2
          self._object_a[slot] = {
            "tracked": bool(_read_le(data, 62, 1)),
            "valid": bool(_read_le(data, 55, 1)),
            "measured": bool(_read_le(data, 61, 1)),
            "index": int(_read_le(data, 63, 1)),
            "dRel": _read_le(data, 0, 12, scale=0.0625),
            "vRel": _read_le(data, 12, 12, scale=0.0625, offset=-128.0),
            "yRel": _read_le(data, 24, 11, scale=0.125, offset=-128.0),
          }

      elif _OBJECT_B_BASE <= address < _OBJECT_B_BASE + _NUM_SLOTS * 2 and (address & 1) == 1:
        if len(data) == 8:
          slot = (address - _OBJECT_B_BASE) // 2
          self._object_b[slot] = {"index2": int(_read_le(data, 63, 1))}
          if address == _TRIGGER_ID:
            triggered = True

    now = self._time_fn()
    if not triggered:
      if now - self._status_time <= _STATUS_TIMEOUT_S:
        return None
      if now - self._last_fault_time < _FAULT_REPORT_PERIOD_S:
        return None
      self._last_fault_time = now
      return self._fault_result(unavailable=True)

    status_fresh = self._have_status and now - self._status_time <= _STATUS_TIMEOUT_S
    if not status_fresh or self._sensor_unavailable or self._sensor_fault:
      return self._fault_result(unavailable=not self._sensor_fault)

    expected_slots = set(range(_NUM_SLOTS))
    trigger_index = self._object_b[_NUM_SLOTS - 1]["index2"]
    set_complete = self._object_a.keys() == expected_slots and self._object_b.keys() == expected_slots
    indexes_coherent = set_complete and all(
      self._object_a[slot]["index"] == trigger_index and self._object_b[slot]["index2"] == trigger_index
      for slot in expected_slots
    )
    if not indexes_coherent:
      return self._fault_result(unavailable=True)

    current_tracks: set[int] = set()
    for slot in range(_NUM_SLOTS):
      track_id = slot + 1
      point_a = self._object_a.get(slot)
      point_b = self._object_b.get(slot)
      if (point_a is None or point_b is None
          or not point_a["tracked"] or not point_a["valid"] or not point_a["measured"]
          or point_a["index"] != point_b["index2"]):
        continue

      current_tracks.add(track_id)
      if track_id not in self.pts:
        self.pts[track_id] = structs.RadarData.RadarPoint()
        self.pts[track_id].trackId = track_id

      point = self.pts[track_id]
      point.dRel = point_a["dRel"]
      point.yRel = point_a["yRel"]
      point.vRel = point_a["vRel"]
      # BrownPanda reserves these unproven BYD motion fields on the wire.
      # NaN is OpenDBC's canonical marker for an unavailable optional field.
      motion = point if "aRel" in point.schema.fields else point.deprecated
      motion.aRel = float("nan")
      motion.yvRel = float("nan")
      motion.measured = True

    for track_id in list(self.pts):
      if track_id not in current_tracks:
        del self.pts[track_id]

    result = structs.RadarData()
    result.points = list(self.pts.values())
    self._clear_pending()
    return result

  def _fault_result(self, unavailable: bool) -> structs.RadarDataT:
    self.pts.clear()
    self._clear_pending()
    result = structs.RadarData()
    if unavailable:
      result.errors.radarUnavailableTemporary = True
    else:
      result.errors.radarFault = True
    result.points = []
    return result

  def _clear_pending(self) -> None:
    self._object_a.clear()
    self._object_b.clear()

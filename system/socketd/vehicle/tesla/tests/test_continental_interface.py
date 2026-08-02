from types import SimpleNamespace

import pytest

from cereal import car
from openpilot.system.socketd.vehicle.tesla.continental_interface import ContinentalRadarInterface
from openpilot.system.socketd.vehicle.tesla.values import CANBUS


def _set_le(data: bytearray, start_bit: int, size: int, raw: int) -> None:
  for i in range(size):
    if raw & (1 << i):
      bit = start_bit + i
      data[bit >> 3] |= 1 << (bit & 7)


def _message(address: int, data: bytes, src: int = CANBUS.party):
  return SimpleNamespace(address=address, dat=data, src=src)


def _point_pair(slot: int, index: int = 1, wire_a_rel: float = 0.0,
                wire_yv_rel: float = 0.0, measured: bool = True):
  a = bytearray(8)
  b = bytearray(8)
  _set_le(a, 0, 12, round(42.5 / 0.0625))
  _set_le(a, 12, 12, round((-3.0 + 128.0) / 0.0625))
  _set_le(a, 24, 11, round((2.25 + 128.0) / 0.125))
  _set_le(a, 40, 10, round((wire_a_rel + 16.0) / 0.03125))
  _set_le(a, 55, 1, 1)
  _set_le(a, 61, 1, int(measured))
  _set_le(a, 62, 1, 1)
  _set_le(a, 63, 1, index)
  _set_le(b, 0, 10, round((wire_yv_rel + 64.0) / 0.125))
  _set_le(b, 63, 1, index)
  return (
    _message(0x410 + slot * 2, bytes(a)),
    _message(0x411 + slot * 2, bytes(b)),
  )


def test_shared_party_bus_all_slots_and_official_geometry():
  radar = ContinentalRadarInterface(car.CarParams.new_message())

  # Firmware paces status and object pairs, so status usually arrives in an
  # earlier vehicled update than the 0x45F trigger.
  assert radar.update([_message(0x401, bytes(8))]) is None

  point_a, point_b = _point_pair(7)
  ret = radar.update([point_a, point_b, _message(0x45F, bytes(8))])

  assert ret is not None
  assert not ret.errors
  assert len(ret.points) == 1
  point = ret.points[0]
  assert point.trackId == 8
  assert point.dRel == pytest.approx(42.5)
  assert point.yRel == pytest.approx(2.25)
  assert point.vRel == pytest.approx(-3.0)
  assert point.aRel == pytest.approx(0.0)
  assert point.yvRel == pytest.approx(0.0)
  assert point.measured


def test_status_unavailable_and_bus_filter():
  radar = ContinentalRadarInterface(car.CarParams.new_message())
  unavailable = bytearray(8)
  _set_le(unavailable, 23, 1, 1)

  # Stock radar bus 1 is deliberately ignored by the EOP10 two-bus consumer.
  assert radar.update([_message(0x401, bytes(8), src=1)]) is None
  assert radar.update([_message(0x401, bytes(unavailable))]) is None
  point_a, point_b = _point_pair(0)
  ret = radar.update([point_a, point_b, _message(0x45F, bytes(8))])

  assert ret is not None
  assert list(ret.errors) == [car.RadarData.Error.fault]
  assert not ret.points


def test_reserved_motion_fields_are_not_accepted_as_measurements():
  radar = ContinentalRadarInterface(car.CarParams.new_message())
  assert radar.update([_message(0x401, bytes(8))]) is None

  point_a, point_b = _point_pair(0, wire_a_rel=7.0, wire_yv_rel=-8.0)
  ret = radar.update([point_a, point_b, _message(0x45F, bytes(8))])

  assert ret is not None
  assert len(ret.points) == 1
  assert ret.points[0].aRel == pytest.approx(0.0)
  assert ret.points[0].yvRel == pytest.approx(0.0)


def test_stale_status_suppresses_complete_object_set():
  now = [100.0]
  radar = ContinentalRadarInterface(car.CarParams.new_message(), time_fn=lambda: now[0])
  assert radar.update([_message(0x401, bytes(8))]) is None

  now[0] += 0.201
  point_a, point_b = _point_pair(0)
  ret = radar.update([point_a, point_b, _message(0x45F, bytes(8))])

  assert ret is not None
  assert list(ret.errors) == [car.RadarData.Error.fault]
  assert not ret.points


def test_new_status_discards_incomplete_previous_pair():
  radar = ContinentalRadarInterface(car.CarParams.new_message())
  point_a, point_b = _point_pair(4)

  assert radar.update([_message(0x401, bytes(8)), point_a]) is None
  # A new set starts before the matching B arrives. It must not combine with
  # the stale A even if a future one-bit index happens to match.
  assert radar.update([_message(0x401, bytes(8)), point_b, _message(0x45F, bytes(8))]) is not None
  assert not radar.pts


def test_short_trigger_and_unmeasured_object_are_rejected():
  radar = ContinentalRadarInterface(car.CarParams.new_message())
  assert radar.update([_message(0x401, bytes(8))]) is None
  point_a, point_b = _point_pair(0)

  assert radar.update([point_a, point_b, _message(0x45F, bytes(7))]) is None

  # Start a clean set after the rejected trigger; an estimated/unmeasured
  # object is outside the BrownPanda contract even if Valid/Tracked are set.
  assert radar.update([_message(0x401, bytes(8))]) is None
  point_a, point_b = _point_pair(0, measured=False)
  ret = radar.update([point_a, point_b, _message(0x45F, bytes(8))])
  assert ret is not None
  assert not ret.points


def test_absent_stream_times_out_without_gateway_status_frames():
  now = [100.0]
  radar = ContinentalRadarInterface(car.CarParams.new_message(), time_fn=lambda: now[0])

  assert radar.update([]) is None
  now[0] += 0.201
  ret = radar.update([])
  assert ret is not None
  assert list(ret.errors) == [car.RadarData.Error.fault]
  assert not ret.points

  # Fault publication is rate limited even though update runs at control rate.
  assert radar.update([]) is None
  now[0] += 0.101
  assert radar.update([]) is not None

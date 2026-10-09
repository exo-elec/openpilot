from opendbc.car import structs
from opendbc.car.can_definitions import CanData
from nagaspilot.runtime.brownpanda_radar import BrownPandaRadarInterface


def stream(index=0, bus=0):
  messages = [CanData(0x401, bytes(8), bus)]
  for slot in range(40):
    a = (320 | (2048 << 12) | (1024 << 24) | (1 << 55) | (1 << 61) | (1 << 62) | (index << 63)).to_bytes(8, 'little')
    messages.extend([CanData(0x410 + slot * 2, a, bus), CanData(0x411 + slot * 2, (index << 63).to_bytes(8, 'little'), bus)])
  return messages


def radar():
  return BrownPandaRadarInterface(structs.CarParams(), time_fn=lambda: 0)


def test_complete_coherent_stream_and_index_wrap():
  instance = radar()
  for index in (0, 1, 0):
    result = instance.update(stream(index))
    assert len(result.points) == 40
    assert result.points[0].dRel == 20
    assert result.points[0].vRel == 0
    assert result.points[0].yRel == 0


def test_incomplete_or_mixed_sets_never_publish_tracks():
  for messages in (stream()[:-3] + stream()[-1:], stream()):
    if len(messages) == 81:
      messages[2] = CanData(0x411, (1 << 63).to_bytes(8, 'little'), 0)
    result = radar().update(messages)
    assert not result.points
    assert result.errors.radarUnavailableTemporary
  assert radar().update(stream(bus=1)) is None


def test_sensor_fault_clears_tracks():
  instance = radar()
  assert len(instance.update(stream()).points) == 40
  messages = stream()
  messages[0] = CanData(0x401, (1 << 27).to_bytes(8, 'little'), 0)
  result = instance.update(messages)
  assert not result.points
  assert result.errors.radarFault

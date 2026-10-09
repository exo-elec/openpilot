"""OpenDBC Tesla parser adapter for the BrownPanda SocketCAN topology."""

import time

from opendbc.can import CANParser


TESLA_MESSAGE_ADDRESSES = (
  0x257, 0x118, 0x39D, 0x286, 0x311, 0x129, 0x2B9, 0x488, 0x370, 0x39B,
  0x3A8, 0x3B0, 0x3D0, 0x700,
)


class SimpleCANParser:
  """Small boundary wrapper; all decoding is performed by OpenDBC."""

  def __init__(self, dbc_name: str, signals, bus: int):
    self.bus = bus
    self.parser = CANParser(
      dbc_name, [(address, float('nan')) for address in TESLA_MESSAGE_ADDRESSES], bus)
    self.vl = self.parser.vl

  def update(self, can_messages: list[tuple[int, bytes, int]]) -> None:
    self.parser.update([(time.monotonic_ns(), can_messages)])
    self.vl = self.parser.vl

  def update_strings(self, can_strings):
    for can_list in can_strings:
      self.update(can_list)

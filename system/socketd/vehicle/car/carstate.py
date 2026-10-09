"""Socketd vehicle-state adapter backed by OpenDBC Tesla CarState."""
from __future__ import annotations

import time
from typing import Any

from cereal import car
from opendbc.car.tesla.carstate import CarState as OpenDBCTeslaCarState


class CarState:
  """Keep EOP's daemon-facing state object while delegating CAN parsing."""

  def __init__(self, CP: car.CarParams):
    self._state = OpenDBCTeslaCarState(CP)
    self.can_parsers = self._state.get_can_parsers(CP)
    self.out = car.CarState.new_message()
    self.hands_on_level = 0
    self.das_control: dict[str, Any] = {}

  @staticmethod
  def get_can_parsers(CP):
    return OpenDBCTeslaCarState.get_can_parsers(CP)

  def update(self, can_packets: list[Any]) -> car.CarState:
    # opendbc's CANParser takes [(nanos, [(address, data, bus), ...])]; socketd hands over the flat frame list.
    can_strings = [(time.monotonic_ns(), can_packets)]
    for parser in self.can_parsers.values():
      parser.update(can_strings)
    self.out = self._state.update(self.can_parsers)
    self.hands_on_level = self._state.hands_on_level
    self.das_control = self._state.das_control or {}
    return self.out

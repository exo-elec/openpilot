"""BrownPanda adapter for the pinned OpenDBC Tesla controller."""

from cereal import car
from opendbc.car import Bus
from opendbc.car.tesla.carcontroller import CarController as OpenDBCTeslaCarController


class CarController:
  """Keep EOP's daemon boundary while using OpenDBC for Tesla CAN output."""

  def __init__(self, CP: car.CarParams):
    self._controller = OpenDBCTeslaCarController(
      {Bus.party: "tesla_model3_party"}, CP)

  def update(self, CC: car.CarControl, CS: car.CarState, now_nanos: int):
    return self._controller.update(CC, CS, now_nanos)

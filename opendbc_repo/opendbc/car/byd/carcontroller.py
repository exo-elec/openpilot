from opendbc.car.interfaces import CarControllerBase


class CarController(CarControllerBase):
  """Passive controller used until BYD actuation and Panda safety are validated."""

  def update(self, CC, CS, now_nanos):
    del CS, now_nanos
    return CC.actuators.as_builder(), []

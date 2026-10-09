"""selfdrived must construct and step on a branch that has no radar4d service (EOP10/01M), with card.py's CarParams."""
from openpilot.system.socketd.vehicle.car.card import Car


class _Params:
  def get(self, key, *args, **kwargs):
    return None

  def get_bool(self, key, *args, **kwargs):
    return False


def _car_params():
  car = Car.__new__(Car)
  car.params = _Params()
  return Car._get_vehicle_params(car)


def test_selfdrived_constructs_and_steps(tmp_path, monkeypatch):
  import openpilot.selfdrive.controls.lib.surface_quality_db as sq
  monkeypatch.setattr(sq.SurfaceQualityDB, 'DB_PATH', tmp_path / 'surface.db')
  from openpilot.selfdrive.selfdrived.selfdrived import SelfdriveD
  sd = SelfdriveD(_car_params())
  for _ in range(3):
    sd.step()

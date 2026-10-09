"""controlsd must construct and run update/state_control with card.py's CarParams on opendbc's schema."""
from openpilot.common.params import Params
from openpilot.system.socketd.vehicle.car.card import Car


class _Params:
  def get(self, key, *args, **kwargs):
    return None

  def get_bool(self, key, *args, **kwargs):
    return False


def test_controlsd_constructs_and_steps(tmp_path, monkeypatch):
  import openpilot.selfdrive.controls.lib.surface_quality_db as sq
  monkeypatch.setattr(sq.SurfaceQualityDB, 'DB_PATH', tmp_path / 'surface.db')
  car = Car.__new__(Car)
  car.params = _Params()
  Params().put("CarParams", Car._get_vehicle_params(car).to_bytes())
  from openpilot.selfdrive.controls.controlsd import Controls
  controls = Controls()
  for _ in range(3):
    controls.update()
    controls.state_control()

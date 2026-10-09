"""card.py's CarState path must work against opendbc's CANParser (update, not the old update_strings)."""
from cereal import car
from opendbc.car.can_definitions import CanData
from openpilot.system.socketd.vehicle.car.card import Car


def _car(tmp_path, monkeypatch):
  import openpilot.selfdrive.controls.lib.surface_quality_db as sq
  monkeypatch.setattr(sq.SurfaceQualityDB, 'DB_PATH', tmp_path / 'surface.db')
  return Car()


def test_car_state_update_accepts_empty_and_real_batches(tmp_path, monkeypatch):
  c = _car(tmp_path, monkeypatch)
  for frames in ([], [CanData(0x118, bytes(8), 0), CanData(0x3f8, bytes(8), 1)]):
    CS = c.CS.update(frames)
  assert CS.vEgo == 0.0
  c.state_publish(CS)


def test_controls_update_runs_with_a_torque_actuator(tmp_path, monkeypatch):
  c = _car(tmp_path, monkeypatch)
  CS = c.CS.update([])
  CC = car.CarControl.new_message()
  CC.enabled = True
  CC.latActive = True
  CC.actuators.torque = 0.1
  CC.actuators.steeringAngleDeg = 1.0
  c.controls_update(CS, CC)

"""card.py's CarParams builder must only write members that exist in opendbc's car.capnp."""
from openpilot.system.socketd.vehicle.car.card import Car


class _Params:
  def get(self, key, *args, **kwargs):
    return None

  def get_bool(self, key, *args, **kwargs):
    return False


def _build(openpilot_enabled):
  car = Car.__new__(Car)
  car.params = _Params()
  if openpilot_enabled:
    car.params.get_bool = lambda key, *a, **k: key == "OpenpilotEnabledToggle"
  return Car._get_vehicle_params(car)


def test_car_params_build_and_serialise():
  cp = _build(openpilot_enabled=False)
  cp.to_bytes()
  assert cp.carFingerprint == "TESLA_MODEL_3"


def test_safety_model_stays_in_the_slot_the_fork_used_and_safety_configs_stay_empty():
  # selfdrived compares panda safety state against CP.safetyConfigs; populating it would add a mismatch check.
  cp = _build(openpilot_enabled=True)
  assert len(cp.safetyConfigs) == 0
  assert str(cp.deprecated.safetyModel) == "tesla"
  assert str(_build(openpilot_enabled=False).deprecated.safetyModel) == "noOutput"

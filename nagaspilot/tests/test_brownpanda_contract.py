from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def test_eop_gateway_protocol_has_two_channels_and_no_synthetic_radar():
  values = (ROOT / 'system/socketd/vehicle/tesla/values.py').read_text()
  assert 'party = 0' in values and 'autopilot_party = 2' in values
  controller = (ROOT / 'system/socketd/vehicle/car/carcontroller.py').read_text()
  assert 'tesla_model3_party' in controller
  assert 'opendbc' not in controller


def test_eop_owns_car_schema_without_opendbc_import():
  assert 'import opendbc' not in (ROOT / 'cereal/__init__.py').read_text()
  assert 'struct CarParams' in (ROOT / 'cereal/car.capnp').read_text()

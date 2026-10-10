"""Compare the standalone EOP protocol with the pinned former OpenDBC output."""
import ast
import json
from pathlib import Path
from types import SimpleNamespace as NS
import numpy as np
from cereal import car
from openpilot.system.socketd.vehicle.protocol.packer import CANPacker
from openpilot.system.socketd.vehicle.protocol.teslacan import TeslaCAN
from openpilot.system.socketd.vehicle.car.carstate import CarState
from openpilot.system.socketd.vehicle.car.carcontroller import CarController

ROOT = Path(__file__).resolve().parents[2]


def test_can_output_matches_482_reference_messages_byte_for_byte():
  cases = json.loads(Path(__file__).with_name('eop_protocol_golden.json').read_text())
  cp = car.CarParams.new_message()
  protocol = TeslaCAN(cp, CANPacker('tesla_model3_party'))
  for case in cases:
    cp.flags = case['flags']
    address, data, bus = getattr(protocol, case['method'])(*case['args'])
    assert (address, data.hex(), bus) == (case['address'], case['data'], case['bus'])


def test_vehicle_state_matches_reference_over_speed_braking_steering_and_bsd_changes():
  cases = json.loads(Path(__file__).with_name('eop_state_golden.json').read_text())
  state = CarState(car.CarParams.new_message(carFingerprint='TESLA_MODEL_3', flags=4))
  for i, case in enumerate(cases):
    messages = [(address, bytes.fromhex(data), bus) for address, data, bus in case['messages']]
    for parser in state.can_parsers.values():
      parser.update([(1_000_000_000 + i * 10_000_000, messages)])
    assert state.decode(state.can_parsers).to_dict() == case['expected']


def test_full_controller_retains_limits_driver_override_counters_and_aeb():
  cases = json.loads(Path(__file__).with_name('eop_controller_golden.json').read_text())
  for case in cases:
    cp = car.CarParams.new_message(carFingerprint='TESLA_MODEL_3', flags=case['flags'],
                                  openpilotLongitudinalControl=case['longitudinal'])
    controller = CarController(cp)
    for frame, entry in enumerate(case['frames']):
      cc = car.CarControl.new_message(latActive=entry['lat'], longActive=entry['long'])
      cc.actuators.steeringAngleDeg = entry['angle']
      cc.actuators.accel = entry['accel']
      cc.cruiseControl.cancel = entry['cancel']
      cs = NS(out=car.CarState.new_message(vEgoRaw=entry['speed'], vEgo=entry['speed'],
                                          steeringAngleDeg=entry['angle'] / 3),
              hands_on_level=entry['hands'], das_control={'DAS_controlCounter': frame % 8})
      actuators, messages = controller.update(cc.as_reader(), cs, frame * 10_000_000)
      assert actuators.to_dict() == entry['actuators']
      assert [[a, d.hex(), b] for a, d, b in messages] == entry['messages']


def test_shared_vehicle_math_preserves_eop_parameters_and_fallbacks():
  from openpilot.system.socketd.vehicle.car.vehicle_model import VehicleModel
  cases = json.loads(Path(__file__).with_name('eop_vehicle_model_golden.json').read_text())
  for case in cases:
    model = VehicleModel(car.CarParams.new_message(**case['physical']))
    model.update_params(case['stiffness'], case['ratio'])
    for entry in case['frames']:
      args = entry['steering'], entry['speed'], entry['roll']
      assert model.calc_curvature(*args) == entry['curvature']
      assert model.yaw_rate(*args) == entry['yaw']
      np.testing.assert_array_equal(model.steady_state_sol(*args), entry['steady'])


def test_vehicle_protocol_and_cereal_have_no_opendbc_imports():
  files = list((ROOT / 'system/socketd/vehicle').rglob('*.py')) + [ROOT / 'cereal/__init__.py']
  for file in files:
    for node in ast.walk(ast.parse(file.read_text())):
      modules = [a.name for a in node.names] if isinstance(node, ast.Import) else [node.module or ''] if isinstance(node, ast.ImportFrom) else []
      assert not any(name == 'opendbc' or name.startswith('opendbc.') for name in modules), str(file)

from pathlib import Path
import opendbc


def test_brownpanda_uses_two_channel_tesla_contract_without_synthetic_radar():
  root = Path(opendbc.__file__).parent
  values = (root / 'car/tesla/values.py').read_text()
  interface = (root / 'car/tesla/interface.py').read_text()
  assert 'party = 0' in values
  assert 'autopilot_party = 2' in values
  assert 'tesla_model3_party' in values
  assert 'brownpanda_radar_present' not in interface
  assert 'BROWNPANDA_RADAR_CARS' not in interface


def test_vehicle_blindspot_flags_remain_available():
  root = Path(opendbc.__file__).parent
  carstate = (root / 'car/tesla/carstate.py').read_text()
  assert 'leftBlindspot' in carstate and 'rightBlindspot' in carstate

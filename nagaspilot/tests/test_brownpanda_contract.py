from pathlib import Path


def test_brownpanda_uses_two_channel_tesla_contract():
  root = Path(__file__).resolve().parents[2]
  values = (root / "opendbc" / "car" / "tesla" / "values.py").read_text()
  interface = (root / "opendbc" / "car" / "tesla" / "interface.py").read_text()

  assert "party = 0" in values
  assert "autopilot_party = 2" in values
  assert "tesla_model3_party" in values
  assert "brownpanda_radar_present" in interface
  assert "fingerprint.get(CANBUS.party" in interface


def test_ngp10_adds_brownpanda_party_bus_radar_in_opendbc():
  root = Path(__file__).resolve().parents[2]
  interface = (root / "opendbc" / "car" / "tesla" / "interface.py").read_text()
  radar = (root / "opendbc" / "car" / "tesla" / "radar_interface.py").read_text()

  assert "RadarInterface = RadarInterface" in interface
  assert "BROWNPANDA_RADAR_CARS" in interface
  assert "CANBUS.party" in radar
  assert "_STATUS_ID = 0x401" in radar
  assert "_TRIGGER_ID" in radar and "0x45F" in radar
  assert "radarUnavailableTemporary" in radar

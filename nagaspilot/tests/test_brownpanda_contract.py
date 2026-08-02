from pathlib import Path


def test_tc275_uses_upstream_two_channel_tesla_contract():
  root = Path(__file__).resolve().parents[2]
  values = (root / "opendbc" / "car" / "tesla" / "values.py").read_text()
  interface = (root / "opendbc" / "car" / "tesla" / "interface.py").read_text()

  assert "party = 0" in values
  assert "autopilot_party = 2" in values
  assert "tesla_model3_party" in values
  assert "ret.radarUnavailable = True" in interface

from openpilot.common.wifi_band import is_5ghz, lan_band_for, read_band_policy


def _conf(tmp_path, text):
  p = tmp_path / "wifi-band.conf"
  p.write_text(text)
  return str(p)


def test_no_file_means_unpinned(tmp_path):
  missing = str(tmp_path / "missing.conf")
  assert read_band_policy(missing) is None
  assert lan_band_for(True, missing) is None
  assert lan_band_for(False, missing) is None


def test_single_channel_radio_pins_2g4(tmp_path):
  path = _conf(tmp_path, "# written by setup_wifi_dualwan.sh\nDBDC=no\nLAN_BAND=bg\nAP_CHANNEL=6\n")
  assert read_band_policy(path) == {"DBDC": "no", "LAN_BAND": "bg", "AP_CHANNEL": "6"}
  assert lan_band_for(True, path) == "bg"
  assert lan_band_for(False, path) == "bg"


def test_dbdc_uses_5ghz_when_available(tmp_path):
  path = _conf(tmp_path, "DBDC=yes\nLAN_BAND=a\n")
  assert lan_band_for(True, path) == "a"
  assert lan_band_for(False, path) is None     # 2.4GHz-only hotspot still joinable


def test_unknown_policy_is_unpinned(tmp_path):
  assert lan_band_for(True, _conf(tmp_path, "DBDC=maybe\n")) is None
  assert lan_band_for(True, _conf(tmp_path, "\n")) is None


def test_is_5ghz():
  assert not is_5ghz(2412)
  assert not is_5ghz(2484)
  assert is_5ghz(5180)
  assert not is_5ghz(0)

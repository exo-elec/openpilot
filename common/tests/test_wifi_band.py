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


def test_5ghz_lan(tmp_path):
  # 02M (setup_wifi_dualwan.sh) and 01M (setup_wifi_lan.sh) both write this.
  path = _conf(tmp_path, "# written by setup_wifi_dualwan.sh\nLAN_BAND=a\nAP_CHANNEL=6\n")
  assert read_band_policy(path) == {"LAN_BAND": "a", "AP_CHANNEL": "6"}
  assert lan_band_for(True, path) == "a"
  assert lan_band_for(False, path) is None     # 2.4GHz-only network still joinable


def test_2g4_lan(tmp_path):
  path = _conf(tmp_path, "LAN_BAND=bg\n")
  assert lan_band_for(True, path) == "bg"
  assert lan_band_for(False, path) == "bg"


def test_unknown_policy_is_unpinned(tmp_path):
  assert lan_band_for(True, _conf(tmp_path, "LAN_BAND=x\n")) is None
  assert lan_band_for(True, _conf(tmp_path, "\n")) is None


def test_is_5ghz():
  assert not is_5ghz(2412)
  assert not is_5ghz(2484)
  assert is_5ghz(5180)
  assert not is_5ghz(0)


def test_wifi_manager_pins_5ghz_and_its_bssid(tmp_path, monkeypatch):
  import asyncio
  import pytest
  pytest.importorskip("dbus_next")
  from openpilot.common import wifi_band
  from openpilot.system.ui.lib import wifi_manager as wm

  conf = _conf(tmp_path, "LAN_BAND=a\n")
  monkeypatch.setattr(wm, "lan_band_for", lambda has_5ghz: wifi_band.lan_band_for(has_5ghz, conf))

  sent = []

  class _Iface:
    async def call_add_and_activate_connection(self, connection, device, ap):
      sent.append(connection)

  async def _get_interface(*_a):
    return _Iface()

  m = wm.WifiManager.__new__(wm.WifiManager)
  m.callbacks = wm.WifiManagerCallbacks()
  m.saved_connections = {}
  m.device_path = "/dev/wlan0"
  m._get_interface = _get_interface
  m.networks = [
    wm.NetworkInfo(ssid="dual", strength=80, is_connected=False, security_type=None, path="/a",
                   bssid="aa:aa", bssid_5ghz="bb:bb", strength_5ghz=60),
    wm.NetworkInfo(ssid="only24", strength=70, is_connected=False, security_type=None, path="/b",
                   bssid="cc:cc"),
  ]

  asyncio.run(m.connect_to_network("dual", "pw", bssid="aa:aa"))
  asyncio.run(m.connect_to_network("only24", "pw", bssid="cc:cc"))
  wifi = [c['802-11-wireless'] for c in sent]
  assert wifi[0]['band'].value == 'a' and bytes(wifi[0]['bssid'].value) == b"bb:bb"
  assert 'band' not in wifi[1] and bytes(wifi[1]['bssid'].value) == b"cc:cc"

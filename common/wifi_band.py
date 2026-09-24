"""02M WiFi band plan for the vehicle's own LAN connections (wlan0).

02M is the only ExoPilot hardware with the ESP32 corner-radar hotspot:
`ap0` is a 2.4GHz access point for the ESP32 corner nodes (ESP32-S3 has no
5GHz radio) and `wlan0` joins the vehicle's own WiFi LAN, on 5GHz where the
radio can run two channels at once (DBDC). exopilot's
scripts/install/setup_wifi_dualwan.sh detects that and writes
/etc/exopilot/wifi-band.conf:

  DBDC=yes|no
  LAN_BAND=a|bg

This module turns that policy into the NetworkManager `802-11-wireless.band`
for a new wlan0 connection. No file (every other board, or 02M before
setup) means no band is set, i.e. unchanged behavior.
"""

from __future__ import annotations

WIFI_BAND_CONF = "/etc/exopilot/wifi-band.conf"


def read_band_policy(path: str = WIFI_BAND_CONF) -> dict[str, str] | None:
  try:
    with open(path) as f:
      lines = f.read().splitlines()
  except OSError:
    return None
  policy = {}
  for line in lines:
    line = line.strip()
    if not line or line.startswith('#') or '=' not in line:
      continue
    key, value = line.split('=', 1)
    policy[key.strip()] = value.strip()
  return policy or None


def lan_band_for(has_5ghz: bool, path: str = WIFI_BAND_CONF) -> str | None:
  """Band to pin a new wlan0 connection to, or None to leave it unpinned.

  - No DBDC: "bg". wlan0 and ap0 share one channel, and ap0 must stay on
    2.4GHz for the ESP32s, so wlan0 may never join 5GHz.
  - DBDC: "a" when the network is visible on 5GHz (the LAN plan), else
    unpinned, so a 2.4GHz-only hotspot can still be joined; with DBDC that
    no longer affects ap0.
  """
  policy = read_band_policy(path)
  if policy is None:
    return None
  if policy.get('DBDC') == 'no':
    return 'bg'
  if policy.get('DBDC') == 'yes':
    return 'a' if has_5ghz else None
  return None


def is_5ghz(frequency_mhz: int) -> bool:
  return frequency_mhz >= 4900

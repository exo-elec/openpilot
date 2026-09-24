"""WiFi band for the vehicle's own LAN connections (wlan0) on ExoPilot boards.

- 02M (AP6275S): `ap0` is a 2.4GHz hotspot for the ESP32 corner radars and
  `wlan0` uses 5GHz for the vehicle's LAN; the radio runs both bands at once.
  exopilot's scripts/install/setup_wifi_dualwan.sh writes the policy.
- 01M (RTL8822CE PCIe card): no hotspot; `wlan0` prefers 5GHz.
  exopilot's scripts/install/setup_wifi_lan.sh writes the policy.

Policy file /etc/exopilot/wifi-band.conf:

  LAN_BAND=a|bg

This module turns it into the NetworkManager `802-11-wireless.band` for a
new wlan0 connection. No file (other boards, or before setup) means no band
is set, i.e. unchanged behavior.
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

  - LAN_BAND=a: "a" when the network is visible on 5GHz, else unpinned so a
    2.4GHz-only network can still be joined (on 02M that does not affect
    ap0: the AP6275S runs both bands at once).
  - LAN_BAND=bg: "bg".
  """
  policy = read_band_policy(path)
  if policy is None:
    return None
  band = policy.get('LAN_BAND')
  if band == 'bg':
    return 'bg'
  if band == 'a':
    return 'a' if has_5ghz else None
  return None


def is_5ghz(frequency_mhz: int) -> bool:
  return frequency_mhz >= 4900

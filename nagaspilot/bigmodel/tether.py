"""The USB-tethering transport: the phone is the USB *device* and shares a network with the comma, which is the host.

Nothing to build on the phone side beyond a TCP listener: Android's "USB tethering" and an iPhone's Personal Hotspot present
an Ethernet-class interface (RNDIS, CDC-NCM/ECM, ipheth) to the host, and the phone is its gateway. The comma finds that interface
by its kernel driver, reads the phone's address from the routing table, and connects out to the app's listening socket. The
protocol is unchanged: the comma still sends HELLO and drives the frames.

Optional link (the default is the direct USB link, see docs/NAVPILOT_BIGMODEL.md D11). Requires the host kernel to carry the USB ethernet drivers (exopilot's `exopilot-usb-tether.config`; on AGNOS this is not known and
is the first thing to check on a comma: `zcat /proc/config.gz | grep -E 'USB_NET|IPHETH|RNDIS'`). The default route NetworkManager
may add for the tether interface must stay behind the comma's own uplink (route metric 900): see docs/NAVPILOT_BIGMODEL.md.
Standard library only.
"""
from __future__ import annotations

import logging
import os
import socket
import threading
from pathlib import Path

from nagaspilot.bigmodel import protocol as P

log = logging.getLogger('nagaspilot.bigmodel.tether')

NET_ROOT = Path('/sys/class/net')
ROUTE_FILE = Path('/proc/net/route')
# kernel drivers a phone's tethering interface binds to on a host
TETHER_DRIVERS = frozenset({'rndis_host', 'cdc_ncm', 'cdc_ether', 'cdc_eem', 'ipheth'})
# the cellular modem (Quectel EC25 on the ExoPilot boards) can also bind an Ethernet-class driver; ModemManager names its port wwan*.
# It is a network uplink, never the phone.
MODEM_PREFIXES = ('wwan', 'ppp')
POLL = 0.25


def tether_interfaces(root: Path = NET_ROOT) -> list[str]:
  """Network interfaces bound to a USB tethering driver, by the driver symlink in sysfs (not by name: names vary by udev),
  except a cellular modem's (wwan*/ppp*)."""
  found = []
  try:
    for dev in sorted(root.iterdir()):
      try:
        driver = os.path.basename(os.readlink(dev / 'device' / 'driver'))
      except OSError:
        continue
      if driver in TETHER_DRIVERS and not dev.name.startswith(MODEM_PREFIXES):
        found.append(dev.name)
  except OSError:
    pass
  return found


def gateway(ifname: str, route_file: Path = ROUTE_FILE) -> str | None:
  """The phone's address: the default gateway learned on [ifname] (a /proc/net/route hex, little-endian), or None."""
  try:
    lines = route_file.read_text().splitlines()[1:]
  except OSError:
    return None
  for line in lines:
    f = line.split()
    if len(f) >= 3 and f[0] == ifname and f[1] == '00000000' and int(f[3], 16) & 2:   # RTF_GATEWAY
      raw = bytes.fromhex(f[2])[::-1]
      return socket.inet_ntoa(raw)
  return None


class TetherListener:
  """Same interface as listener.Listener / ffs.FfsListener: take(timeout) gives a connected socket to the phone, or None.

  The phone's app listens on its tether address; this side dials. A phone that is not tethered, or an app that is not open,
  is simply 'not there yet'."""

  def __init__(self, port: int = P.DEFAULT_PORT, net_root: Path = NET_ROOT, route_file: Path = ROUTE_FILE, connect=socket.create_connection):
    self.port, self.net_root, self.route_file, self._connect = port, net_root, route_file, connect
    self.bound = True              # nothing to bind: the comma is the client
    self._stop = threading.Event()

  def start(self):
    return self

  def phone_address(self) -> str | None:
    for ifname in tether_interfaces(self.net_root):
      addr = gateway(ifname, self.route_file)
      if addr:
        return addr
    return None

  def take(self, timeout: float):
    deadline = timeout
    waited = 0.0
    while not self._stop.is_set():
      addr = self.phone_address()
      if addr:
        try:
          return self._connect((addr, self.port), timeout=min(2.0, max(timeout, 0.1)))
        except OSError as e:
          log.debug('phone at %s:%d not answering: %s', addr, self.port, e)
      if waited >= deadline:
        return None
      self._stop.wait(POLL)
      waited += POLL
    return None

  def close(self) -> None:
    self._stop.set()

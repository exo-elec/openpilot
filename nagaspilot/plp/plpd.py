"""plpd: serve PLP over the USB cable (iPhone mode) while the cable link is on.

Runs beside bigmodeld, which holds the USB port as a network gadget; this binds the comma's end of that network and answers the phone's hello, ping and locate messages.
On a comma 3 (no BLE) this is the phone's only door to the device apart from the big-model link. Android's bulk interface is not served here (it carries the big-model
protocol; see PLP_V5_SPEC.md, section 4.1). Standard library plus the NGP10 localization modules.

No cereal output yet: the phone's GNSS fix and map horizon are held in `LocateSink` for the localization code to read once the output message is decided.
"""
from __future__ import annotations

import logging
import signal
import threading

from nagaspilot.plp.cable import CablePLP
from nagaspilot.plp.hello import Hello
from nagaspilot.plp.locate import LocateSink

log = logging.getLogger('nagaspilot.plp.plpd')

NETWORK_MODE = 2             # ngp_bigmodel_link: the iPhone (CDC-NCM) cable; see bigmodel/gadget.py
REPORT_PERIOD_S = 60.0

SERVICES = (('core', 1, 1), ('locate', 1, 1))


def device_hello() -> Hello:
  return Hello(max_payload=16384, max_message=1 << 20, services=SERVICES, link='usb')


def build(sink: LocateSink, **server_kw) -> CablePLP:
  return CablePLP(device_hello(), sink.attach, **server_kw)


def main() -> None:
  logging.basicConfig(level=logging.INFO)
  sink = LocateSink()
  server = build(sink)
  stop = threading.Event()
  signal.signal(signal.SIGTERM, lambda *_: stop.set())
  signal.signal(signal.SIGINT, lambda *_: stop.set())
  server.start()
  try:
    while not stop.wait(REPORT_PERIOD_S):
      log.info('plpd: connected=%s horizons=%d fix=%s', server.session is not None, sink.horizons_taken, sink.latest_fix is not None)
  finally:
    server.stop()


if __name__ == '__main__':
  main()

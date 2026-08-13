#!/usr/bin/env python3
"""Pigeon GNSS Daemon for U-blox GPS modules.

Low-level UBX protocol handling and GPIO power control live in
`exopilot/hal/hal/drivers/gps`. This daemon opens the HAL, injects optional
AssistNow data from application params, and publishes raw UBX messages.
"""
from __future__ import annotations

import signal
import sys
import time
from pathlib import Path

from cereal import messaging
from openpilot.common.time_helpers import system_time_valid
from openpilot.common.params import Params
from openpilot.common.swaglog import cloudlog
from openpilot.system.hardware import HARDWARE, RK3588

# Low-level u-blox driver is board-support code in ExoPilot HAL.
try:
    from hal.drivers.gps import (
        TTYPigeon,
        init_baudrate,
        initialize_pigeon,
        deinitialize_pigeon,
        set_power,
        get_gps_device,
    )
except Exception:
    cloudlog.exception("PigeonD: failed to import hal.drivers.gps")
    TTYPigeon = None  # type: ignore[misc,assignment]


def _hal_available() -> bool:
    return TTYPigeon is not None


class PigeonD:
    """Pigeon GNSS Daemon - Multi-platform GPS driver."""

    def __init__(self):
        self.params = Params()
        self.pm = messaging.PubMaster(['ubloxRaw'])

        self.pigeon: TTYPigeon | None = None
        self.running = False

    def run(self) -> None:
        if not RK3588:
            cloudlog.warning("PigeonD: non-RK3588 platform, exiting")
            return

        if not _hal_available():
            cloudlog.warning("PigeonD: GPS HAL not available, exiting")
            return

        device = get_gps_device()
        if not Path(device).exists():
            cloudlog.warning(f"PigeonD: GPS device {device} not present, exiting")
            return

        set_power(False)
        time.sleep(0.1)
        set_power(True)
        time.sleep(0.5)

        self.pigeon = TTYPigeon(baudrate=9600)
        init_baudrate(self.pigeon)

        token = self.params.get('AssistNowToken')
        if not initialize_pigeon(
            self.pigeon,
            system_time_valid=system_time_valid(),
            assistnow_token=token,
            last_gps_lat=float(self.params.get('LastGPSLatitude') or 0.0),
            last_gps_lon=float(self.params.get('LastGPSLongitude') or 0.0),
            last_gps_alt=float(self.params.get('LastGPSAltitude') or 0.0),
        ):
            deinitialize_pigeon(self.pigeon)
            return

        self.params.put_bool('UbloxAvailable', True)
        self.running = True
        last_almanac = time.monotonic()

        while self.running:
            try:
                dat = self.pigeon.receive()
                if len(dat) > 0:
                    if dat[0] == 0x00:
                        cloudlog.warning("PigeonD: invalid data from ublox, re-initializing")
                        init_baudrate(self.pigeon)
                        if not initialize_pigeon(self.pigeon, system_time_valid=system_time_valid()):
                            break
                        continue

                    msg = messaging.new_message('ubloxRaw', len(dat), valid=True)
                    msg.ubloxRaw = dat[:]
                    self.pm.send('ubloxRaw', msg)
                else:
                    time.sleep(0.001)

                # Save almanac every 5 minutes
                if time.monotonic() - last_almanac > 300:
                    try:
                        self.pigeon.send(b"\xB5\x62\x09\x14\x04\x00\x00\x00\x00\x00\x21\xEC")
                        last_almanac = time.monotonic()
                    except Exception:
                        cloudlog.warning("PigeonD: periodic almanac save failed")
            except Exception:
                cloudlog.exception("PigeonD: error in receive loop")
                time.sleep(0.1)

        deinitialize_pigeon(self.pigeon)
        sys.exit(0)

    def stop(self) -> None:
        self.running = False


def main():
    daemon = PigeonD()

    def handler(sig, frame):
        daemon.stop()

    signal.signal(signal.SIGINT, handler)
    signal.signal(signal.SIGTERM, handler)
    daemon.run()


if __name__ == "__main__":
    main()

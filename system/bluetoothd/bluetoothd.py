#!/usr/bin/env python3
"""Bluetooth daemon for OpenPilot.

Manages both Classic SPP (RFCOMM, for legacy OBD scanners) and BLE GATT
(Nordic UART Service, for NavPilot on iOS + Android) simultaneously.
"""
from __future__ import annotations

import threading
import time

try:
    import warnings
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", ImportWarning)
        import dbus
        import dbus.mainloop.glib
        from gi.repository import GLib
    DBUS_AVAILABLE = True
except ImportError:
    dbus = None
    GLib = None
    DBUS_AVAILABLE = False

from openpilot.common.params import Params
from openpilot.common.swaglog import cloudlog
from openpilot.system.bluetoothd import spp, pairing_agent, ble_gatt, ncp_session, ble_central


def _set_adapter_name(bus: dbus.Bus, name: str) -> None:
    """Set the Bluetooth adapter's public name (shown to phones during scan)."""
    try:
        adapter = bus.get_object('org.bluez', '/org/bluez/hci0')
        props = dbus.Interface(adapter, 'org.freedesktop.DBus.Properties')
        props.Set('org.bluez.Adapter1', 'Alias', dbus.String(name))
        cloudlog.info(f'bluetoothd: adapter name set to "{name}"')
    except Exception as e:
        cloudlog.warning(f'bluetoothd: could not set adapter name: {e}')


def _set_adapter_discoverable(bus: dbus.Bus, discoverable: bool = True,
                               timeout: int = 0) -> None:
    """Make adapter discoverable (timeout=0 → always discoverable)."""
    try:
        adapter = bus.get_object('org.bluez', '/org/bluez/hci0')
        props = dbus.Interface(adapter, 'org.freedesktop.DBus.Properties')
        props.Set('org.bluez.Adapter1', 'Discoverable', dbus.Boolean(discoverable))
        props.Set('org.bluez.Adapter1', 'DiscoverableTimeout', dbus.UInt32(timeout))
        props.Set('org.bluez.Adapter1', 'Pairable', dbus.Boolean(True))
        props.Set('org.bluez.Adapter1', 'PairableTimeout', dbus.UInt32(0))
    except Exception as e:
        cloudlog.warning(f'bluetoothd: discoverable setup error: {e}')


class BluetoothD:
    """Main Bluetooth daemon — Classic SPP + BLE GATT simultaneously."""

    def __init__(self):
        self.params = Params()
        self.enabled = self.params.get_bool('EOPBluetoothEnabled')

        # GLib main loop (required for D-Bus callbacks — pairing agent + GATT)
        self._glib_loop: GLib.MainLoop | None = None
        self._glib_thread: threading.Thread | None = None

        if DBUS_AVAILABLE:
            dbus.mainloop.glib.DBusGMainLoop(set_as_default=True)
            self.bus = dbus.SystemBus()
        else:
            self.bus = None

        # BLE central for ESP32 corner radars — self-gates on EOPBluetoothRadarEnabled.
        # Created BEFORE the NCP session so the session can serve its pairing
        # service-tool messages (RADAR_PAIR_CONTROL/STATUS) against it.
        self.ble_central = ble_central.BLECentral(params=self.params)

        # ONE shared NCP session — ensures a single PubMaster per service in this process
        self._session = ncp_session.NCPSession(params=self.params, ble_central=self.ble_central)

        self.sppd  = spp.SPPD(params=self.params,      session=self._session)
        self.gattd = ble_gatt.GATTD(params=self.params, session=self._session)
        self._pairing_agent = None

    def _device_name(self) -> str:
        name = self.params.get('EOPDeviceName')
        if name:
            return name.decode() if isinstance(name, bytes) else name
        return 'EXOPILOT'

    def run(self) -> None:
        if not self.enabled:
            cloudlog.info('bluetoothd: disabled')
            return

        # Start GLib main loop in background thread (needed for D-Bus callbacks)
        if DBUS_AVAILABLE and GLib:
            self._glib_loop = GLib.MainLoop()
            self._glib_thread = threading.Thread(
                target=self._glib_loop.run, daemon=True, name='glib_main'
            )
            self._glib_thread.start()
            cloudlog.info('bluetoothd: GLib main loop started')

        # Set device name; discoverable for the pairing window then go dark
        if self.bus:
            _set_adapter_name(self.bus, self._device_name())
            _raw = self.params.get('EOPBluetoothPairWindow') or b'300'
            pair_window = int(_raw.decode() if isinstance(_raw, bytes) else _raw)
            _set_adapter_discoverable(self.bus, discoverable=True, timeout=pair_window)
            cloudlog.info(f'bluetoothd: discoverable for {pair_window}s pairing window')

        # Register BlueZ pairing agent (DisplayOnly → phone must type PIN)
        self._pairing_agent = pairing_agent.register_agent()
        if self._pairing_agent:
            cloudlog.info('bluetoothd: pairing agent registered (DisplayOnly)')
        else:
            cloudlog.warning('bluetoothd: pairing agent not available')

        # Start shared NCP session (one telemetry loop, one pub/sub set)
        self._session.start()
        cloudlog.info('bluetoothd: NCP session started')

        # Start Classic SPP (RFCOMM) — legacy OBD scanners + NavPilot fallback
        self.sppd.run()
        cloudlog.info('bluetoothd: SPP started')

        # Start BLE GATT (Nordic UART) — NavPilot on iOS + Android
        if self.bus:
            if self.gattd.setup(self.bus):
                self.gattd.start()
                cloudlog.info('bluetoothd: BLE GATT started')
            else:
                cloudlog.warning('bluetoothd: BLE GATT not available')

        # Start BLE central for ESP32 corner radars — only when its param is on.
        # When enabled, ble_central is the SOLE radar2d publisher (msgq allows
        # one publisher per service — see ble_central.py docstring warning).
        if self.bus and self.ble_central.enabled:
            if self.ble_central.setup(self.bus):
                self.ble_central.start()
                cloudlog.info('bluetoothd: BLE central (corner radars) started')
            else:
                cloudlog.warning('bluetoothd: BLE central not available')

        cloudlog.info('bluetoothd: running')

        try:
            while True:
                time.sleep(1)
        except KeyboardInterrupt:
            pass
        finally:
            self.stop()

    def stop(self) -> None:
        cloudlog.info('bluetoothd: stopping')
        self._session.stop()
        self.gattd.stop()
        self.ble_central.stop()
        pairing_agent.unregister_agent(self._pairing_agent)
        self.sppd.stop()
        if self._glib_loop:
            self._glib_loop.quit()
        cloudlog.info('bluetoothd: stopped')


def main():
    daemon = BluetoothD()
    daemon.run()


if __name__ == '__main__':
    main()

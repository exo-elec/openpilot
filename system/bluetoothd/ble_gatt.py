#!/usr/bin/env python3
"""BLE GATT server — Nordic UART Service (NUS) for NavPilot on iOS + Android.

Exposes NCP v4.1 over BLE so NavPilot can connect on both Android and iOS
(iOS blocks Classic Bluetooth SPP for non-MFi apps; BLE GATT is unrestricted).

GATT layout:
    Service:     6E400001-B5A3-F393-E0A9-E50E24DCCA9E  (Nordic UART)
    RX char:     6E400002-B5A3-F393-E0A9-E50E24DCCA9E  (phone → device, write)
    TX char:     6E400003-B5A3-F393-E0A9-E50E24DCCA9E  (device → phone, notify)

NavPilot writes NCP frames to RX; device notifies on TX.
Pairing (OS-level): BLE bonding uses the same BlueZ PairingAgent as Classic BT.
Device is discoverable as the name from 'EOPDeviceName' param (e.g. 'EXOPILOT 01').
"""
from __future__ import annotations

import logging
import threading
from typing import Callable

try:
    import dbus
    import dbus.service
    import dbus.mainloop.glib
    from gi.repository import GLib
    DBUS_AVAILABLE = True
except ImportError:
    dbus = None
    GLib = None
    DBUS_AVAILABLE = False

try:
    from openpilot.common.params import Params
except ImportError:
    Params = None

from openpilot.system.bluetoothd import protocol, ncp_session

logger = logging.getLogger('bluetoothd.ble_gatt')

# ── Nordic UART Service UUIDs ──────────────────────────────────────────────────
NUS_SERVICE_UUID = '6e400001-b5a3-f393-e0a9-e50e24dcca9e'
NUS_RX_UUID      = '6e400002-b5a3-f393-e0a9-e50e24dcca9e'  # phone writes
NUS_TX_UUID      = '6e400003-b5a3-f393-e0a9-e50e24dcca9e'  # device notifies

# ── D-Bus paths ────────────────────────────────────────────────────────────────
GATT_APP_PATH = '/org/bluez/eop/gatt_app'
NUS_SVC_PATH  = '/org/bluez/eop/gatt_app/service0'
NUS_RX_PATH   = '/org/bluez/eop/gatt_app/service0/char0'
NUS_TX_PATH   = '/org/bluez/eop/gatt_app/service0/char1'
ADVERT_PATH   = '/org/bluez/eop/advertisement'

# ── BlueZ / D-Bus interfaces ──────────────────────────────────────────────────
BLUEZ_SVC         = 'org.bluez'
GATT_MGR_IFACE    = 'org.bluez.GattManager1'
LE_ADV_MGR_IFACE  = 'org.bluez.LEAdvertisingManager1'
LE_ADV_IFACE      = 'org.bluez.LEAdvertisement1'
GATT_SVC_IFACE    = 'org.bluez.GattService1'
GATT_CHAR_IFACE   = 'org.bluez.GattCharacteristic1'
PROPS_IFACE       = 'org.freedesktop.DBus.Properties'
OBJMGR_IFACE      = 'org.freedesktop.DBus.ObjectManager'


# ── D-Bus exception types ──────────────────────────────────────────────────────

def _make_exc(name: str):
    if not DBUS_AVAILABLE:
        return type(name, (Exception,), {})
    return type(name, (dbus.exceptions.DBusException,), {'_dbus_error_name': f'org.bluez.Error.{name}'})


NotSupported = _make_exc('NotSupportedException')
InvalidArgs  = _make_exc('InvalidArgsException')


# ── GATT Application (ObjectManager) ──────────────────────────────────────────

class _Application(dbus.service.Object if DBUS_AVAILABLE else object):  # type: ignore[misc]
    def __init__(self, bus: dbus.Bus, service: '_NUSService', rx: '_RXChar', tx: '_TXChar'):
        if DBUS_AVAILABLE:
            dbus.service.Object.__init__(self, bus, GATT_APP_PATH)
        self._svc, self._rx, self._tx = service, rx, tx

    @dbus.service.method(OBJMGR_IFACE, out_signature='a{oa{sa{sv}}}')  # type: ignore[misc]
    def GetManagedObjects(self):  # noqa: N802
        return {
            dbus.ObjectPath(NUS_SVC_PATH): self._svc.get_properties(),
            dbus.ObjectPath(NUS_RX_PATH):  self._rx.get_properties(),
            dbus.ObjectPath(NUS_TX_PATH):  self._tx.get_properties(),
        }


# ── Nordic UART Service ────────────────────────────────────────────────────────

class _NUSService(dbus.service.Object if DBUS_AVAILABLE else object):  # type: ignore[misc]
    def __init__(self, bus: dbus.Bus):
        if DBUS_AVAILABLE:
            dbus.service.Object.__init__(self, bus, NUS_SVC_PATH)

    def get_properties(self):
        return {GATT_SVC_IFACE: {
            'UUID':            dbus.String(NUS_SERVICE_UUID),
            'Primary':         dbus.Boolean(True),
            'Characteristics': dbus.Array(
                [dbus.ObjectPath(NUS_RX_PATH), dbus.ObjectPath(NUS_TX_PATH)], signature='o'),
        }}

    @dbus.service.method(PROPS_IFACE, in_signature='s', out_signature='a{sv}')  # type: ignore[misc]
    def GetAll(self, interface):  # noqa: N802
        return self.get_properties().get(interface, {})


# ── RX Characteristic (phone writes NCP frames to device) ─────────────────────

class _RXChar(dbus.service.Object if DBUS_AVAILABLE else object):  # type: ignore[misc]
    def __init__(self, bus: dbus.Bus, on_write: Callable[[bytes], None]):
        if DBUS_AVAILABLE:
            dbus.service.Object.__init__(self, bus, NUS_RX_PATH)
        self._on_write = on_write

    def get_properties(self):
        return {GATT_CHAR_IFACE: {
            'Service': dbus.ObjectPath(NUS_SVC_PATH),
            'UUID':    dbus.String(NUS_RX_UUID),
            'Flags':   dbus.Array(['write', 'write-without-response'], signature='s'),
            'Value':   dbus.Array([], signature='y'),
        }}

    @dbus.service.method(PROPS_IFACE, in_signature='s', out_signature='a{sv}')  # type: ignore[misc]
    def GetAll(self, interface):  # noqa: N802
        return self.get_properties().get(interface, {})

    @dbus.service.method(GATT_CHAR_IFACE, in_signature='aya{sv}', out_signature='')  # type: ignore[misc]
    def WriteValue(self, value, options):  # noqa: N802
        data = bytes(bytearray(value))
        logger.debug('BLE RX: %d bytes', len(data))
        if self._on_write:
            self._on_write(data)

    @dbus.service.method(GATT_CHAR_IFACE, in_signature='', out_signature='')  # type: ignore[misc]
    def StartNotify(self):  # noqa: N802
        raise NotSupported('RX is write-only')

    @dbus.service.method(GATT_CHAR_IFACE, in_signature='', out_signature='')  # type: ignore[misc]
    def StopNotify(self):  # noqa: N802
        raise NotSupported('RX is write-only')


# ── TX Characteristic (device notifies NCP frames to phone) ───────────────────

class _TXChar(dbus.service.Object if DBUS_AVAILABLE else object):  # type: ignore[misc]
    def __init__(self, bus: dbus.Bus,
                 on_notify_start: Callable[[], None] | None = None,
                 on_notify_stop: Callable[[], None] | None = None):
        if DBUS_AVAILABLE:
            dbus.service.Object.__init__(self, bus, NUS_TX_PATH)
        self._notifying = False
        self._lock = threading.Lock()
        self._value: list[int] = []
        self._on_notify_start = on_notify_start
        self._on_notify_stop = on_notify_stop

    def get_properties(self):
        return {GATT_CHAR_IFACE: {
            'Service':   dbus.ObjectPath(NUS_SVC_PATH),
            'UUID':      dbus.String(NUS_TX_UUID),
            'Flags':     dbus.Array(['notify'], signature='s'),
            'Value':     dbus.Array(self._value, signature='y'),
            'Notifying': dbus.Boolean(self._notifying),
        }}

    @dbus.service.method(PROPS_IFACE, in_signature='s', out_signature='a{sv}')  # type: ignore[misc]
    def GetAll(self, interface):  # noqa: N802
        return self.get_properties().get(interface, {})

    @dbus.service.method(GATT_CHAR_IFACE, in_signature='', out_signature='')  # type: ignore[misc]
    def StartNotify(self):  # noqa: N802
        with self._lock:
            self._notifying = True
        logger.info('BLE TX: notify started (phone connected)')
        if self._on_notify_start:
            self._on_notify_start()

    @dbus.service.method(GATT_CHAR_IFACE, in_signature='', out_signature='')  # type: ignore[misc]
    def StopNotify(self):  # noqa: N802
        with self._lock:
            self._notifying = False
        logger.info('BLE TX: notify stopped (phone disconnected)')
        if self._on_notify_stop:
            self._on_notify_stop()

    @dbus.service.signal(PROPS_IFACE, signature='sa{sv}as')  # type: ignore[misc]
    def PropertiesChanged(self, interface, changed, invalidated):  # noqa: N802
        pass

    def is_notifying(self) -> bool:
        with self._lock:
            return self._notifying

    def notify(self, data: bytes) -> None:
        """Send data to phone. Lock covers both value update and signal emission
        so concurrent callers (telemetry thread + GLib response thread) cannot
        interleave chunks from different frames on the BLE channel."""
        if not self.is_notifying() or not DBUS_AVAILABLE:
            return
        for i in range(0, len(data), 500):  # 500-byte chunks (safe under BlueZ MTU 512)
            chunk = list(data[i:i + 500])
            with self._lock:
                self._value = chunk
                self.PropertiesChanged(
                    GATT_CHAR_IFACE,
                    {'Value': dbus.Array(chunk, signature='y')},
                    dbus.Array([], signature='s'),
                )


# ── LE Advertisement ───────────────────────────────────────────────────────────

class _LEAdvertisement(dbus.service.Object if DBUS_AVAILABLE else object):  # type: ignore[misc]
    def __init__(self, bus: dbus.Bus, name: str):
        if DBUS_AVAILABLE:
            dbus.service.Object.__init__(self, bus, ADVERT_PATH)
        self._name = name

    @dbus.service.method(PROPS_IFACE, in_signature='s', out_signature='a{sv}')  # type: ignore[misc]
    def GetAll(self, interface):  # noqa: N802
        if interface != LE_ADV_IFACE:
            raise InvalidArgs()
        return {
            'Type':         dbus.String('peripheral'),
            'ServiceUUIDs': dbus.Array([NUS_SERVICE_UUID], signature='s'),
            'LocalName':    dbus.String(self._name),
            'Includes':     dbus.Array(['tx-power'], signature='s'),
        }

    @dbus.service.method(LE_ADV_IFACE, in_signature='', out_signature='')  # type: ignore[misc]
    def Release(self):  # noqa: N802
        logger.info('BLE advertisement released')


# ── Main GATT Daemon ───────────────────────────────────────────────────────────

class GATTD:
    """BLE GATT daemon — Nordic UART Service transport for NCP v4.1.

    Transport layer only. NCP protocol logic lives in NCPSession (ncp_session.py).
    GATTD owns: BlueZ D-Bus GATT application, BLE advertising, rx/tx wiring.
    """

    def __init__(self, params: Params | None = None,
                 session: ncp_session.NCPSession | None = None):
        self.params = params or (Params() if Params else None)
        self._device_name = self._get_device_name()

        self._bus: dbus.Bus | None = None
        self._app: _Application | None = None
        self._svc: _NUSService | None = None
        self._rx: _RXChar | None = None
        self._tx: _TXChar | None = None
        self._advert: _LEAdvertisement | None = None

        self._rx_buffer = b''

        # Shared NCP session — one per process; if not supplied, create standalone
        self.session = session or ncp_session.NCPSession(params=self.params)

    def _get_device_name(self) -> str:
        if self.params:
            name = self.params.get('EOPDeviceName')
            if name:
                return name.decode() if isinstance(name, bytes) else name
        return 'EXOPILOT'

    # ── D-Bus / GATT setup ────────────────────────────────────────────────────

    def setup(self, bus: dbus.Bus) -> bool:
        """Register GATT application and start advertising. Returns True on success."""
        if not DBUS_AVAILABLE:
            logger.warning('dbus-python / gi not available — BLE GATT disabled')
            return False

        try:
            self._bus = bus
            self._svc = _NUSService(bus)
            self._rx = _RXChar(bus, self._on_ble_data)
            self._tx = _TXChar(bus, on_notify_start=self._on_phone_connect,
                               on_notify_stop=self._on_phone_disconnect)
            self._app = _Application(bus, self._svc, self._rx, self._tx)
            self._advert = _LEAdvertisement(bus, self._device_name)

            gatt_mgr = dbus.Interface(
                bus.get_object(BLUEZ_SVC, '/org/bluez/hci0'), GATT_MGR_IFACE)
            gatt_mgr.RegisterApplication(
                GATT_APP_PATH, {},
                reply_handler=lambda: logger.info('BLE GATT application registered'),
                error_handler=lambda e: logger.error('BLE GATT registration failed: %s', e),
            )

            adv_mgr = dbus.Interface(
                bus.get_object(BLUEZ_SVC, '/org/bluez/hci0'), LE_ADV_MGR_IFACE)
            adv_mgr.RegisterAdvertisement(
                ADVERT_PATH, {},
                reply_handler=lambda: logger.info('BLE advertisement registered as "%s"', self._device_name),
                error_handler=lambda e: logger.warning('BLE advertisement error: %s', e),
            )

            logger.info('BLE GATT setup complete — advertising as "%s"', self._device_name)
            return True

        except Exception as e:
            logger.error('BLE GATT setup failed: %s', e)
            return False

    # ── Phone connect / disconnect (wired from _TXChar callbacks) ─────────────

    def _on_phone_connect(self) -> None:
        """Called by _TXChar.StartNotify — phone subscribed to TX, session is live."""
        if self._tx:
            self.session.add_transport('gatt', self._tx.notify)

    def _on_phone_disconnect(self) -> None:
        """Called by _TXChar.StopNotify — phone unsubscribed, reset route state."""
        self.session.remove_transport('gatt')
        self._rx_buffer = b''

    # ── Receive path ──────────────────────────────────────────────────────────

    def _on_ble_data(self, data: bytes) -> None:
        """Called by RX characteristic when phone writes data. Buffers and decodes NCP frames."""
        self._rx_buffer += data
        while self._rx_buffer:
            frame, consumed = protocol.Frame.decode(self._rx_buffer)
            if consumed:
                self._rx_buffer = self._rx_buffer[consumed:]
            if frame is not None:
                response = self.session.handle_frame(frame)
                # Response goes back to this transport only (not broadcast)
                if response and self._tx:
                    self._tx.notify(response.encode())
            elif consumed == 0:
                break

    # ── Lifecycle ─────────────────────────────────────────────────────────────

    def start(self) -> None:
        logger.info('GATTD started')

    def stop(self) -> None:
        self.session.remove_transport('gatt')  # clean up if phone is still connected
        if self._bus and self._advert:
            try:
                adv_mgr = dbus.Interface(
                    self._bus.get_object(BLUEZ_SVC, '/org/bluez/hci0'), LE_ADV_MGR_IFACE)
                adv_mgr.UnregisterAdvertisement(ADVERT_PATH)
            except Exception:
                pass
        if self._bus and self._app:
            try:
                gatt_mgr = dbus.Interface(
                    self._bus.get_object(BLUEZ_SVC, '/org/bluez/hci0'), GATT_MGR_IFACE)
                gatt_mgr.UnregisterApplication(GATT_APP_PATH)
            except Exception:
                pass
        logger.info('GATTD stopped')

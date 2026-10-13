#!/usr/bin/env python3
"""PLP v5 over BLE GATT (ExoPilot boards; a comma 3 has no BLE hardware).

A second GATT application beside the Nordic UART one (ble_gatt.py, NCP v4.1), so an old NCP-only app never sees PLP and a new app picks the PLP service when it exists:

    Service:  0ac2bfc2-457f-4979-b921-4ff0946bd329   (proposed UUID, to be reserved)
    RX char:  0ac2bfc3-...                           (phone -> device, write without response)
    TX char:  0ac2bfc4-...                           (device -> phone, notify)

The characteristics carry the PLP frame byte stream; chunking and pacing are nagaspilot.plp.ble_stream, the protocol is nagaspilot.plp.session. One phone at a time; a new
subscription replaces the old session. Both characteristics require an encrypted link. UNTESTED against BlueZ (no bluez, dbus or radio in the dev container): the pure parts
are tested in nagaspilot/tests/test_plp_ble_stream.py, this file is the thin D-Bus shell around them and must be exercised on a board.
"""
from __future__ import annotations

import logging
import threading

from nagaspilot.plp.ble_stream import DEFAULT_ATT_MTU, BleStream
from nagaspilot.plp.hello import Hello
from nagaspilot.plp.locate import LocateSink
from nagaspilot.plp.session import PlpSession
from openpilot.system.bluetoothd.ble_gatt import (BLUEZ_SVC, DBUS_AVAILABLE, GATT_CHAR_IFACE, GATT_MGR_IFACE, GATT_SVC_IFACE, OBJMGR_IFACE, PROPS_IFACE, NotSupported,
                                                  dbus, dbus_method, dbus_signal)

logger = logging.getLogger('bluetoothd.plp_gatt')

PLP_SERVICE_UUID = '0ac2bfc2-457f-4979-b921-4ff0946bd329'
PLP_RX_UUID = '0ac2bfc3-457f-4979-b921-4ff0946bd329'
PLP_TX_UUID = '0ac2bfc4-457f-4979-b921-4ff0946bd329'

PLP_APP_PATH = '/org/bluez/eop/plp_app'
PLP_SVC_PATH = PLP_APP_PATH + '/service0'
PLP_RX_PATH = PLP_SVC_PATH + '/char0'
PLP_TX_PATH = PLP_SVC_PATH + '/char1'

SERVICES = (('core', 1, 1), ('locate', 1, 1))
_Base = dbus.service.Object if DBUS_AVAILABLE else object


def device_hello() -> Hello:
    return Hello(max_payload=512, max_message=1 << 20, services=SERVICES, link='ble')


class _Service(_Base):  # type: ignore[misc]
    def __init__(self, bus):
        if DBUS_AVAILABLE:
            dbus.service.Object.__init__(self, bus, PLP_SVC_PATH)

    def get_properties(self):
        return {GATT_SVC_IFACE: {
            'UUID': dbus.String(PLP_SERVICE_UUID),
            'Primary': dbus.Boolean(True),
            'Characteristics': dbus.Array([dbus.ObjectPath(PLP_RX_PATH), dbus.ObjectPath(PLP_TX_PATH)], signature='o'),
        }}

    @dbus_method(PROPS_IFACE, in_signature='s', out_signature='a{sv}')  # type: ignore[misc]
    def GetAll(self, interface):
        return self.get_properties().get(interface, {})


class _RX(_Base):  # type: ignore[misc]
    def __init__(self, bus, on_write):
        if DBUS_AVAILABLE:
            dbus.service.Object.__init__(self, bus, PLP_RX_PATH)
        self._on_write = on_write

    def get_properties(self):
        return {GATT_CHAR_IFACE: {
            'Service': dbus.ObjectPath(PLP_SVC_PATH),
            'UUID': dbus.String(PLP_RX_UUID),
            'Flags': dbus.Array(['encrypt-write', 'write-without-response'], signature='s'),
            'Value': dbus.Array([], signature='y'),
        }}

    @dbus_method(PROPS_IFACE, in_signature='s', out_signature='a{sv}')  # type: ignore[misc]
    def GetAll(self, interface):
        return self.get_properties().get(interface, {})

    @dbus_method(GATT_CHAR_IFACE, in_signature='aya{sv}', out_signature='')  # type: ignore[misc]
    def WriteValue(self, value, options):
        mtu = options.get('mtu') if options else None      # BlueZ passes the link's ATT MTU with each write
        self._on_write(bytes(bytearray(value)), int(mtu) if mtu else None)

    @dbus_method(GATT_CHAR_IFACE, in_signature='', out_signature='')  # type: ignore[misc]
    def StartNotify(self):
        raise NotSupported('RX is write-only')

    @dbus_method(GATT_CHAR_IFACE, in_signature='', out_signature='')  # type: ignore[misc]
    def StopNotify(self):
        raise NotSupported('RX is write-only')


class _TX(_Base):  # type: ignore[misc]
    def __init__(self, bus, on_start, on_stop):
        if DBUS_AVAILABLE:
            dbus.service.Object.__init__(self, bus, PLP_TX_PATH)
        self._on_start, self._on_stop = on_start, on_stop
        self._notifying = False
        self._lock = threading.Lock()

    def get_properties(self):
        return {GATT_CHAR_IFACE: {
            'Service': dbus.ObjectPath(PLP_SVC_PATH),
            'UUID': dbus.String(PLP_TX_UUID),
            'Flags': dbus.Array(['encrypt-read', 'notify'], signature='s'),
            'Value': dbus.Array([], signature='y'),
            'Notifying': dbus.Boolean(self._notifying),
        }}

    @dbus_method(PROPS_IFACE, in_signature='s', out_signature='a{sv}')  # type: ignore[misc]
    def GetAll(self, interface):
        return self.get_properties().get(interface, {})

    @dbus_method(GATT_CHAR_IFACE, in_signature='', out_signature='')  # type: ignore[misc]
    def StartNotify(self):
        with self._lock:
            self._notifying = True
        self._on_start()

    @dbus_method(GATT_CHAR_IFACE, in_signature='', out_signature='')  # type: ignore[misc]
    def StopNotify(self):
        with self._lock:
            self._notifying = False
        self._on_stop()

    @dbus_signal(PROPS_IFACE, signature='sa{sv}as')  # type: ignore[misc]
    def PropertiesChanged(self, interface, changed, invalidated):
        pass

    def notify_chunk(self, chunk: bytes) -> bool:
        """One notification (already at most MTU - 3 bytes). False when nobody is subscribed."""
        with self._lock:
            if not self._notifying or not DBUS_AVAILABLE:
                return False
            self.PropertiesChanged(GATT_CHAR_IFACE, {'Value': dbus.Array(list(chunk), signature='y')}, dbus.Array([], signature='s'))
            return True


class _Application(_Base):  # type: ignore[misc]
    def __init__(self, bus, svc, rx, tx):
        if DBUS_AVAILABLE:
            dbus.service.Object.__init__(self, bus, PLP_APP_PATH)
        self._svc, self._rx, self._tx = svc, rx, tx

    @dbus_method(OBJMGR_IFACE, out_signature='a{oa{sa{sv}}}')  # type: ignore[misc]
    def GetManagedObjects(self):
        return {
            dbus.ObjectPath(PLP_SVC_PATH): self._svc.get_properties(),
            dbus.ObjectPath(PLP_RX_PATH): self._rx.get_properties(),
            dbus.ObjectPath(PLP_TX_PATH): self._tx.get_properties(),
        }


class PlpGATT:
    """The PLP BLE transport: owns the D-Bus application and one PlpSession per subscribed phone."""

    def __init__(self, sink: LocateSink | None = None):
        self.sink = sink or LocateSink()
        self._bus = None
        self._app = None
        self._tx: _TX | None = None
        self._stream: BleStream | None = None
        self.session: PlpSession | None = None

    def setup(self, bus) -> bool:
        if not DBUS_AVAILABLE:
            logger.warning('dbus-python / gi not available - PLP over BLE disabled')
            return False
        try:
            self._bus = bus
            svc = _Service(bus)
            rx = _RX(bus, self._on_write)
            self._tx = _TX(bus, self._on_phone_connect, self._on_phone_disconnect)
            self._app = _Application(bus, svc, rx, self._tx)
            dbus.Interface(bus.get_object(BLUEZ_SVC, '/org/bluez/hci0'), GATT_MGR_IFACE).RegisterApplication(
                PLP_APP_PATH, {},
                reply_handler=lambda: logger.info('PLP GATT application registered'),
                error_handler=lambda e: logger.error('PLP GATT registration failed: %s', e))
            return True
        except Exception:
            logger.exception('PLP GATT setup failed')
            return False

    def _on_phone_connect(self) -> None:
        self._drop_session()
        assert self._tx is not None
        stream = BleStream(self._tx.notify_chunk, on_dead=self._drop_session)
        session = PlpSession(device_hello(), stream.send, role='device')
        self.sink.attach(session)
        stream.start()
        self._stream, self.session = stream, session
        logger.info('PLP BLE: phone subscribed')

    def _on_phone_disconnect(self) -> None:
        self._drop_session()

    def _drop_session(self) -> None:
        stream, self._stream, self.session = self._stream, None, None
        if stream is not None:
            stream.stop()

    def _on_write(self, data: bytes, mtu: int | None) -> None:
        stream, session = self._stream, self.session
        if stream is None or session is None:
            return
        if mtu:
            stream.set_mtu(mtu)
        elif stream.att_mtu < DEFAULT_ATT_MTU:
            stream.set_mtu(DEFAULT_ATT_MTU)
        session.feed(data)

    def start(self) -> None:
        logger.info('PlpGATT started')

    def stop(self) -> None:
        self._drop_session()
        if self._bus and self._app:
            try:
                dbus.Interface(self._bus.get_object(BLUEZ_SVC, '/org/bluez/hci0'), GATT_MGR_IFACE).UnregisterApplication(PLP_APP_PATH)
            except Exception:
                pass

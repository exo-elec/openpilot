#!/usr/bin/env python3
"""SPP (Serial Port Profile) daemon — Classic Bluetooth RFCOMM transport.

Handles BOTH protocols simultaneously on the same socket:
- NCP v4.1 framed JSON: delegates to NCPSession for all protocol logic
- ELM327 raw ASCII: direct bridge to obd2d for legacy OBD scanner apps
"""
from __future__ import annotations

import socket
import struct
import time
import threading
import logging
from collections.abc import Callable

try:
    import dbus
except ImportError:
    dbus = None

try:
    import cereal.messaging as messaging
    from openpilot.common.params import Params
except ImportError:
    messaging = None
    Params = None

from openpilot.system.bluetoothd import protocol, device, ncp_session

logger = logging.getLogger('bluetoothd.spp')

RFCOMM_CHANNEL = 1


class Client:
    """SPP client connection — dual NCP + ELM327 mux on one socket."""

    def __init__(self, sock: socket.socket, addr: str, on_frame: Callable,
                 on_close: Callable, on_raw: Callable | None = None,
                 send_lock: threading.Lock | None = None):
        self.sock = sock
        self.addr = addr
        self.on_frame = on_frame
        self.on_close = on_close
        self.on_raw = on_raw
        self.running = False
        self._buffer = b''
        # Shared with NCPSession's _send_locked so both response and telemetry
        # writes go through the same lock — prevents interleaved sendall on the socket.
        self._send_lock = send_lock or threading.Lock()

    def _looks_like_ncp_frame(self, data: bytes, offset: int = 0) -> bool:
        if len(data) - offset < 4:
            return False
        length = struct.unpack('>H', data[offset:offset + 2])[0]
        if not (4 <= length <= 65535):
            return False
        msg_type = struct.unpack('>H', data[offset + 2:offset + 4])[0]
        return 0x00 <= msg_type <= 0x6F

    def _looks_like_elm327_line(self, data: bytes, offset: int = 0) -> bool:
        if len(data) - offset < 1:
            return False
        first = chr(data[offset]).upper() if data[offset] < 128 else ''
        return first in ('A', 'S') or first in '0123456789'

    def _try_ncp_frame(self) -> bool:
        frame, consumed = protocol.Frame.decode(self._buffer)
        if consumed:
            self._buffer = self._buffer[consumed:]
        if frame is not None:
            response = self.on_frame(frame)
            if response:
                with self._send_lock:
                    self.sock.sendall(response.encode())
            return True
        return False

    def _try_elm327_line(self) -> bool:
        cr_pos = self._buffer.find(b'\r')
        lf_pos = self._buffer.find(b'\n')
        if cr_pos == -1 and lf_pos == -1:
            return False
        pos = cr_pos if cr_pos != -1 and (lf_pos == -1 or cr_pos < lf_pos) else lf_pos
        line = self._buffer[:pos].decode('ascii', errors='ignore').strip()
        self._buffer = self._buffer[pos + 1:]
        if not line:
            return True
        logger.debug('SPP ELM327: %s', line)
        if self.on_raw:
            response = self.on_raw(line)
            if response:
                with self._send_lock:
                    self.sock.sendall(response.encode('ascii', errors='replace'))
                    self.sock.sendall(b'\r\r>')
        return True

    def _process_buffer(self) -> None:
        while self._buffer:
            if self._looks_like_ncp_frame(self._buffer):
                if self._try_ncp_frame():
                    continue
                break
            if self._looks_like_elm327_line(self._buffer):
                if self._try_elm327_line():
                    continue
                break
            logger.debug('SPP: dropping unexpected byte 0x%02x', self._buffer[0])
            self._buffer = self._buffer[1:]

    def run(self):
        self.running = True
        try:
            while self.running:
                data = self.sock.recv(1024)
                if not data:
                    break
                self._buffer += data
                self._process_buffer()
        except Exception as e:
            logger.debug('Client %s error: %s', self.addr, e)
        finally:
            self.running = False
            self.on_close()

    def close(self):
        self.running = False
        try:
            self.sock.close()
        except Exception:
            pass


class SPPD:
    """SPP daemon — Classic Bluetooth RFCOMM transport + ELM327 passthrough.

    NCP protocol logic lives entirely in NCPSession (ncp_session.py).
    SPPD owns: socket management, ELM327 ↔ obd2d bridge, reconnect logic.
    """

    MAX_RECONNECT = 10
    BASE_DELAY    = 3.0
    MAX_DELAY     = 60.0

    def __init__(self, params: Params | None = None,
                 session: ncp_session.NCPSession | None = None):
        self.params = params or (Params() if Params else None)

        self.enabled        = self.params.get_bool('EOPSPPEnabled') if self.params else False
        self.auto_reconnect = self.params.get_bool('EOPSPPAutoReconnect') if self.params else True
        paired = self.params.get('EOPSPPPairedDevice') if self.params else b''
        self.paired_addr    = (paired or b'').decode() if isinstance(paired, bytes) else (paired or '')

        self.running = False
        self.server: socket.socket | None = None
        self.client: Client | None = None
        self._reconnect_count = 0
        self._reconnect_delay = self.BASE_DELAY
        self._timer:  threading.Timer | None = None
        self._thread: threading.Thread | None = None

        # Shared NCP session — created externally so it is exactly one per process
        self.session = session or ncp_session.NCPSession(params=self.params)

        # SubMaster only for ELM327 passthrough response polling (obdResponse).
        # obdCommand is published via self.session.pm (shared, avoids duplicate publishers).
        if messaging:
            self._elm_sm = messaging.SubMaster(['obdResponse'])
        else:
            self._elm_sm = None

    # ── Device classification ─────────────────────────────────────────────────

    def _is_mobile(self, addr: str) -> bool:
        if not dbus:
            return True
        try:
            bus = dbus.SystemBus()
            path = f'/org/bluez/hci0/dev_{addr.replace(":", "_")}'
            obj = bus.get_object('org.bluez', path)
            props = dbus.Interface(obj, 'org.freedesktop.DBus.Properties')
            uuids = [str(u) for u in props.Get('org.bluez.Device1', 'UUIDs')]
            name = str(props.Get('org.bluez.Device1', 'Name'))
            info = device.classify(addr, name, uuids)
            return info.is_mobile() or info.device_type == device.Type.UNKNOWN
        except Exception:
            return True

    # ── Connection management ─────────────────────────────────────────────────

    def _connect_client(self) -> bool:
        if not self.paired_addr:
            return False
        try:
            sock = socket.socket(socket.AF_BLUETOOTH, socket.SOCK_STREAM, socket.BTPROTO_RFCOMM)
            sock.settimeout(10.0)
            sock.connect((self.paired_addr, RFCOMM_CHANNEL))
            self._on_connect(sock, self.paired_addr)
            logger.info('SPP connected to %s', self.paired_addr)
            return True
        except Exception as e:
            logger.debug('SPP connect failed: %s', e)
            return False

    def _on_connect(self, sock: socket.socket, addr: str):
        if self.client:
            self.client.close()

        # One lock shared by both the NCP response path (Client._send_lock)
        # and the telemetry broadcast path (_send_locked) — same socket, one writer at a time.
        shared_lock = threading.Lock()

        def _send_locked(data: bytes) -> None:
            with shared_lock:
                try:
                    sock.sendall(data)
                except Exception:
                    pass

        self.session.add_transport('spp', _send_locked)

        self.client = Client(
            sock=sock, addr=addr,
            on_frame=self._handle_frame,
            on_close=self._on_disconnect,
            on_raw=self._handle_elm327_raw,
            send_lock=shared_lock,
        )
        threading.Thread(target=self.client.run, daemon=True).start()

        self._reconnect_count = 0
        self._reconnect_delay = self.BASE_DELAY
        if self.params:
            self.params.put('EOPSPPPairedDevice', addr)

    def _on_disconnect(self):
        logger.info('SPP client disconnected')
        self.session.remove_transport('spp')
        self.client = None
        if self.auto_reconnect:
            self._schedule_reconnect()

    def _schedule_reconnect(self):
        if self._reconnect_count >= self.MAX_RECONNECT:
            return
        self._reconnect_count += 1
        logger.info('SPP reconnect in %.0fs', self._reconnect_delay)
        self._timer = threading.Timer(self._reconnect_delay, self._do_reconnect)
        self._timer.daemon = True
        self._timer.start()
        self._reconnect_delay = min(self._reconnect_delay * 2, self.MAX_DELAY)

    def _do_reconnect(self):
        if self.running and not self.client:
            if not self._connect_client():
                self._schedule_reconnect()

    # ── NCP — delegated to NCPSession ─────────────────────────────────────────

    def _handle_frame(self, frame: protocol.Frame) -> protocol.Frame | None:
        return self.session.handle_frame(frame)

    # ── ELM327 passthrough — SPP-specific ─────────────────────────────────────

    def _handle_elm327_raw(self, cmd: str) -> str:
        """Bridge raw ELM327 AT command / hex PID to obd2d and return response."""
        if not self._elm_sm or not messaging or not self.session.pm:
            return 'UNABLE TO CONNECT'
        try:
            msg = messaging.new_message('obdCommand')
            msg.obdCommand.command = cmd
            self.session.pm.send('obdCommand', msg)  # reuse shared pm — no duplicate publisher
            for _ in range(20):
                self._elm_sm.update(0)
                if self._elm_sm.updated['obdResponse']:
                    resp = self._elm_sm['obdResponse']
                    if resp.response:
                        return resp.response
                time.sleep(0.01)
            return 'NO DATA'
        except Exception:
            logger.exception('ELM327 raw error')
            return 'ERROR'

    # ── Server loop ───────────────────────────────────────────────────────────

    def _accept_loop(self):
        try:
            self.server = socket.socket(socket.AF_BLUETOOTH, socket.SOCK_STREAM, socket.BTPROTO_RFCOMM)
            self.server.bind(('', RFCOMM_CHANNEL))
            self.server.listen(1)
            logger.info('SPP listening on channel %d', RFCOMM_CHANNEL)
        except Exception:
            logger.exception('Failed to start SPP server')
            return

        while self.running:
            try:
                if self.auto_reconnect and not self.client and not self._timer:
                    self._connect_client()
                self.server.settimeout(1.0)
                try:
                    sock, addr = self.server.accept()
                except TimeoutError:
                    continue
                addr_str = addr[0]
                if not self._is_mobile(addr_str):
                    sock.close()
                    continue
                if not self.paired_addr:
                    self.paired_addr = addr_str
                    if self.params:
                        self.params.put('EOPSPPPairedDevice', addr_str)
                self._on_connect(sock, addr_str)
                logger.info('SPP client connected: %s', addr_str)
            except Exception:
                if self.running:
                    logger.exception('Accept error')
                    time.sleep(1)

    # ── Lifecycle ─────────────────────────────────────────────────────────────

    def run(self):
        if not self.enabled:
            logger.info('SPP disabled')
            return
        self.running = True
        self.session.start()  # no-op if bluetoothd already started it; safe standalone too
        self._thread = threading.Thread(target=self._accept_loop, daemon=True)
        self._thread.start()
        logger.info('SPP started')

    def stop(self):
        self.running = False
        if self._timer:
            self._timer.cancel()
        if self.client:
            self.client.close()
        if self.server:
            try:
                self.server.close()
            except Exception:
                pass
        logger.info('SPP stopped')


def main():
    logging.basicConfig(level=logging.INFO)
    d = SPPD()
    d.run()
    try:
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        d.stop()


if __name__ == '__main__':
    main()

#!/usr/bin/env python3
"""NCP v4.1 over the USB cable: the phone's network interface to the comma instead of BLE.

With the phone-hosted big model in iPhone mode (EOPBigModelLink = 2) the comma presents a CDC-NCM network at
192.168.60.1 and the phone dials it (jetlink-root.sh gadget --ios). This serves the same NCP frames BLE and SPP carry
over a TCP stream on that address, registered with the shared NCPSession as the 'cable' transport, so every NCP feature
(navigation, telemetry, vehicle data, pairing messages) works over the cable with no change to the session.

Not on the open network: the server binds the cable address only, drops any peer outside the cable subnet, drops a
link whose frame header is malformed (the buffer is otherwise bounded by the 64 KB frame size), and serves one phone at
a time (a new connection replaces the old). A BLE bond is OS-level trust; here the physical cable is the trust anchor,
and NCP's own pairing messages are unchanged.

Android's Jetlink link is a USB vendor interface with no network, so this is the iPhone cable path.
"""
from __future__ import annotations

import logging
import socket
import threading
import time

from openpilot.system.bluetoothd import protocol

logger = logging.getLogger('bluetoothd.cable')

CABLE_ADDRESS = '192.168.60.1'   # the comma's end of the USB network (jetlink-root.sh gadget --ios)
CABLE_SUBNET = '192.168.60.'
NCP_CABLE_PORT = 5598            # Jetlink's own link uses 5599
MIN_FRAME_LENGTH = 4             # the length field counts type (2) + CRC (2) + payload
BIND_RETRY_S = 2.0               # the network interface appears only after the gadget binds


def _hard_close(sock: socket.socket | None) -> None:
    """close() from one thread does not wake another thread blocked in recv()/accept() on the same socket, so shut it
    down first: the blocked call returns and the peer sees the connection end."""
    if sock is None:
        return
    for action in (lambda: sock.shutdown(socket.SHUT_RDWR), sock.close):
        try:
            action()
        except OSError:
            pass


class CableNCP:
    def __init__(self, session, bind_addr: str = CABLE_ADDRESS, port: int = NCP_CABLE_PORT,
                 allowed_prefix: str = CABLE_SUBNET):
        self.session = session
        self.bind_addr = bind_addr
        self.port = port
        self.allowed_prefix = allowed_prefix
        self._listener: socket.socket | None = None
        self._client: socket.socket | None = None
        self._lock = threading.Lock()
        self._send_lock = threading.Lock()
        self._running = False
        self._thread: threading.Thread | None = None

    # -- lifecycle ----------------------------------------------------------------
    def start(self) -> None:
        if self._thread is not None:
            return
        self._running = True
        self._thread = threading.Thread(target=self._serve, daemon=True, name='ncp_cable')
        self._thread.start()

    def stop(self) -> None:
        self._running = False
        _hard_close(self._listener)
        _hard_close(self._client)
        if self._thread is not None:
            self._thread.join(timeout=2.0)
            self._thread = None

    @property
    def bound_port(self) -> int | None:
        """The port actually bound (differs from `port` when 0 was asked for)."""
        return self._listener.getsockname()[1] if self._listener is not None else None

    # -- accept -------------------------------------------------------------------
    def _bind(self) -> bool:
        try:
            listener = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            listener.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            listener.bind((self.bind_addr, self.port))
            listener.listen(2)
            listener.settimeout(1.0)
        except OSError as e:
            # EADDRNOTAVAIL until the gadget has bound and the interface has its address
            logger.debug('cable NCP: cannot bind %s:%s yet (%s)', self.bind_addr, self.port, e)
            try:
                listener.close()
            except (OSError, UnboundLocalError):
                pass
            return False
        self._listener = listener
        logger.info('cable NCP: listening on %s:%s', self.bind_addr, listener.getsockname()[1])
        return True

    def _serve(self) -> None:
        while self._running and self._listener is None:
            if not self._bind():
                time.sleep(BIND_RETRY_S)
        while self._running:
            try:
                sock, addr = self._listener.accept()
            except TimeoutError:
                continue
            except OSError:
                break
            if not addr[0].startswith(self.allowed_prefix):
                logger.warning('cable NCP: dropped a peer outside the cable subnet: %s', addr[0])
                sock.close()
                continue
            with self._lock:
                old, self._client = self._client, sock
            _hard_close(old)   # one phone at a time: the new connection replaces the old
            threading.Thread(target=self._handle, args=(sock, addr[0]), daemon=True, name='ncp_cable_client').start()

    # -- one phone ----------------------------------------------------------------
    def _send(self, sock: socket.socket, data: bytes) -> None:
        with self._send_lock:
            sock.sendall(data)

    def _handle(self, sock: socket.socket, peer: str) -> None:
        sock.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
        sock.settimeout(None)
        self.session.add_transport('cable', lambda data: self._send(sock, data))
        logger.info('cable NCP: phone connected from %s', peer)
        buffer = b''
        try:
            while self._running:
                data = sock.recv(4096)
                if not data:
                    break
                buffer += data
                buffer = self._process(sock, buffer)
        except ValueError as e:
            logger.warning('cable NCP: dropping the link: %s', e)
        except OSError as e:
            logger.debug('cable NCP: link ended: %s', e)
        finally:
            with self._lock:
                is_current = self._client is sock
                if is_current:
                    self._client = None
            # a newer phone has re-registered the 'cable' transport: only the current connection removes it
            if is_current:
                self.session.remove_transport('cable')
            _hard_close(sock)
            logger.info('cable NCP: phone disconnected (%s)', peer)

    def _process(self, sock: socket.socket, buffer: bytes) -> bytes:
        while buffer:
            # TCP does not lose sync, so a length no frame can have means a peer that is not speaking NCP
            if len(buffer) >= 2 and int.from_bytes(buffer[:2], 'big') < MIN_FRAME_LENGTH:
                raise ValueError(f'frame length {int.from_bytes(buffer[:2], "big")} is below the minimum')
            frame, consumed = protocol.Frame.decode(buffer)
            if consumed == 0:
                break            # incomplete: wait for more bytes
            buffer = buffer[consumed:]
            if frame is None:
                logger.debug('cable NCP: dropped a frame with a bad CRC')
                continue
            response = self.session.handle_frame(frame)
            if response is not None:
                self._send(sock, response.encode())
        return buffer

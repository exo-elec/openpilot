"""PLP over the USB cable: a TCP server on the comma's end of the cable network, for the iPhone (CDC-NCM, comma 192.168.60.1; ngp_bigmodel_link = 2).

This is the baseline transport on every branch. A comma 3 has no BLE hardware, so on NGP10 the cable is the only link; BLE (ExoPilot boards, EOP10 and up) is a second
transport that feeds the same `PlpSession`. Android's cable is a FunctionFS bulk pipe with no network; it gets its own transport class on the same session.

Not on an open network: bind the cable address only, refuse a peer outside the cable subnet, serve one phone at a time (a new connection replaces the old). The physical
cable is the trust anchor, as for the big-model link. No dependency on bluetoothd or BlueZ.
"""
from __future__ import annotations

import logging
import socket
import threading
import time
from collections.abc import Callable

from nagaspilot.plp.hello import Hello
from nagaspilot.plp.session import PlpSession

logger = logging.getLogger('plp.cable')

CABLE_ADDRESS = '192.168.60.1'
CABLE_SUBNET = '192.168.60.'
PLP_CABLE_PORT = 5601            # proposed; the big-model link (NPBM) uses 5600. Owner to confirm.
BIND_RETRY_S = 2.0               # the interface appears only after the gadget binds
RECV_BYTES = 65536


def _hard_close(sock: socket.socket | None) -> None:
  if sock is None:
    return
  for action in (lambda: sock.shutdown(socket.SHUT_RDWR), sock.close):
    try:
      action()
    except OSError:
      pass


class CablePLP:
  def __init__(self, hello: Hello, setup: Callable[[PlpSession], None], bind_addr: str = CABLE_ADDRESS, port: int = PLP_CABLE_PORT, allowed_prefix: str = CABLE_SUBNET):
    """`setup(session)` registers the handlers on each new connection's session."""
    self.hello = hello
    self.setup = setup
    self.bind_addr = bind_addr
    self.port = port
    self.allowed_prefix = allowed_prefix
    self.session: PlpSession | None = None
    self._listener: socket.socket | None = None
    self._client: socket.socket | None = None
    self._send_lock = threading.Lock()
    self._running = False
    self._thread: threading.Thread | None = None

  @property
  def bound_port(self) -> int | None:
    return self._listener.getsockname()[1] if self._listener is not None else None

  def start(self) -> None:
    if self._thread is not None:
      return
    self._running = True
    self._thread = threading.Thread(target=self._serve, daemon=True, name='plp_cable')
    self._thread.start()

  def stop(self) -> None:
    self._running = False
    _hard_close(self._listener)
    _hard_close(self._client)
    if self._thread is not None:
      self._thread.join(timeout=2.0)
      self._thread = None

  def _bind(self) -> socket.socket | None:
    while self._running:
      s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
      s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
      try:
        s.bind((self.bind_addr, self.port))
        s.listen(1)
        s.settimeout(0.5)
        return s
      except OSError:
        s.close()
        time.sleep(BIND_RETRY_S)
    return None

  def _serve(self) -> None:
    self._listener = self._bind()
    while self._running and self._listener is not None:
      try:
        conn, peer = self._listener.accept()
      except TimeoutError:
        continue
      except OSError:
        return
      if not peer[0].startswith(self.allowed_prefix):
        logger.warning('plp cable: refused %s', peer[0])
        _hard_close(conn)
        continue
      _hard_close(self._client)                    # a new phone replaces the old one
      self._client = conn
      threading.Thread(target=self._handle, args=(conn,), daemon=True, name='plp_cable_conn').start()

  def _handle(self, conn: socket.socket) -> None:
    def send(raw: bytes) -> None:
      with self._send_lock:
        try:
          conn.sendall(raw)
        except OSError:
          _hard_close(conn)

    session = PlpSession(self.hello, send, role='device')
    self.setup(session)
    self.session = session
    try:
      while self._running:
        data = conn.recv(RECV_BYTES)
        if not data:
          break
        session.feed(data)
    except OSError:
      pass
    finally:
      _hard_close(conn)
      if self._client is conn:
        self._client = None
        self.session = None

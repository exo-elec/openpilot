"""Where the phone connects to the comma.

The comma owns the address on the cable (192.168.60.1) and the phone gets one by DHCP, so the phone dials and the comma
accepts. The protocol's roles do not change with it: whoever connected, the comma sends HELLO and asks for frames.

The socket is bound to the cable address only, never 0.0.0.0: the comma's Wi-Fi and cellular interfaces must not offer
an inference port. The cable's interface appears when the gadget binds, so binding is retried until it exists.
"""
from __future__ import annotations

import logging
import socket
import threading

from nagaspilot.bigmodel import protocol as P

log = logging.getLogger('nagaspilot.bigmodel.listener')
BIND_RETRY = 1.0


class Listener:
  """Keeps the newest phone connection. A phone that reconnects replaces its stale predecessor."""

  def __init__(self, host: str = P.COMMA_ADDR, port: int = P.DEFAULT_PORT):
    self.host, self.port = host, port
    self._sock: socket.socket | None = None
    self._ready: socket.socket | None = None
    self._cv = threading.Condition()
    self._stop = threading.Event()
    self._thread: threading.Thread | None = None

  def start(self) -> Listener:
    if self._thread is None:
      self._thread = threading.Thread(target=self._loop, daemon=True)
      self._thread.start()
    return self

  def _bind(self) -> bool:
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    try:
      s.bind((self.host, self.port))
      s.listen(2)
    except OSError:
      s.close()
      return False
    self._sock = s
    self.port = s.getsockname()[1]
    return True

  def _loop(self) -> None:
    from nagaspilot.bigmodel.priority import background_thread
    background_thread()
    while not self._stop.is_set() and not self._bind():
      self._stop.wait(BIND_RETRY)
    while not self._stop.is_set():
      try:
        conn, peer = self._sock.accept()
      except OSError:
        return
      log.info('phone connected from %s', peer[0])
      with self._cv:
        if self._ready is not None:
          self._ready.close()
        self._ready = conn
        self._cv.notify_all()

  @property
  def bound(self) -> bool:
    return self._sock is not None

  def take(self, timeout: float) -> socket.socket | None:
    """The waiting connection, or None if no phone connects within `timeout`. The caller owns the socket."""
    with self._cv:
      self._cv.wait_for(lambda: self._ready is not None or self._stop.is_set(), timeout)
      conn, self._ready = self._ready, None
      return conn

  def close(self) -> None:
    self._stop.set()
    with self._cv:
      self._cv.notify_all()
      if self._ready is not None:
        self._ready.close()
        self._ready = None
    if self._sock is not None:
      try:
        self._sock.shutdown(socket.SHUT_RDWR)
      except OSError:
        pass
      self._sock.close()

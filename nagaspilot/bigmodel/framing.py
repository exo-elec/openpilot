"""Message framing on the USB bulk transport (Android): where the stream stays NPBM and the pipe adds padding.

A bulk transfer ends on a short packet, so a message whose length is an exact multiple of the packet size would never end.
Rules (the same ones Jetlink's measurements arrived at; docs/NAVPILOT_BIGMODEL.md D8):

- comma -> phone: every message is padded with zeros to a multiple of BURST (16 KB), so none ends on a short packet and the
  phone's 16 KB reads never hold the end of one message while waiting for the next
- phone -> comma: one pad byte when the message length is an exact multiple of PACKET (512, which divides USB 3's 1024)

The NPBM header carries the length, so the receiver always knows where the message ends and discards the padding.
`BulkSocket` is the comma's end (a socket-like object over the endpoint files, so `Client` is unchanged); `HostBulkSocket` is
the phone's end, the executable spec for the native core and the way the reference server is tested over this framing.
Standard library only.
"""
from __future__ import annotations

import socket
import time

from nagaspilot.bigmodel import protocol as P

BURST = 16 * 1024
PACKET = 512


def pad_to_burst(data: bytes) -> bytes:
  """comma -> phone: zeros up to the next multiple of BURST (a message already on the grid is left alone)."""
  return data + bytes(-len(data) % BURST)


def pad_for_packet(data: bytes) -> bytes:
  """phone -> comma: one byte when the length is an exact multiple of PACKET."""
  return data + b'\0' if data and len(data) % PACKET == 0 else data


class _Endpoint:
  """What a raw pipe provides: read(n, timeout) -> 1..n bytes, raising TimeoutError when none came; write(data); close()."""


class _FramedReader:
  """Delivers the bytes of NPBM messages from a raw stream and discards the padding after each."""

  def __init__(self, endpoint, pad_after):
    self._ep = endpoint
    self._pad_after = pad_after       # message length on the wire -> padding bytes that follow it
    self._buf = bytearray()
    self._remaining = 0               # bytes of the current message still to deliver
    self._skip = 0                    # padding bytes still to discard before the next message
    self.timeout: float | None = None

  def _fill(self, deadline: float | None) -> None:
    left = None if deadline is None else deadline - time.monotonic()
    if left is not None and left <= 0:
      raise TimeoutError('timed out')
    self._buf += self._ep.read(1 << 16, left)

  def _discard_padding(self, deadline) -> None:
    while self._skip:
      if not self._buf:
        self._fill(deadline)
      n = min(self._skip, len(self._buf))
      del self._buf[:n]
      self._skip -= n

  def recv(self, n: int) -> bytes:
    deadline = None if self.timeout is None else time.monotonic() + self.timeout
    self._discard_padding(deadline)
    if self._remaining == 0:           # a new message: its header says how long it is
      while len(self._buf) < P.HEADER_SIZE:
        self._fill(deadline)
      try:
        _, _, _, length = P.parse_header(bytes(self._buf[:P.HEADER_SIZE]))
      except P.ProtocolError as e:
        raise OSError(f'broken stream: {e}') from e
      self._remaining = P.HEADER_SIZE + length
      self._skip_after = self._pad_after(self._remaining)
    if not self._buf:
      self._fill(deadline)
    take = min(n, self._remaining, len(self._buf))
    out = bytes(self._buf[:take])
    del self._buf[:take]
    self._remaining -= take
    if self._remaining == 0:
      self._skip = self._skip_after
    return out


class BulkSocket:
  """The comma's end: socket-like (settimeout / recv / sendall / close) over a raw endpoint."""

  def __init__(self, endpoint):
    self._ep = endpoint
    self._reader = _FramedReader(endpoint, lambda wire: 1 if wire % PACKET == 0 else 0)

  def settimeout(self, t):
    self._reader.timeout = t

  def setsockopt(self, *a):
    pass

  def recv(self, n: int) -> bytes:
    return self._reader.recv(n)

  def sendall(self, data: bytes) -> None:
    self._ep.write(pad_to_burst(data))

  def close(self) -> None:
    self._ep.close()


class HostBulkSocket:
  """The phone's end of the same framing: sends pad for the packet, receives drop the comma's burst padding."""

  def __init__(self, endpoint):
    self._ep = endpoint
    self._reader = _FramedReader(endpoint, lambda wire: -wire % BURST)

  def settimeout(self, t):
    self._reader.timeout = t

  def setsockopt(self, *a):
    pass

  def recv(self, n: int) -> bytes:
    return self._reader.recv(n)

  def sendall(self, data: bytes) -> None:
    self._ep.write(pad_for_packet(data))

  def close(self) -> None:
    self._ep.close()


class SocketEndpoint:
  """A raw endpoint over a connected socket: the tests' pipe, and the shape a USB endpoint file is wrapped in."""

  def __init__(self, sock: socket.socket):
    self._s = sock

  def read(self, n: int, timeout: float | None) -> bytes:
    self._s.settimeout(timeout)
    data = self._s.recv(n)
    if not data:
      raise ConnectionError('closed')
    return data

  def write(self, data: bytes) -> None:
    self._s.settimeout(None)
    self._s.sendall(data)

  def close(self) -> None:
    try:
      self._s.close()
    except OSError:
      pass

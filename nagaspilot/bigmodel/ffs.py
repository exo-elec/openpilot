"""The comma's end of the Android transport: a FunctionFS vendor-specific bulk function (USB device), read by the phone as USB host.

NOT RUN ON A DEVICE. Everything here is checked against fakes (pipes, a fake sysfs); the hazards below are the ones the kernel
interface is known for, and each is handled the way it must be, but only a comma 3X bench can show it works.

Two roles, two processes:
- `FfsControl` (bigmodeld): writes the descriptors to ep0, binds the controller, and keeps ep0 open for the gadget's whole life.
  Closing ep0 tears the function down, so the process that owns it must outlive every link.
- `FfsEndpoint` (modeld): opens ep1 (host -> comma) and ep2 (comma -> host) and moves bytes. It gives `framing.BulkSocket`
  the raw endpoint interface (read / write / close).

Hazards (kernel FunctionFS behaviour):
- Descriptors v2 put every per-speed count first and the descriptor blocks after; interleaving them is EINVAL.
- Opening ep1/ep2 before the host has enabled the function wedges the gadget until reboot: they are opened only once the
  controller's sysfs state reads `configured`.
- FunctionFS ignores O_NONBLOCK once the endpoint is enabled, so a read cannot honour a deadline on the caller's thread: a
  reader thread owns the fd and hands chunks over a condition variable.
- A signal inside a synchronous transfer loses or repeats bytes (the kernel dequeues the request but os.read/os.write
  re-issue it). modeld receives signals constantly, so both IO threads block every signal for their whole life.
- A write never times out by itself: it waits queued until the host drains it. `write()` waits WRITE_TIMEOUT for the writer
  thread, then raises, and closing drops the gadget (unbind) to complete a stuck request.
"""
from __future__ import annotations

import errno
import os
import signal
import struct
import threading
import time
from collections import deque
from pathlib import Path

from nagaspilot.bigmodel import framing as F

FUNCTIONFS_DESCRIPTORS_MAGIC_V2 = 3
FUNCTIONFS_STRINGS_MAGIC = 2
FLAG_HAS_FS, FLAG_HAS_HS, FLAG_HAS_SS = 1, 2, 4
USB_DT_INTERFACE, USB_DT_ENDPOINT, USB_DT_SS_ENDPOINT_COMP = 0x04, 0x05, 0x30
USB_ENDPOINT_XFER_BULK = 0x02
EP_OUT = 0x01   # host -> comma
EP_IN = 0x82    # comma -> host
VENDOR_CLASS = (0xFF, 0x00, 0x00)   # class, subclass, protocol
SS_PACKET = 1024
READ_CHUNK = F.BURST                 # one kmalloc'd buffer per read: the burst size
WRITE_CHUNK = 8 * 1024               # the largest size SLUB serves from a slab cache (no compaction stalls)
WRITE_TIMEOUT = 15.0
MAX_QUEUED = 512 << 10
UDC_ROOT = Path('/sys/class/udc')
MOUNT = '/dev/ffs-ngpbigmodel'
_IO_SIGNALS = signal.valid_signals()


def _interface_desc(n_endpoints: int = 2, string_index: int = 1) -> bytes:
  return struct.pack('<BBBBBBBBB', 9, USB_DT_INTERFACE, 0, 0, n_endpoints, *VENDOR_CLASS, string_index)


def _endpoint_desc(addr: int, max_packet: int) -> bytes:
  return struct.pack('<BBBBHB', 7, USB_DT_ENDPOINT, addr, USB_ENDPOINT_XFER_BULK, max_packet, 0)


def _ss_companion(max_burst: int = 15) -> bytes:
  """bMaxBurst counts additional packets: 15 is a 16 KB burst. Without bursting a 400 KB request takes over the frame budget."""
  return struct.pack('<BBBBH', 6, USB_DT_SS_ENDPOINT_COMP, max_burst, 0, 0)


def build_descriptors() -> bytes:
  fs = _interface_desc() + _endpoint_desc(EP_OUT, 64) + _endpoint_desc(EP_IN, 64)
  hs = _interface_desc() + _endpoint_desc(EP_OUT, 512) + _endpoint_desc(EP_IN, 512)
  ss = (_interface_desc() + _endpoint_desc(EP_OUT, SS_PACKET) + _ss_companion()
        + _endpoint_desc(EP_IN, SS_PACKET) + _ss_companion())
  # head: magic, total length, flags; then one count per speed (three descriptors each), then the blocks
  counts = struct.pack('<III', 3, 3, 5)
  body = counts + fs + hs + ss
  flags = FLAG_HAS_FS | FLAG_HAS_HS | FLAG_HAS_SS
  return struct.pack('<III', FUNCTIONFS_DESCRIPTORS_MAGIC_V2, 12 + len(body), flags) + body


def build_strings(name: str = 'navpilot big model') -> bytes:
  s = name.encode() + b'\0'
  return struct.pack('<IIII', FUNCTIONFS_STRINGS_MAGIC, 16 + 2 + len(s), 1, 1) + struct.pack('<H', 0x0409) + s


def udc_state(root: Path = UDC_ROOT) -> str | None:
  try:
    for udc in sorted(root.iterdir()):
      return (udc / 'state').read_text().strip()
  except OSError:
    pass
  return None


def configured(root: Path = UDC_ROOT) -> bool:
  return udc_state(root) == 'configured'


class FfsControl:
  """bigmodeld's ep0: descriptors, bind, and the function's events. Hold it for the gadget's whole life."""

  # struct usb_functionfs_event: 8 bytes of union, a type byte, 3 bytes of padding
  EVENT = struct.Struct('<8sB3x')
  BIND, UNBIND, ENABLE, DISABLE, SETUP, SUSPEND, RESUME = range(7)

  def __init__(self, mount: str = MOUNT, gadget: str | None = None, udc_root: Path = UDC_ROOT):
    self.mount, self.gadget, self.udc_root = mount, gadget, Path(udc_root)
    self.ep0 = -1

  def open(self, bind: bool = True) -> None:
    self.ep0 = os.open(os.path.join(self.mount, 'ep0'), os.O_RDWR)
    try:
      os.write(self.ep0, build_descriptors())
      os.write(self.ep0, build_strings())
      if bind:
        self.bind()
    except BaseException:
      self.close()
      raise

  def udc_name(self) -> str:
    for udc in sorted(self.udc_root.iterdir()):
      return udc.name
    raise OSError(errno.ENODEV, 'no USB device controller')

  def bind(self) -> None:
    """Last: a FunctionFS gadget cannot attach until its descriptors are written, which is why the script leaves UDC empty."""
    if self.gadget is None:
      raise OSError('no gadget directory to bind')
    Path(self.gadget, 'UDC').write_text(self.udc_name())

  def unbind(self) -> None:
    if self.gadget is not None:
      try:
        Path(self.gadget, 'UDC').write_text('\n')
      except OSError:
        pass

  def events(self) -> list[int]:
    """Event types waiting on ep0 (non-blocking). SETUP requests need no data stage here and are left to the kernel's stall."""
    out = []
    if self.ep0 < 0:
      return out
    os.set_blocking(self.ep0, False)
    while True:
      try:
        raw = os.read(self.ep0, self.EVENT.size)
      except BlockingIOError:
        break
      if len(raw) < self.EVENT.size:
        break
      out.append(self.EVENT.unpack(raw)[1])
    return out

  def close(self) -> None:
    if self.ep0 >= 0:
      try:
        os.close(self.ep0)
      except OSError:
        pass
      self.ep0 = -1


class FfsEndpoint:
  """modeld's ep1/ep2 as the raw endpoint `framing.BulkSocket` wants. Construct only once the host has configured the gadget."""

  def __init__(self, fd_in: int, fd_out: int, clock=time.monotonic):
    self._rd, self._wr = fd_in, fd_out
    self._clock = clock
    self._cv = threading.Condition()
    self._chunks: deque[bytes] = deque()
    self._queued = 0
    self._error: BaseException | None = None
    self._closed = False
    self._out: deque[tuple[bytes, threading.Event, list]] = deque()
    self._out_cv = threading.Condition()
    self._reader = threading.Thread(target=self._read_loop, daemon=True)
    self._writer = threading.Thread(target=self._write_loop, daemon=True)
    self._reader.start()
    self._writer.start()

  @classmethod
  def open(cls, mount: str = MOUNT, udc_root: Path = UDC_ROOT, timeout: float = 10.0, sleep=time.sleep,
           clock=time.monotonic) -> FfsEndpoint:
    """Wait for the controller to read `configured`, then open the endpoint files. Opening earlier wedges the gadget."""
    deadline = clock() + timeout
    while not configured(udc_root):
      if clock() >= deadline:
        raise TimeoutError('the host has not configured the gadget')
      sleep(0.05)
    rd = os.open(os.path.join(mount, 'ep1'), os.O_RDONLY)
    try:
      wr = os.open(os.path.join(mount, 'ep2'), os.O_WRONLY)
    except BaseException:
      os.close(rd)
      raise
    return cls(rd, wr)

  # -- IO threads: every signal blocked, so an interrupted transfer cannot lose or repeat bytes
  def _read_loop(self) -> None:
    signal.pthread_sigmask(signal.SIG_BLOCK, _IO_SIGNALS)
    while not self._closed:
      try:
        data = os.read(self._rd, READ_CHUNK)
      except OSError as e:
        self._fail(e)
        return
      if not data:
        self._fail(ConnectionError('the host closed the endpoint'))
        return
      with self._cv:
        while self._queued >= MAX_QUEUED and not self._closed:
          self._cv.wait(0.05)         # the consumer stalled: stop reading rather than grow without bound
        self._chunks.append(data)
        self._queued += len(data)
        self._cv.notify_all()

  def _write_loop(self) -> None:
    signal.pthread_sigmask(signal.SIG_BLOCK, _IO_SIGNALS)
    while True:
      with self._out_cv:
        while not self._out and not self._closed:
          self._out_cv.wait()
        if self._closed and not self._out:
          return
        data, done, result = self._out.popleft()
      try:
        view = memoryview(data)
        while view:
          n = os.write(self._wr, view[:WRITE_CHUNK])
          view = view[n:]
      except OSError as e:
        result.append(e)
        self._fail(e)
      done.set()

  def _fail(self, e: BaseException) -> None:
    with self._cv:
      self._error = self._error or e
      self._cv.notify_all()

  # -- the raw endpoint interface
  def read(self, n: int, timeout: float | None) -> bytes:
    deadline = None if timeout is None else self._clock() + timeout
    with self._cv:
      while not self._chunks:
        if self._error is not None:
          raise ConnectionError(str(self._error))
        left = None if deadline is None else deadline - self._clock()
        if left is not None and left <= 0:
          raise TimeoutError('timed out')
        self._cv.wait(left)
      chunk = self._chunks.popleft()
      if len(chunk) > n:
        self._chunks.appendleft(chunk[n:])
        chunk = chunk[:n]
      self._queued -= len(chunk)
      self._cv.notify_all()
      return chunk

  def write(self, data: bytes) -> None:
    if self._error is not None:
      raise ConnectionError(str(self._error))
    done, result = threading.Event(), []
    with self._out_cv:
      self._out.append((bytes(data), done, result))
      self._out_cv.notify()
    if not done.wait(WRITE_TIMEOUT):
      raise TimeoutError('the host is not taking data')
    if result:
      raise ConnectionError(str(result[0]))

  def close(self) -> None:
    self._closed = True
    with self._out_cv:
      self._out_cv.notify_all()
    with self._cv:
      self._cv.notify_all()
    for fd in (self._rd, self._wr):
      try:
        os.close(fd)
      except OSError:
        pass


class FfsListener:
  """Like listener.Listener, for the bulk transport: take() gives a BulkSocket once the phone has configured the gadget."""

  def __init__(self, mount: str = MOUNT, udc_root: Path = UDC_ROOT, opener=FfsEndpoint.open):
    self.mount, self.udc_root, self._open = mount, udc_root, opener
    self.bound = True              # the owner process holds the gadget; there is no address to wait for
    self._taken_session = False

  def start(self):
    return self

  def take(self, timeout: float):
    if self._taken_session:
      # one open per enumeration: the endpoint files stay busy until the previous link is gone and the host re-enumerates
      if configured(self.udc_root):
        return None
      self._taken_session = False
    try:
      endpoint = self._open(self.mount, self.udc_root, min(timeout, 10.0))
    except (TimeoutError, OSError):
      return None
    self._taken_session = True
    return F.BulkSocket(endpoint)

  def close(self) -> None:
    pass

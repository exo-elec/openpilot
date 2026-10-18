"""PLP over BLE GATT, the transport-independent part: chunk a frame stream to the link's MTU and pace it.

A BLE stack hands the transport two things: a function that sends one notification (at most MTU - 3 bytes) and the writes the phone makes. `BleStream` turns those into the
byte pipe a `PlpSession` wants. Whole frames are queued and one sender thread writes them chunk by chunk, so two frames can never interleave (the NCP sender took its lock per
chunk). A notification the stack refuses (queue full) is retried after a short wait, which is the pacing; a link that stays blocked is dropped. No BlueZ import here; the D-Bus
objects live in bluetoothd (`system/bluetoothd/plp_ble.py`), which exists only on ExoPilot boards: a comma 3 has no BLE hardware.
"""
from __future__ import annotations

import logging
import queue
import threading
import time
from collections.abc import Callable

logger = logging.getLogger('plp.ble')

ATT_OVERHEAD = 3                 # opcode (1) + handle (2)
DEFAULT_ATT_MTU = 23             # what a link has before the phone asks for more
MIN_USABLE_PAYLOAD = 64          # below this a link stays NCP v4.1 (spec 4.2)
RETRY_WAIT_S = 0.01
BLOCKED_LIMIT_S = 5.0            # a notification refused for this long ends the link


def usable_payload(att_mtu: int) -> int:
  return max(0, att_mtu - ATT_OVERHEAD)


def split(data: bytes, att_mtu: int) -> list[bytes]:
  n = usable_payload(att_mtu)
  if n <= 0:
    raise ValueError('ATT MTU too small')
  return [data[i:i + n] for i in range(0, len(data), n)]


class BleStream:
  def __init__(self, notify: Callable[[bytes], bool], on_dead: Callable[[], None] | None = None, clock: Callable[[], float] = time.monotonic,
               sleep: Callable[[float], None] = time.sleep) -> None:
    """`notify(chunk)` returns True when the stack took the chunk and False when its queue is full."""
    self._notify = notify
    self._on_dead = on_dead
    self._clock, self._sleep = clock, sleep
    self.att_mtu = DEFAULT_ATT_MTU
    self._q: queue.Queue[bytes | None] = queue.Queue()
    self._thread: threading.Thread | None = None
    self.dropped_frames = 0

  def set_mtu(self, att_mtu: int) -> None:
    self.att_mtu = att_mtu

  def plp_usable(self) -> bool:
    return usable_payload(self.att_mtu) >= MIN_USABLE_PAYLOAD

  def start(self) -> None:
    if self._thread is None:
      self._thread = threading.Thread(target=self._run, daemon=True, name='plp_ble_tx')
      self._thread.start()

  def stop(self) -> None:
    self._q.put(None)
    if self._thread is not None:
      self._thread.join(timeout=2.0)
      self._thread = None

  def send(self, frame_bytes: bytes) -> None:
    """The `send` callable of a PlpSession: queues one whole frame."""
    self._q.put(frame_bytes)

  def _run(self) -> None:
    while True:
      frame = self._q.get()
      if frame is None:
        return
      if not self._send_frame(frame):
        self.dropped_frames += 1
        if self._on_dead:
          self._on_dead()
        return

  def _send_frame(self, frame: bytes) -> bool:
    for chunk in split(frame, self.att_mtu):
      blocked_since = self._clock()
      while not self._notify(chunk):
        if self._clock() - blocked_since > BLOCKED_LIMIT_S:
          logger.warning('plp ble: notifications blocked for %.0f s, ending the link', BLOCKED_LIMIT_S)
          return False
        self._sleep(RETRY_WAIT_S)
    return True

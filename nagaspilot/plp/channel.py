"""Sending and receiving whole messages over PLP frames: fragmentation, priority between channels, reassembly.

`Outbox` splits each message into frames no bigger than the link allows and hands them out one at a time, lowest channel number first, so a small vehicle message
overtakes a half-sent bulk message at the next frame boundary. A channel that has waited `STARVATION_LIMIT` frames is served once regardless, so bulk still moves.
`Reassembler` puts the fragments of each channel back together and enforces the negotiated limits.
"""
from __future__ import annotations

from collections import deque
from dataclasses import dataclass

from nagaspilot.plp.frame import FLAG_CRITICAL, FLAG_MORE, FLAG_NO_PAYLOAD_CRC, MAX_FRAME_PAYLOAD, Frame

CHANNELS = 4
STARVATION_LIMIT = 16
DEFAULT_MAX_MESSAGE = 4 << 20


@dataclass(frozen=True)
class Message:
  kind: int
  service: int
  message: int
  payload: bytes = b''
  channel: int = 2
  encoding: int = 1
  request_id: int = 0
  critical: bool = False


class Outbox:
  def __init__(self, max_payload: int) -> None:
    if not 1 <= max_payload <= MAX_FRAME_PAYLOAD:
      raise ValueError('max_payload out of range')
    self.max_payload = max_payload
    self.payload_crc = True                    # set from the peer's hello
    self._queues: list[deque[Frame]] = [deque() for _ in range(CHANNELS)]
    self._waited = [0] * CHANNELS

  def send(self, m: Message) -> None:
    if not 0 <= m.channel < CHANNELS:
      raise ValueError('bad channel')
    parts = [m.payload[i:i + self.max_payload] for i in range(0, len(m.payload), self.max_payload)] or [b'']
    for n, part in enumerate(parts):
      flags = (FLAG_MORE if n < len(parts) - 1 else 0) | (FLAG_CRITICAL if m.critical else 0) | (0 if self.payload_crc else FLAG_NO_PAYLOAD_CRC)
      self._queues[m.channel].append(Frame(m.kind, m.service, m.message, part, m.channel, m.encoding, m.request_id, flags))

  def pending(self) -> int:
    return sum(len(q) for q in self._queues)

  def next_frame(self) -> bytes | None:
    ready = [c for c in range(CHANNELS) if self._queues[c]]
    if not ready:
      return None
    pick = ready[0]
    starved = [c for c in ready[1:] if self._waited[c] >= STARVATION_LIMIT]
    if starved:
      pick = starved[0]
    for c in ready:
      self._waited[c] = 0 if c == pick else self._waited[c] + 1
    return self._queues[pick].popleft().encode()


class Reassembler:
  """Returns a Message when its last fragment arrives. A fragment that does not belong, or a message over the limit, drops that channel's partial message and counts it."""

  def __init__(self, max_message: int = DEFAULT_MAX_MESSAGE) -> None:
    self.max_message = max_message
    self._partial: dict[int, tuple[Frame, bytearray]] = {}
    self.dropped = 0

  def push(self, f: Frame) -> Message | None:
    head, buf = self._partial.get(f.channel, (None, None))
    if head is not None and (f.kind, f.service, f.message, f.request_id, f.encoding) != (head.kind, head.service, head.message, head.request_id, head.encoding):
      self.dropped += 1                      # a new message began before the old one ended
      head, buf = None, None
    if head is None:
      head, buf = f, bytearray()
    buf += f.payload
    if len(buf) > self.max_message:
      self.dropped += 1
      self._partial.pop(f.channel, None)
      return None
    if f.more:
      self._partial[f.channel] = (head, buf)
      return None
    self._partial.pop(f.channel, None)
    return Message(head.kind, head.service, head.message, bytes(buf), head.channel, head.encoding, head.request_id, head.critical)

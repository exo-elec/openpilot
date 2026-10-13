"""One PLP link, whatever carries it: bytes in, bytes out, handlers by message.

The session owns the decoder, reassembly, the outgoing priority queue, the hello and the ping. It does not know whether the bytes travel over the USB cable (`cable.py`),
BLE or SPP; a transport feeds `feed()` and supplies a `send` callable. Handlers are registered by registry name ("locate.gnss_fix") and may return a Message to send back.
A handler that raises answers a request with `bad_payload` and never takes the session down.
"""
from __future__ import annotations

import logging
import threading
from collections.abc import Callable

from nagaspilot.plp import dispatch, registry
from nagaspilot.plp.channel import Message, Outbox, Reassembler
from nagaspilot.plp.frame import CH_CONTROL, ENC_JSON, FrameDecoder
from nagaspilot.plp.hello import Agreed, Hello, negotiate

logger = logging.getLogger('plp')

Handler = Callable[[Message], 'Message | None']
HELLO = registry.BY_NAME['core.hello']
PING = registry.BY_NAME['core.ping']
ERR_NO_HELLO = 'no_hello'
ERR_WRONG_DIRECTION = 'wrong_direction'


class PlpSession:
  def __init__(self, local: Hello, send: Callable[[bytes], None], role: str = 'device') -> None:
    if role not in ('device', 'phone'):
      raise ValueError('role is device or phone')
    self.local = local
    self.role = role
    self.agreed: Agreed | None = None
    self._send = send
    self._decoder = FrameDecoder()
    self._reassembler = Reassembler(local.max_message)
    self._outbox = Outbox(min(local.max_payload, 512))        # until the hello says what the peer accepts
    self._handlers: dict[tuple[int, int], Handler] = {}
    self._lock = threading.Lock()

  def on(self, name: str, handler: Handler) -> None:
    self._handlers[registry.BY_NAME[name].key] = handler

  # -- sending -------------------------------------------------------------------------
  def send(self, m: Message) -> None:
    with self._lock:
      self._outbox.send(m)
      while (raw := self._outbox.next_frame()) is not None:
        self._send(raw)

  def hello_request(self) -> None:
    """The phone opens the link; the device answers in `_on_hello`."""
    self.send(Message(registry.KIND_REQUEST, HELLO.service, HELLO.message, self.local.to_json(), CH_CONTROL, ENC_JSON, 1))

  # -- receiving -----------------------------------------------------------------------
  def feed(self, data: bytes) -> None:
    for frame in self._decoder.feed(data):
      m = self._reassembler.push(frame)
      if m is not None:
        self._dispatch(m)

  def _dispatch(self, m: Message) -> None:
    key = (m.service, m.message)
    if key == HELLO.key:
      self._on_hello(m)
      return
    if self.agreed is None:
      if m.kind == registry.KIND_REQUEST:
        self.send(dispatch.error_reply(m, ERR_NO_HELLO))
      return
    d = registry.MESSAGES.get(key)
    if d is not None and key == PING.key and m.kind == registry.KIND_REQUEST:
      self.send(Message(registry.KIND_RESPONSE, m.service, m.message, m.payload, m.channel, m.encoding, m.request_id))
      return
    if d is not None and d.direction == ('d2p' if self.role == 'device' else 'p2d'):
      if m.kind == registry.KIND_REQUEST:
        self.send(dispatch.error_reply(m, ERR_WRONG_DIRECTION, d.full_name))
      return
    handler = self._handlers.get(key)
    if handler is None:
      if (r := dispatch.unknown_reply(m)) is not None:
        self.send(r)
      return
    try:
      reply = handler(m)
    except ValueError as e:                        # a bad payload (BadFix, BadHorizon and BadHello are ValueErrors)
      logger.warning('plp %s: %s', d.full_name if d else key, e)
      if m.kind == registry.KIND_REQUEST:
        self.send(dispatch.error_reply(m, dispatch.ERR_BAD_PAYLOAD, str(e)))
      return
    if reply is not None:
      self.send(reply)

  def _on_hello(self, m: Message) -> None:
    try:
      remote = Hello.parse(m.payload)
    except ValueError as e:
      if m.kind == registry.KIND_REQUEST:
        self.send(dispatch.error_reply(m, dispatch.ERR_BAD_PAYLOAD, str(e)))
      return
    agreed = negotiate(self.local, remote)
    if agreed is None:
      if m.kind == registry.KIND_REQUEST:
        self.send(dispatch.error_reply(m, dispatch.ERR_UNSUPPORTED, 'no common version'))
      return
    self.agreed = agreed
    if m.kind == registry.KIND_REQUEST:
      self.send(Message(registry.KIND_RESPONSE, HELLO.service, HELLO.message, self.local.to_json(), CH_CONTROL, ENC_JSON, m.request_id))
    self._outbox.max_payload = max(1, min(agreed.max_payload, self.local.max_payload))
    self._outbox.payload_crc = agreed.send_payload_crc

"""The comma's end of the link: a blocking TCP client for the phone's model server (protocol.py).

Knows nothing about openpilot. Every failure is a LinkError, which RemoteModelState and Joining catch and answer with the
small model. A frame blocks until its reply or `deadline`; only a stall past FRAME_TIMEOUT is a failure, a late reply is
the caller's to hold (remote_state.HOLD_FRAME).
"""
from __future__ import annotations

import socket
import time

from nagaspilot.bigmodel import protocol as P
from nagaspilot.bigmodel.allowlist import AllowList, NotAllowed
from nagaspilot.bigmodel.spec import ModelSpec

CONNECT_TIMEOUT = 2.0
HELLO_TIMEOUT = 5.0
# no reply for this long means the far end is gone, not slow (Chestnut's own wait timeout is 3 s)
FRAME_TIMEOUT = 0.2
PING_TIMEOUT = 2.0


class LinkError(Exception):
  pass


class LinkTimeout(LinkError):
  pass


class ServerInfo:
  def __init__(self, ack: dict):
    self.contract: str = str(ack.get('contract', P.CONTRACT_SPLIT))
    self.model: str = str(ack.get('model', ''))
    self.sha256: str = str(ack.get('sha256', ''))
    self.vision_floats: int = int(ack.get('vision_floats', 0))
    self.policy_floats: int = int(ack.get('policy_floats', 0))
    self.ready: bool = bool(ack.get('ready', False))
    self.spec: ModelSpec | None = ModelSpec.from_dict(ack['spec']) if isinstance(ack.get('spec'), dict) else None

  @property
  def out_floats(self) -> int:
    """The floats an INFER_RESP carries."""
    return self.spec.reply_nelem if self.spec is not None else self.vision_floats + self.policy_floats


class Client:
  def __init__(self, host: str = P.COMMA_ADDR, port: int = P.DEFAULT_PORT, *, sock: socket.socket | None = None, clock=time.monotonic,
               contracts: tuple[str, ...] = (P.CONTRACT_SPLIT,), allow: AllowList | None = None):
    self.host, self.port = host, port
    self.contracts = contracts       # offered to the phone, best first
    self.allow = allow or AllowList()   # the combined models the comma will run
    self._sock = sock
    self._clock = clock
    self._seq = 0
    self.dead = False
    self.info: ServerInfo | None = None
    self._pending: int | None = None   # seq of the request sent and not yet answered
    self._rest_deadline = 0.0

  # -- connection
  def connect(self) -> ServerInfo:
    """Open (unless handed a socket), say hello, return what the server runs. Raises LinkError."""
    try:
      if self._sock is None:
        self._sock = socket.create_connection((self.host, self.port), timeout=CONNECT_TIMEOUT)
      if isinstance(self._sock, socket.socket):
        self._sock.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
      self._send(P.pack(P.HELLO, self._next(), P.hello_payload(self.contracts)))
      msg = self._recv(self._clock() + HELLO_TIMEOUT)
    except OSError as e:
      self._fail(f'connect: {e}')
    if msg.type == P.ERROR:
      self._fail(f"server refused: {self._error_text(msg)}")
    if msg.type != P.HELLO_ACK:
      self._fail(f'expected HELLO_ACK, got {P.TYPE_NAMES.get(msg.type, msg.type)}')
    try:
      ack = P.decode_json(msg.payload)
    except P.ProtocolError as e:
      self._fail(str(e))
    if not ack.get('ok', False):
      self._fail(f"server not ok: {ack.get('error', 'no reason')}")
    try:
      info = ServerInfo(ack)
      self._vet(info)
    except (ValueError, KeyError, TypeError, NotAllowed) as e:
      self._fail(f'the phone announced a model the comma will not run: {e}')
    self.info = info
    return info

  def _vet(self, info: ServerInfo) -> None:
    """The comma's side of the safety rule: the contract is one it offered, and a spec'd model is one it allow-lists."""
    if info.contract not in self.contracts:
      raise ValueError(f'contract {info.contract!r} was not offered')
    if info.contract == P.CONTRACT_SPLIT:
      return
    if info.spec is None:
      raise ValueError('no model spec')
    if info.spec.warped_shape != P.FRAME_SHAPE:
      raise ValueError(f'frames of {info.spec.warped_shape}, the comma warps {P.FRAME_SHAPE}')
    self.allow.check(info.spec)

  def close(self) -> None:
    self.dead = True
    sock, self._sock = self._sock, None
    if sock is not None:
      try:
        sock.close()
      except OSError:
        pass

  # -- frames
  def send_frame(self, frame_id: int, flags: int, desire, traffic, frame) -> int:
    """Send one INFER_REQ; returns its seq. One request may be outstanding."""
    if self._pending is not None:
      self._fail('a frame is already outstanding')
    seq = self._next()
    self._send(P.pack_infer_req(seq, frame_id, flags, desire, traffic, frame))
    self._pending = seq
    return seq

  def send_frame_packed(self, frame_id: int, flags: int, packed, frame) -> int:
    """Send one INFER_REQ for any contract: the packed floats the contract lays out, then the warped frame."""
    if self._pending is not None:
      self._fail('a frame is already outstanding')
    seq = self._next()
    self._send(P.pack_infer_req_packed(seq, frame_id, flags, packed, frame))
    self._pending = seq
    return seq

  def recv_response(self, seq: int, timeout: float) -> P.InferResp | None:
    """The reply to `seq`, or None if it is not back within `timeout` (it stays outstanding: ask again next frame).
    Raises LinkError when the link is broken or the reply has been outstanding past FRAME_TIMEOUT beyond its due time."""
    deadline = self._clock() + max(timeout, 0.0)
    try:
      while True:
        msg = self._recv(deadline, partial_ok=True)
        if msg is None:
          return None
        if msg.type == P.ERROR:
          self._fail(f'server error: {self._error_text(msg)}')
        if msg.type == P.PONG:
          continue
        if msg.type != P.INFER_RESP:
          self._fail(f'unexpected {P.TYPE_NAMES.get(msg.type, msg.type)}')
        if msg.seq == self._pending:
          self._pending = None
        # a reply to an earlier seq (a held frame's, read late) is returned to the caller as it is
        return P.unpack_infer_resp(msg.payload)
    except P.ProtocolError as e:
      self._fail(str(e))

  @property
  def outstanding(self) -> bool:
    return self._pending is not None

  def ping(self, timeout: float = PING_TIMEOUT) -> None:
    if self._pending is not None:
      return   # a frame in flight proves the link better than a ping
    self._send(P.pack(P.PING, self._next()))
    deadline = self._clock() + timeout
    try:
      while True:
        msg = self._recv(deadline)
        if msg.type == P.PONG:
          return
        if msg.type == P.ERROR:
          self._fail(f'server error: {self._error_text(msg)}')
    except P.ProtocolError as e:
      self._fail(str(e))

  def leave(self, reason: str, **stats) -> None:
    """Say why the comma stops using the link. Best effort; says nothing over a dead link."""
    if self.dead or self._sock is None:
      return
    try:
      self._send(P.pack(P.LEAVE, self._next(), P.encode_json({'reason': reason, 'stats': stats})))
    except LinkError:
      pass

  # -- plumbing
  def _next(self) -> int:
    self._seq = (self._seq + 1) & 0xFFFFFFFF
    return self._seq

  def _fail(self, why: str):
    self.close()
    raise LinkError(why)

  @staticmethod
  def _error_text(msg: P.Message) -> str:
    try:
      return str(P.decode_json(msg.payload).get('error', ''))
    except P.ProtocolError:
      return 'unreadable error'

  def _send(self, data: bytes) -> None:
    if self._sock is None:
      raise LinkError('not connected')
    try:
      self._sock.settimeout(FRAME_TIMEOUT * 5)
      self._sock.sendall(data)
    except OSError as e:
      self._fail(f'send: {e}')

  def _read_exact(self, n: int, deadline: float, may_idle: bool = False):
    """n bytes. The first byte may take until `deadline`: if `may_idle` and none came, None (nothing was lost); otherwise
    that is a failure. Once bytes are coming the rest must follow within FRAME_TIMEOUT or the stream is broken."""
    buf = bytearray()
    while len(buf) < n:
      left = deadline - self._clock() if not buf else self._rest_deadline - self._clock()
      if left <= 0 and (buf or not may_idle):
        self._fail('timed out' if not buf else 'timed out mid-message')
      try:
        self._sock.settimeout(max(left, 1e-4))
        chunk = self._sock.recv(n - len(buf))
      except TimeoutError:
        if not buf and may_idle:
          return None
        self._fail('timed out' if not buf else 'timed out mid-message')
      except OSError as e:
        self._fail(f'recv: {e}')
      if not chunk:
        self._fail('closed by the far end')
      if not buf:
        self._rest_deadline = self._clock() + FRAME_TIMEOUT
      buf += chunk
    return bytes(buf)

  def _recv(self, deadline: float, partial_ok: bool = False):
    if self._sock is None:
      raise LinkError('not connected')
    head = self._read_exact(P.HEADER_SIZE, deadline, partial_ok)
    if head is None:
      return None
    try:
      mtype, flags, seq, length = P.parse_header(head)
    except P.ProtocolError as e:
      self._fail(str(e))
    payload = self._read_exact(length, self._clock() + FRAME_TIMEOUT) if length else b''
    return P.Message(mtype, seq, flags, payload)

  def __enter__(self):
    return self

  def __exit__(self, *exc):
    self.close()

import socket
import threading

import numpy as np
import pytest

from nagaspilot.bigmodel import framing as F
from nagaspilot.bigmodel import protocol as P
from nagaspilot.bigmodel import server_ref as S
from nagaspilot.bigmodel.client import Client


def test_padding_rules():
  assert len(F.pad_to_burst(b'x' * 20)) == F.BURST
  assert len(F.pad_to_burst(b'x' * F.BURST)) == F.BURST             # already on the grid
  assert len(F.pad_to_burst(b'x' * (F.BURST + 1))) == 2 * F.BURST
  assert F.pad_for_packet(b'x' * 511) == b'x' * 511
  assert F.pad_for_packet(b'x' * 512) == b'x' * 512 + b'\0'
  assert F.pad_for_packet(b'') == b''


def pair():
  a, b = socket.socketpair()
  return F.BulkSocket(F.SocketEndpoint(a)), F.HostBulkSocket(F.SocketEndpoint(b))


def read_message(sock):
  head = b''
  while len(head) < P.HEADER_SIZE:
    head += sock.recv(P.HEADER_SIZE - len(head))
  _, _, _, length = P.parse_header(head)
  body = b''
  while len(body) < length:
    body += sock.recv(length - len(body))
  return head, body


def test_comma_to_phone_messages_arrive_whole_with_padding_dropped():
  comma, phone = pair()
  phone.settimeout(2)
  big = P.pack_infer_req(1, 1, 0, [0] * 8, [1, 0], bytes(P.FRAME_BYTES))
  def send():
    for m in (P.pack(P.PING, 1), big, P.pack(P.PING, 2)):
      comma.sendall(m)
  threading.Thread(target=send, daemon=True).start()   # a socketpair holds ~200 KB: the writer must not block the reader
  assert read_message(phone)[0][5] == P.PING
  head, body = read_message(phone)
  assert head[5] == P.INFER_REQ and len(body) == len(big) - P.HEADER_SIZE
  assert read_message(phone)[0][8:12] == (2).to_bytes(4, 'little')


def test_phone_to_comma_pad_byte_only_on_exact_multiples_and_is_dropped():
  comma, phone = pair()
  comma.settimeout(2)
  # a reply whose wire length is exactly 512: header 20 + payload 492 (12 fixed + 480 of floats)
  exact = P.pack_infer_resp(3, 1, P.OK, 5, bytes(480))
  assert len(exact) == 512
  phone.sendall(exact)
  phone.sendall(P.pack(P.PONG, 4))
  _, body = read_message(comma)
  assert len(body) == 492
  head, _ = read_message(comma)
  assert head[5] == P.PONG


def test_a_damaged_header_on_the_bulk_stream_is_an_error():
  a, b = socket.socketpair()
  comma = F.BulkSocket(F.SocketEndpoint(a))
  comma.settimeout(1)
  b.sendall(b'z' * 64)
  with pytest.raises(OSError, match='broken'):
    comma.recv(20)


def test_a_silent_bulk_stream_times_out():
  comma, _ = pair()
  comma.settimeout(0.05)
  with pytest.raises(TimeoutError):
    comma.recv(20)


def test_the_whole_protocol_runs_over_the_bulk_framing():
  a, b = socket.socketpair()
  server = S.Server(S.StubBackend(), listen=False)
  threading.Thread(target=server.serve, args=(F.HostBulkSocket(F.SocketEndpoint(b)),), daemon=True).start()
  with Client(sock=F.BulkSocket(F.SocketEndpoint(a))) as c:
    assert c.connect().model == 'stub'
    local, backend = S.State(), S.StubBackend()
    for i, v in enumerate((3, 9, 27)):
      want = S.step(local, backend, [0] * 8, (1, 0), np.full(P.FRAME_SHAPE, v, np.uint8))
      seq = c.send_frame(i + 1, P.RESET if i == 0 else 0, [0] * 8, (1, 0), bytes([v]) * P.FRAME_BYTES)
      got = c.recv_response(seq, 2.0)
      assert got is not None and np.array_equal(np.frombuffer(got.out, np.float32), want)
    c.ping()

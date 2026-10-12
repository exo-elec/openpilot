import socket
import time

import numpy as np
import pytest

from nagaspilot.bigmodel import protocol as P
from nagaspilot.bigmodel import server_ref as S
from nagaspilot.bigmodel.client import Client, LinkError


def frame(value=0):
  return np.full(P.FRAME_BYTES, value, np.uint8).tobytes()


@pytest.fixture
def server():
  s = S.Server(S.StubBackend(), '127.0.0.1', 0)
  yield s
  s.close()


def connect(server):
  c = Client('127.0.0.1', server.port)
  c.connect()
  return c


def infer(c, fid, value=0, flags=0, desire=(0,) * 8, traffic=(1, 0)):
  seq = c.send_frame(fid, flags, desire, traffic, frame(value))
  resp = c.recv_response(seq, 1.0)
  assert resp is not None
  return resp


def test_hello_returns_what_the_server_runs(server):
  with Client('127.0.0.1', server.port) as c:
    info = c.connect()
    assert (info.model, info.vision_floats, info.policy_floats, info.ready) == ('stub', 1024, 600, True)
    assert info.out_floats == 1624


def test_a_frame_round_trips_with_the_output_size_the_hello_promised(server):
  with connect(server) as c:
    r = infer(c, 5, value=10)
    assert (r.frame_id, r.status) == (5, P.OK)
    assert len(bytes(r.out)) == c.info.out_floats * 4
    assert not c.outstanding


def test_server_state_matches_an_independent_replay(server):
  """Two frames through the server equal two steps of the state machine on its own."""
  local, backend = S.State(), S.StubBackend()
  with connect(server) as c:
    for i, v in enumerate((3, 9, 27)):
      desire = (0, 1.0 if i == 1 else 0.0, 0, 0, 0, 0, 0, 0)
      want = S.step(local, backend, desire, (1, 0), np.full(P.FRAME_SHAPE, v, np.uint8))
      got = np.frombuffer(infer(c, i, v, desire=desire).out, np.float32)
      assert np.array_equal(got, want)


def test_reset_flag_clears_history(server):
  with connect(server) as c:
    a = np.frombuffer(infer(c, 1, 5, flags=P.RESET).out, np.float32).copy()
    infer(c, 2, 77)
    b = np.frombuffer(infer(c, 3, 5, flags=P.RESET).out, np.float32)
    assert np.array_equal(a, b)


def test_non_finite_output_keeps_the_last_good_state():
  class Nan(S.StubBackend):
    bad = False

    def run_vision(self, imgs):
      out = super().run_vision(imgs)
      if self.bad:
        out[3] = np.nan
      return out

  backend = Nan()
  server = S.Server(backend, '127.0.0.1', 0)
  try:
    with connect(server) as c:
      infer(c, 1, 4)
      before = server.state.features.copy()
      backend.bad = True
      r = infer(c, 2, 8)
      assert r.status == P.NOT_FINITE and len(bytes(r.out)) == 0
      assert np.array_equal(server.state.features, before)
      backend.bad = False
      assert infer(c, 3, 8).status == P.OK
  finally:
    server.close()


def test_desire_is_a_pulse_on_the_rising_edge():
  st, backend = S.State(), S.StubBackend()
  f = np.zeros(P.FRAME_SHAPE, np.uint8)
  up = (0, 1, 0, 0, 0, 0, 0, 0)
  S.step(st, backend, up, (1, 0), f)
  assert st.full_desire[0, -1, 1] == 1
  S.step(st, backend, up, (1, 0), f)      # held high: no new pulse
  assert st.full_desire[0, -1, 1] == 0
  assert st.full_desire[0, -2, 1] == 1


def test_feature_history_uses_the_temporal_skip():
  st, backend = S.State(), S.StubBackend()
  for v in range(1, 11):
    S.step(st, backend, (0,) * 8, (1, 0), np.full(P.FRAME_SHAPE, v, np.uint8))
  assert st.features.shape == (1, S.FULL_HISTORY, S.FEATURE_LEN)
  # a feature is 2 cameras x mean(older, newest frame): frame 10 sees (6, 10), frame 6 sees (2, 6)
  newest, back4 = st.features[0, -1, 0], st.features[0, -1 - S.TEMPORAL_SKIP, 0]
  assert newest == pytest.approx(16) and back4 == pytest.approx(8)


def test_a_new_hello_resets_the_server_and_replaces_the_connection(server):
  a = connect(server)
  infer(a, 1, 5)
  b = connect(server)
  assert not np.any(server.state.features)
  with pytest.raises(LinkError):
    infer(a, 2, 5)
  assert infer(b, 3, 5).status == P.OK
  b.close()


def test_a_late_reply_is_none_then_readable(server):
  class Slow(S.StubBackend):
    def run_vision(self, imgs):
      time.sleep(0.15)
      return super().run_vision(imgs)
  slow = S.Server(Slow(), '127.0.0.1', 0)
  try:
    with connect(slow) as c:
      seq = c.send_frame(1, 0, (0,) * 8, (1, 0), frame())
      assert c.recv_response(seq, 0.02) is None and c.outstanding
      resp = c.recv_response(seq, 1.0)
      assert resp is not None and resp.frame_id == 1 and not c.outstanding
  finally:
    slow.close()


def test_one_frame_at_a_time(server):
  with connect(server) as c:
    c.send_frame(1, 0, (0,) * 8, (1, 0), frame())
    with pytest.raises(LinkError):
      c.send_frame(2, 0, (0,) * 8, (1, 0), frame())


def test_ping_and_leave(server):
  with connect(server) as c:
    c.ping()
    c.leave(P.LEAVE_BEHIND, held=3)
    time.sleep(0.1)
    assert server.leaves == [{'reason': 'behind', 'stats': {'held': 3}}]


def test_connection_refused_is_a_link_error():
  s = socket.socket()
  s.bind(('127.0.0.1', 0))
  port = s.getsockname()[1]
  s.close()
  with pytest.raises(LinkError):
    Client('127.0.0.1', port).connect()


def test_a_server_that_hangs_up_is_a_link_error(server):
  c = connect(server)
  server.close()
  with pytest.raises(LinkError):
    infer(c, 1)
  assert c.dead


def test_a_garbled_reply_kills_the_link():
  l = socket.socket()
  l.bind(('127.0.0.1', 0))
  l.listen(1)
  import threading

  def junk():
    conn, _ = l.accept()
    conn.recv(100)
    conn.sendall(b'garbage that is long enough to be a header....')
  threading.Thread(target=junk, daemon=True).start()
  with pytest.raises(LinkError):
    Client('127.0.0.1', l.getsockname()[1]).connect()
  l.close()


def test_a_comma_on_another_contract_is_refused(server, monkeypatch):
  monkeypatch.setattr(P, 'hello_payload', lambda: P.encode_json({'contract': 'something-else', 'frame': list(P.FRAME_SHAPE), 'desire': 8, 'traffic': 2}))
  with pytest.raises(LinkError, match='contract'):
    Client('127.0.0.1', server.port).connect()


def test_the_phone_dials_the_comma_and_the_comma_drives_the_protocol():
  import threading
  from nagaspilot.bigmodel.listener import Listener
  listener = Listener('127.0.0.1', 0).start()
  deadline = time.monotonic() + 10
  while not listener.bound and time.monotonic() < deadline:
    time.sleep(0.01)
  assert listener.bound, 'the listener never bound'
  stop = threading.Event()
  t = threading.Thread(target=S.dial, args=(S.StubBackend(), '127.0.0.1', listener.port), kwargs=dict(retry=0.05, stop=stop), daemon=True)
  t.start()
  try:
    sock = listener.take(10.0)
    assert sock is not None
    with Client(sock=sock) as c:
      assert c.connect().model == 'stub'
      assert infer(c, 1, 3).status == P.OK
  finally:
    stop.set()
    listener.close()


def test_the_listener_keeps_only_the_newest_phone():
  from nagaspilot.bigmodel.listener import Listener
  listener = Listener('127.0.0.1', 0).start()
  deadline = time.monotonic() + 10
  while not listener.bound and time.monotonic() < deadline:
    time.sleep(0.01)
  assert listener.bound, 'the listener never bound'
  a = socket.create_connection(('127.0.0.1', listener.port))
  time.sleep(0.1)
  b = socket.create_connection(('127.0.0.1', listener.port))
  time.sleep(0.1)
  got = listener.take(1.0)
  assert got is not None and got.getpeername() == b.getsockname()
  assert listener.take(0.05) is None
  got.close(), a.close(), b.close()
  listener.close()


def test_the_listener_waits_for_the_cable_interface_to_exist():
  from nagaspilot.bigmodel import listener as L
  l = L.Listener('203.0.113.9', 0)         # an address this machine does not have
  l.start()
  time.sleep(0.2)
  assert not l.bound and l.take(0.05) is None
  l.close()

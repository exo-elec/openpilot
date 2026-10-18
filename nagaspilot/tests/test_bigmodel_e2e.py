"""The comma side against the reference phone server over a real socket: Client, RemoteModelState and Joining together."""
import logging
import threading
import time

import numpy as np

from nagaspilot.bigmodel import protocol as P
from nagaspilot.bigmodel import server_ref as S
from nagaspilot.bigmodel.client import Client
from nagaspilot.bigmodel.joining import Joining
from nagaspilot.bigmodel.listener import Listener
from nagaspilot.bigmodel.remote_state import RemoteModelState

VISION, POLICY = 1024, 600


class Warp:
  def __init__(self):
    self.output = memoryview(bytearray(P.FRAME_BYTES))
    self.n = 0

  def start_buffers(self, bufs, transforms):
    self.n += 1
    self.output[:] = bytes([self.n % 251]) * P.FRAME_BYTES

  def wait(self):
    pass


class Parser:
  def parse_vision_outputs(self, d):
    return {'hidden_state': d['hidden'].copy()}

  def parse_policy_outputs(self, d):
    return {'plan': d['plan'].copy()}


def remote(client, info):
  return RemoteModelState(client, info, Warp(), parser=Parser(), vision_slices={'hidden': slice(0, 512)},
                          policy_slices={'plan': slice(0, 600)}, get_action_from_model=lambda *a: 'a', log=logging.getLogger('t'))


def INPUTS():
  return {'desire': np.zeros(8, np.float32), 'traffic_convention': np.array([[1, 0]], np.float32)}


class SmallModel:
  desire_key = 'desire'
  numpy_inputs = {}
  lat_delay = 0.0

  def run(self, *a, **k):
    return 'small'


def test_remote_state_over_a_socket_matches_the_servers_own_replay():
  server = S.Server(S.StubBackend(VISION, POLICY), '127.0.0.1', 0)
  try:
    client = Client('127.0.0.1', server.port)
    info = client.connect()
    state = remote(client, info)
    local, backend, warp = S.State(), S.StubBackend(VISION, POLICY), Warp()
    for _ in range(6):
      got = state.run({}, {}, INPUTS())
      warp.start_buffers({}, {})
      want = S.step(local, backend, np.zeros(8), (1, 0), np.frombuffer(bytes(warp.output), np.uint8).reshape(P.FRAME_SHAPE))
      assert np.array_equal(got['hidden_state'][0], want[:512])
      assert np.array_equal(got['plan'][0], want[VISION:VISION + 600])
    assert state.behind is None and state.trips.frames == 6
    client.close()
  finally:
    server.close()


def test_a_phone_that_dials_in_is_joined_swapped_in_and_dropped_when_the_cable_goes():
  listener = Listener('127.0.0.1', 0).start()
  deadline = time.monotonic() + 10
  while not listener.bound and time.monotonic() < deadline:
    time.sleep(0.01)
  assert listener.bound, 'the listener never bound'
  stop = threading.Event()
  threading.Thread(target=S.dial, args=(S.StubBackend(VISION, POLICY), '127.0.0.1', listener.port),
                   kwargs=dict(retry=0.05, stop=stop), daemon=True).start()

  def connect():
    sock = listener.take(0.5)
    if sock is None:
      raise OSError('no phone')
    c = Client(sock=sock)
    return c, c.connect()

  joining = Joining(SmallModel(), connect, remote, None, log=logging.getLogger('t'), in_control=lambda: False)
  try:
    deadline = time.monotonic() + 5
    while joining.big_model_state != 'ready' and time.monotonic() < deadline:
      time.sleep(0.01)
    assert joining.big_model_state == 'ready'
    outs = [joining.run({}, {}, INPUTS()) for _ in range(8)]
    assert outs[:3] == ['small'] * 3
    assert joining.big_model_state == 'running' and joining.is_big
    assert 'hidden_state' in outs[-1]
    # the phone goes away: the next frame fails, the small model re-runs it, and the join starts over
    stop.set()
    joining._big.client.close()
    assert joining.run({}, {}, INPUTS()) == 'small'
    assert joining.big_model_state == 'retrying' and not joining.is_big
  finally:
    joining.close()
    listener.close()
    stop.set()

"""The stateful layout (openpilot #38916, Cinque Terre V3 onward): new_img in, state_<q> in and next_state_<q> out looped back, no queues."""
import logging

import numpy as np
import pytest

from nagaspilot.bigmodel import protocol as P
from nagaspilot.bigmodel import server_ref as S
from nagaspilot.bigmodel.allowlist import AllowList
from nagaspilot.bigmodel.client import Client, LinkError, ServerInfo
from nagaspilot.bigmodel.queues import PolicyQueues, StateLoop, make_history
from nagaspilot.bigmodel.remote_state import RemoteSpecModelState
from nagaspilot.bigmodel.spec import ModelSpec

SHAPES = {'new_img': (2, 6, 128, 256), 'desire': (1, 8), 'traffic_convention': (1, 2), 'action_t': (1, 2),
          'state_feat': (1, 16), 'state_hist': (1, 4, 3)}
OUTS = {'outputs': (1, 24), 'next_state_feat': (1, 16), 'next_state_hist': (1, 4, 3)}
SLICES = {'plan': slice(0, 6), 'meta': slice(6, 24)}      # no hidden_state: it stays in the graph


def make_spec(**kw) -> ModelSpec:
  args = dict(sha256='ef' * 32, nbytes=1, frame_skip=4, input_shapes=SHAPES, output_shapes=OUTS, output_slices=SLICES, checkpoint='t')
  args.update(kw)
  return ModelSpec(**args)


def pin(sp):
  return {k: sp.to_dict()[k] for k in ('frame_skip', 'input_shapes', 'output_shapes', 'output_slices')}


def test_the_spec_reads_a_stateful_graph():
  sp = make_spec()
  assert sp.stateful and sp.model_hw == (128, 256) and sp.warped_shape == (2, 6, 128, 256)
  assert sp.state_pairs == {'state_feat': 'next_state_feat', 'state_hist': 'next_state_hist'}
  assert sp.packed_shapes == {'desire': (8,), 'traffic_convention': (1, 2), 'action_t': (1, 2)} and sp.packed_nelem == 12
  assert sp.hidden_range is None and sp.reply_nelem == 24
  assert isinstance(make_history(sp), StateLoop) and isinstance(make_history(make_spec(input_shapes={
    'img': (1, 12, 128, 256), 'big_img': (1, 12, 128, 256), 'desire_pulse': (1, 33, 8), 'traffic_convention': (1, 2), 'action_t': (1, 2),
    'features_buffer': (1, 32, 2, 4)}, output_shapes={'outputs': (1, 28)}, output_slices={'hidden_state': slice(2, 10)})), PolicyQueues)


def test_the_loop_feeds_each_next_state_back_as_the_next_state_and_reset_zeroes_them():
  sp = make_spec()
  loop = StateLoop(sp)
  warped, packed = np.zeros(sp.warped_shape, np.uint8), np.zeros(12, np.float32)
  first = loop.step(warped, packed)
  assert not first['state_feat'].any() and first['new_img'].shape == (2, 6, 128, 256) and first['desire'].shape == (1, 8)
  out = {'outputs': np.zeros(24, np.float32), 'next_state_feat': np.arange(16, dtype=np.float32).reshape(1, 16),
         'next_state_hist': np.full((1, 4, 3), 7, np.float32)}
  loop.after_run(out)
  second = loop.step(warped, packed)
  assert np.array_equal(second['state_feat'], out['next_state_feat']) and np.array_equal(second['state_hist'], out['next_state_hist'])
  loop.new_client()                                                  # a hello changes nothing
  assert loop.step(warped, packed)['state_feat'].any()
  loop.reset()
  assert not loop.step(warped, packed)['state_feat'].any()


def test_a_graph_with_no_state_pairs_or_mismatched_ones_is_refused():
  with pytest.raises(ValueError, match='next_state_'):
    StateLoop(make_spec(output_shapes={'outputs': (1, 24)}))
  with pytest.raises(ValueError, match='state_feat'):
    StateLoop(make_spec(output_shapes={**OUTS, 'next_state_feat': (1, 17)}))
  with pytest.raises(ValueError, match='stateful'):
    StateLoop(make_spec(input_shapes={k: v for k, v in SHAPES.items() if k != 'new_img'}))


@pytest.fixture
def served():
  sp = make_spec()
  server = S.Server(S.StubBackend(), '127.0.0.1', 0, spec_backend=S.StubStatefulBackend(sp))
  yield sp, server
  server.close()


def test_the_server_offers_the_stateful_contract_and_the_client_vets_it(served):
  sp, server = served
  with pytest.raises(LinkError, match='allow-list'):
    Client('127.0.0.1', server.port, contracts=(P.CONTRACT_STATEFUL,)).connect()
  c = Client('127.0.0.1', server.port, contracts=(P.CONTRACT_QUEUED, P.CONTRACT_STATEFUL, P.CONTRACT_SPLIT),
             allow=AllowList({sp.sha256: {'name': 's', **pin(sp)}}))
  info = c.connect()
  assert info.contract == P.CONTRACT_STATEFUL and info.spec == sp and info.out_floats == 24
  c.close()
  # a comma that only knows the queued contract gets the split one: this server's model is not queued
  with Client('127.0.0.1', server.port, contracts=(P.CONTRACT_QUEUED, P.CONTRACT_SPLIT)) as c2:
    assert c2.connect().contract == P.CONTRACT_SPLIT


def test_a_contract_that_does_not_match_the_graph_is_refused():
  c = Client(contracts=(P.CONTRACT_QUEUED,))
  info = ServerInfo({'ok': True, 'contract': P.CONTRACT_QUEUED, 'ready': True, 'spec': make_spec().to_dict()})
  c.allow = AllowList({info.spec.sha256: {'name': 's', **pin(info.spec)}})
  with pytest.raises(ValueError, match='does not match'):
    c._vet(info)


def test_frames_round_trip_and_match_an_independent_replay(served):
  sp, server = served
  local, backend = StateLoop(sp), S.StubStatefulBackend(sp)
  with Client('127.0.0.1', server.port, contracts=(P.CONTRACT_STATEFUL,), allow=AllowList({sp.sha256: {'name': 's', **pin(sp)}})) as c:
    c.connect()
    for i in range(10):
      packed = [1.0 if i == 3 else 0.0] + [0.0] * 7 + [1.0, 0.0, 0.275, 0.525]
      frame = bytes([(i * 53) % 251]) * P.FRAME_BYTES
      if i == 0:
        local.reset()
      want = S.step_spec(local, backend, packed, np.frombuffer(frame, np.uint8).reshape(sp.warped_shape))
      seq = c.send_frame_packed(i + 1, P.RESET if i == 0 else 0, packed, frame)
      r = c.recv_response(seq, 3.0)
      assert r.status == P.OK and np.array_equal(np.frombuffer(r.out, np.float32), want)


def test_the_state_carries_between_frames_and_a_reset_clears_it(served):
  sp, server = served
  with Client('127.0.0.1', server.port, contracts=(P.CONTRACT_STATEFUL,), allow=AllowList({sp.sha256: {'name': 's', **pin(sp)}})) as c:
    c.connect()

    def go(i, flags, fill):
      seq = c.send_frame_packed(i, flags, [0.0] * 12, bytes([fill]) * P.FRAME_BYTES)
      return np.frombuffer(c.recv_response(seq, 3.0).out, np.float32).copy()
    a = [go(1, P.RESET, 5), go(2, 0, 5), go(3, 0, 5)]
    assert not np.array_equal(a[0], a[1])                  # the same frame, a different answer: the state moved
    b = [go(4, P.RESET, 5), go(5, 0, 5), go(6, 0, 5)]
    assert all(np.array_equal(x, y) for x, y in zip(a, b, strict=True))


def test_a_non_finite_state_leaves_the_state_as_it_was():
  sp = make_spec()
  loop, backend = StateLoop(sp), S.StubStatefulBackend(sp)
  warped, packed = np.zeros(sp.warped_shape, np.uint8), np.zeros(12, np.float32)
  S.step_spec(loop, backend, packed, warped)
  before = {k: v.copy() for k, v in loop.state.items()}

  class Bad(S.StubStatefulBackend):
    def run(self, inputs):
      out = super().run(inputs)
      out['next_state_feat'][0, 3] = np.nan
      return out
  assert S.step_spec(loop, Bad(sp), packed, warped) is None
  assert all(np.array_equal(loop.state[k], before[k]) for k in before)


def test_the_comma_state_packs_and_parses_a_stateful_model_like_any_spec():
  sp = make_spec()
  server = S.Server(S.StubBackend(), '127.0.0.1', 0, spec_backend=S.StubStatefulBackend(sp))

  class Warp:
    output = memoryview(bytearray(P.FRAME_BYTES))

    def start_buffers(self, b, t):
      pass

    def wait(self):
      pass
  try:
    client = Client('127.0.0.1', server.port, contracts=(P.CONTRACT_STATEFUL,), allow=AllowList({sp.sha256: {'name': 's', **pin(sp)}}))
    info = client.connect()
    seen = []
    state = RemoteSpecModelState(client, info, Warp(), parse=lambda d: seen.append(d) or d, get_action_from_model=None, log=logging.getLogger('t'))
    inputs = {'desire': np.zeros(8, np.float32), 'traffic_convention': np.array([[1, 0]], np.float32), 'action_t': np.array([0.3, 0.5], np.float32)}
    for _ in range(4):
      state.run({}, {}, inputs)
    assert state.trips.frames == 4 and state.behind is None
    assert seen[0]['plan'].shape == (1, 6) and seen[0]['meta'].shape == (1, 18)
    client.close()
  finally:
    server.close()

"""Step 4: the comma's state for a combined (spec'd) model."""
import logging
import math

import numpy as np
import pytest

from nagaspilot.bigmodel import protocol as P
from nagaspilot.bigmodel import server_ref as S
from nagaspilot.bigmodel.allowlist import AllowList
from nagaspilot.bigmodel.client import Client, LinkError, ServerInfo
from nagaspilot.bigmodel.queues import PolicyQueues
from nagaspilot.bigmodel.remote_state import RemoteSpecModelState
from nagaspilot.bigmodel.spec import ModelSpec

SHAPES = {'img': (1, 12, 128, 256), 'big_img': (1, 12, 128, 256), 'desire_pulse': (1, 33, 8), 'traffic_convention': (1, 2),
          'action_t': (1, 2), 'features_buffer': (1, 32, 2, 4)}
# outputs: plan 0..2, hidden_state 2..10, lead 10..14, meta 14..28 (a slice after the hidden state)
SLICES = {'plan': slice(0, 2), 'hidden_state': slice(2, 10), 'lead': slice(10, 14), 'meta': slice(14, 28)}


def make_spec(**kw) -> ModelSpec:
  args = dict(sha256='cd' * 32, nbytes=1, frame_skip=4, input_shapes=SHAPES, output_shapes={'outputs': (1, 28)}, output_slices=SLICES,
              checkpoint='t')
  args.update(kw)
  return ModelSpec(**args)


def test_reply_slices_skip_the_hidden_state_and_shift_what_follows_it():
  sp = make_spec()
  assert sp.reply_nelem == 20
  assert sp.reply_slices == {'plan': slice(0, 2, 1), 'lead': slice(2, 6, 1), 'meta': slice(6, 20, 1)}
  with pytest.raises(ValueError, match='overlaps'):
    _ = make_spec(output_slices={**SLICES, 'bad': slice(8, 12)}).reply_slices


class Warp:
  output = memoryview(bytearray(P.FRAME_BYTES))

  def start_buffers(self, bufs, transforms):
    pass

  def wait(self):
    pass


class Clock:
  t = 100.0

  def __call__(self):
    return self.t


class FakeClient:
  """Answers at once; remembers what it was sent."""

  def __init__(self, sp):
    self.sp, self.sent, self.dead = sp, [], False

  def send_frame_packed(self, frame_id, flags, packed, frame):
    self.sent.append((frame_id, flags, list(packed), len(frame)))
    return len(self.sent)

  def recv_response(self, seq, timeout):
    out = np.arange(self.sp.reply_nelem, dtype=np.float32) + seq
    return P.InferResp(self.sent[seq - 1][0], P.OK, 50, memoryview(out.tobytes()))

  def close(self):
    pass


def make_state(sp=None, parse=None):
  sp = sp or make_spec()
  seen = []

  def record(d):
    seen.append(d)
    return {'parsed': True, **d}
  info = ServerInfo({'ok': True, 'contract': P.CONTRACT_QUEUED, 'ready': True, 'spec': sp.to_dict()})
  state = RemoteSpecModelState(FakeClient(sp), info, Warp(), parse=parse or record, get_action_from_model=lambda *a: 'a',
                               log=logging.getLogger('t'), clock=Clock())
  return state, seen


def inputs(desire=None, action_t=(0.3, 0.5)):
  return {'desire': np.array(desire if desire is not None else [0] * 8, np.float32), 'traffic_convention': np.array([[1, 0]], np.float32),
          'action_t': np.array(action_t, np.float32)}


def test_a_frame_packs_desire_traffic_and_action_t_as_the_spec_lays_them_out():
  state, _ = make_state()
  state.run({}, {}, inputs([0, 1, 0, 0, 0, 0, 0, 0]))
  frame_id, flags, packed, nbytes = state.client.sent[0]
  assert flags == P.RESET and nbytes == P.FRAME_BYTES
  assert packed == pytest.approx([0, 1, 0, 0, 0, 0, 0, 0, 1, 0, 0.3, 0.5])


def test_desire_is_a_pulse_on_the_rising_edge_and_slot_zero_is_never_sent():
  state, _ = make_state()
  up = [1, 1, 0, 0, 0, 0, 0, 0]
  state.run({}, {}, inputs(up))
  state.run({}, {}, inputs(up))
  state.run({}, {}, inputs([0] * 8))
  state.run({}, {}, inputs(up))
  pulses = [sent[2][:8] for sent in state.client.sent]
  assert pulses[0] == [0, 1, 0, 0, 0, 0, 0, 0]      # slot 0 is cleared
  assert pulses[1] == [0] * 8                        # held high: no new pulse
  assert pulses[3] == [0, 1, 0, 0, 0, 0, 0, 0]      # a new rising edge


def test_the_parser_gets_the_reply_cut_by_the_specs_slices():
  state, seen = make_state()
  out = state.run({}, {}, inputs())
  d = seen[0]
  assert d['plan'].shape == (1, 2) and d['lead'].shape == (1, 4) and d['meta'].shape == (1, 14) and 'hidden_state' not in d
  assert d['plan'][0].tolist() == [1, 2]             # reply = arange + seq(1)
  assert d['lead'][0].tolist() == [3, 4, 5, 6]       # reply index 2.., which is full-output index 10..
  assert out['parsed'] is True


def test_a_desire_or_scalar_of_the_wrong_width_is_an_error_not_a_wrong_input():
  state, _ = make_state()
  with pytest.raises(LinkError, match='desire'):
    state.run({}, {}, {**inputs(), 'desire': np.zeros(5, np.float32)})
  state, _ = make_state()
  with pytest.raises(LinkError, match='action_t'):
    state.run({}, {}, inputs(action_t=(1, 2, 3)))


def test_a_reply_of_the_wrong_size_is_an_error():
  state, _ = make_state()
  state.client.recv_response = lambda seq, timeout: P.InferResp(1, P.OK, 1, memoryview(np.zeros(3, np.float32).tobytes()))
  with pytest.raises(LinkError, match='promised'):
    state.run({}, {}, inputs())


def test_a_spec_contract_without_a_spec_is_refused_at_construction():
  with pytest.raises(ValueError, match='spec'):
    RemoteSpecModelState(FakeClient(make_spec()), ServerInfo({'ok': True, 'contract': P.CONTRACT_QUEUED}), Warp(), parse=dict,
                         get_action_from_model=None, log=logging.getLogger('t'))


def test_over_a_real_socket_the_comma_gets_what_an_independent_replay_computes():
  sp = make_spec()
  server = S.Server(S.StubBackend(), '127.0.0.1', 0, spec_backend=S.StubSpecBackend(sp))
  allow = AllowList({sp.sha256: {'name': 's', **{k: sp.to_dict()[k] for k in ('frame_skip', 'input_shapes', 'output_shapes', 'output_slices')}}})
  try:
    client = Client('127.0.0.1', server.port, contracts=(P.CONTRACT_QUEUED,), allow=allow)
    info = client.connect()
    got = []
    state = RemoteSpecModelState(client, info, Warp(), parse=lambda d: {k: v.copy() for k, v in d.items()}, get_action_from_model=None,
                                 log=logging.getLogger('t'))
    local, backend = PolicyQueues(sp), S.StubSpecBackend(sp)
    local.new_client()
    for i in range(6):
      warp_fill = (i * 41) % 251
      Warp.output = memoryview(bytearray([warp_fill]) * P.FRAME_BYTES)
      got.append(state.run({}, {}, inputs([0] * 8)))
      want = S.step_spec(local, backend, [0] * 8 + [1, 0, 0.3, 0.5], np.full(sp.warped_shape, warp_fill, np.uint8))
      assert np.array_equal(got[-1]['plan'][0], want[0:2])
      assert np.array_equal(got[-1]['lead'][0], want[2:6])
      assert math.isclose(float(got[-1]['meta'][0][0]), float(want[6]))
    assert state.behind is None and state.trips.frames == 6
    client.close()
  finally:
    server.close()
    Warp.output = memoryview(bytearray(P.FRAME_BYTES))

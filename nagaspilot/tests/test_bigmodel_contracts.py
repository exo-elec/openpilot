"""Step 3: the contract list in HELLO, the spec in HELLO_ACK, per-spec requests and replies, and the comma's allow-list."""
import math

import numpy as np
import pytest

from nagaspilot.bigmodel import protocol as P
from nagaspilot.bigmodel import server_ref as S
from nagaspilot.bigmodel.allowlist import AllowList, NotAllowed
from nagaspilot.bigmodel.client import Client, LinkError
from nagaspilot.bigmodel.spec import ModelSpec

SHAPES = {'img': (1, 12, 128, 256), 'big_img': (1, 12, 128, 256), 'desire_pulse': (1, 33, 8), 'traffic_convention': (1, 2),
          'action_t': (1, 2), 'features_buffer': (1, 32, 2, 4)}
FEAT = math.prod(SHAPES['features_buffer'][2:])


def spec(**kw) -> ModelSpec:
  args = dict(sha256='cd' * 32, nbytes=1, frame_skip=4, input_shapes=SHAPES, output_shapes={'outputs': (1, FEAT + 20)},
              output_slices={'hidden_state': slice(2, 2 + FEAT)}, checkpoint='t')
  args.update(kw)
  return ModelSpec(**args)


def allow(sp: ModelSpec) -> AllowList:
  return AllowList({sp.sha256: {'name': 'stub', **{k: sp.to_dict()[k] for k in ('frame_skip', 'input_shapes', 'output_shapes', 'output_slices')}}})


@pytest.fixture
def served():
  sp = spec()
  server = S.Server(S.StubBackend(), '127.0.0.1', 0, spec_backend=S.StubSpecBackend(sp))
  yield sp, server
  server.close()


def connect(server, **kw):
  c = Client('127.0.0.1', server.port, **kw)
  c.connect()
  return c


def test_hello_picks_the_first_contract_the_server_supports():
  assert P.check_hello(P.decode_json(P.hello_payload((P.CONTRACT_QUEUED, P.CONTRACT_SPLIT))), (P.CONTRACT_SPLIT, P.CONTRACT_QUEUED)) == P.CONTRACT_QUEUED
  assert P.check_hello(P.decode_json(P.hello_payload((P.CONTRACT_QUEUED, P.CONTRACT_SPLIT))), (P.CONTRACT_SPLIT,)) == P.CONTRACT_SPLIT
  with pytest.raises(P.ProtocolError, match='contracts'):
    P.check_hello(P.decode_json(P.hello_payload((P.CONTRACT_QUEUED,))), (P.CONTRACT_SPLIT,))
  with pytest.raises(P.ProtocolError, match='contract list'):
    P.check_hello({'frame': list(P.FRAME_SHAPE), 'desire': 8, 'traffic': 2})


def test_a_split_comma_still_gets_the_split_contract_from_a_server_that_offers_both(served):
  _, server = served
  with connect(server) as c:
    assert c.info.contract == P.CONTRACT_SPLIT and c.info.spec is None and c.info.out_floats == 1624


def test_a_spec_contract_is_refused_unless_the_model_is_on_the_allow_list(served):
  sp, server = served
  with pytest.raises(LinkError, match='allow-list'):
    connect(server, contracts=(P.CONTRACT_QUEUED,))
  with pytest.raises(LinkError, match='allow-list'):
    connect(server, contracts=(P.CONTRACT_QUEUED,), allow=AllowList())


def test_an_allow_listed_model_is_accepted_and_its_spec_arrives_intact(served):
  sp, server = served
  with connect(server, contracts=(P.CONTRACT_QUEUED, P.CONTRACT_SPLIT), allow=allow(sp)) as c:
    assert c.info.contract == P.CONTRACT_QUEUED and c.info.spec == sp and c.info.out_floats == sp.reply_nelem


@pytest.mark.parametrize('change', [dict(frame_skip=2), dict(output_slices={'hidden_state': slice(4, 4 + FEAT)}),
                                    dict(input_shapes={**SHAPES, 'action_t': (1, 3)})])
def test_a_model_that_differs_from_the_allowed_one_in_any_pinned_field_is_refused(served, change):
  sp, server = served
  pinned = ('frame_skip', 'input_shapes', 'output_shapes', 'output_slices')
  tampered = AllowList({sp.sha256: {'name': 'stub', **{k: spec(**change).to_dict()[k] for k in pinned}}})
  with pytest.raises(LinkError, match='differs'):
    connect(server, contracts=(P.CONTRACT_QUEUED,), allow=tampered)


def test_a_phone_that_announces_a_spec_with_other_frames_than_the_comma_warps_is_refused():
  bad = spec(input_shapes={**SHAPES, 'img': (1, 12, 64, 256), 'big_img': (1, 12, 64, 256)})
  server = S.Server(S.StubBackend(), '127.0.0.1', 0, spec_backend=S.StubSpecBackend(bad))
  try:
    with pytest.raises(LinkError, match='warps'):
      connect(server, contracts=(P.CONTRACT_QUEUED,), allow=allow(bad))
  finally:
    server.close()


def test_frames_round_trip_and_match_an_independent_replay(served):
  sp, server = served
  local, backend = PolicyQ(sp), S.StubSpecBackend(sp)
  with connect(server, contracts=(P.CONTRACT_QUEUED,), allow=allow(sp)) as c:
    for i in range(8):
      packed = [1.0 if i == 3 else 0.0] + [0.0] * 7 + [1.0, 0.0] + [0.275, 0.525]
      frame = bytes([(i * 37) % 251]) * P.FRAME_BYTES
      want = S.step_spec(local, backend, packed, np.frombuffer(frame, np.uint8).reshape(sp.warped_shape))
      seq = c.send_frame_packed(i + 1, P.RESET if i == 0 else 0, packed, frame)
      r = c.recv_response(seq, 3.0)
      assert r.status == P.OK and np.array_equal(np.frombuffer(r.out, np.float32), want)
      assert len(bytes(r.out)) == sp.reply_nelem * 4
    c.ping()


def PolicyQ(sp):
  from nagaspilot.bigmodel.queues import PolicyQueues
  q = PolicyQueues(sp)
  q.new_client()
  return q


def test_a_reset_frame_returns_the_phone_to_its_start_state(served):
  sp, server = served
  with connect(server, contracts=(P.CONTRACT_QUEUED,), allow=allow(sp)) as c:
    def go(i, flags, fill):
      seq = c.send_frame_packed(i, flags, [0.0] * 12, bytes([fill]) * P.FRAME_BYTES)
      return np.frombuffer(c.recv_response(seq, 3.0).out, np.float32).copy()
    first = [go(1, P.RESET, 5), go(2, 0, 99), go(3, 0, 7), go(4, 0, 11), go(5, 0, 13)]
    again = [go(6, P.RESET, 5), go(7, 0, 99), go(8, 0, 7), go(9, 0, 11), go(10, 0, 13)]
    # the features queue is sampled every frame_skip rows, so the hidden state fed back shows from the fifth frame on
    assert all(np.array_equal(a, b) for a, b in zip(first, again, strict=True))


def test_without_a_reset_the_history_changes_the_answer(served):
  sp, server = served
  with connect(server, contracts=(P.CONTRACT_QUEUED,), allow=allow(sp)) as c:
    def go(i, flags, fill):
      seq = c.send_frame_packed(i, flags, [0.0] * 12, bytes([fill]) * P.FRAME_BYTES)
      return np.frombuffer(c.recv_response(seq, 3.0).out, np.float32).copy()
    first = [go(1, P.RESET, 5)] + [go(i, 0, 7) for i in range(2, 7)]
    second = [go(7, 0, 5)] + [go(i, 0, 7) for i in range(8, 13)]     # no reset: the queues still hold the first run
    assert not all(np.array_equal(a, b) for a, b in zip(first, second, strict=True))


def test_a_wrongly_sized_request_is_a_broken_stream_not_a_failed_frame(served):
  sp, server = served
  with connect(server, contracts=(P.CONTRACT_QUEUED,), allow=allow(sp)) as c:
    c.send_frame_packed(1, 0, [0.0] * 5, bytes(P.FRAME_BYTES))     # 5 packed floats where the spec lays out 12
    with pytest.raises(LinkError):
      c.recv_response(1, 2.0)


def test_the_empty_default_allow_list_file_allows_nothing():
  assert not AllowList.load() and AllowList.load().entries == {}
  with pytest.raises(NotAllowed):
    AllowList.load().check(spec())

import math

import numpy as np
import pytest

from nagaspilot.bigmodel import protocol as P
from nagaspilot.bigmodel.queues import PolicyQueues
from nagaspilot.bigmodel.spec import ModelSpec

BIG = {'img': (1, 12, 128, 256), 'big_img': (1, 12, 128, 256), 'desire_pulse': (1, 33, 8), 'traffic_convention': (1, 2),
       'action_t': (1, 2), 'features_buffer': (1, 32, 32, 512)}


def big_spec(**kw) -> ModelSpec:
  args = dict(sha256='ab' * 32, nbytes=766 << 20, frame_skip=4, input_shapes=BIG, output_shapes={'outputs': (1, 18452)},
              output_slices={'hidden_state': slice(2068, 18452)}, checkpoint='test')
  args.update(kw)
  return ModelSpec(**args)


def test_the_big_models_sizes_are_the_ones_the_wire_was_designed_around():
  s = big_spec()
  assert s.n_frames == 2 and s.model_hw == (128, 256) and s.warped_shape == (2, 6, 128, 256)
  assert s.warped_nbytes == P.FRAME_BYTES == 393216
  assert s.feat_dim == 16384                         # prod(shape[2:]), not shape[2]: older forks got 32 here
  assert s.packed_shapes == {'desire': (8,), 'traffic_convention': (1, 2), 'action_t': (1, 2)}
  assert s.packed_nelem == 12                        # 8 + 2 + 2 floats after the frame
  assert s.output_nelem == 18452 and s.hidden_range == (2068, 18452)
  assert s.reply_nelem == 2068 and s.prev_feat_shape == (1, 16384)
  assert s.img_buf_shape == (5, 6, 128, 256)         # frame_skip * (n_frames - 1) + 1 frames of history
  assert s.feat_q_shape == (128, 1, 16384) and s.desire_q_shape == (132, 1, 8)
  assert not s.stateful


def test_the_hidden_state_slice_resolves_as_python_slicing_does():
  assert big_spec(output_slices={'hidden_state': slice(None, 100)}).hidden_range == (0, 100)
  assert big_spec(output_slices={'hidden_state': slice(-100, None)}).hidden_range == (18352, 18452)
  assert big_spec(output_slices={'hidden_state': slice(100, 100)}).hidden_range is None
  assert big_spec(output_slices={}).hidden_range is None
  assert big_spec(output_slices={}).reply_nelem == 18452


def test_a_stateful_graph_is_recognised_and_pairs_its_states():
  shapes = {'new_img': (2, 6, 128, 256), 'desire': (1, 8), 'traffic_convention': (1, 2), 'action_t': (1, 2),
            'state_feat': (1, 16384), 'state_img': (1, 4, 6, 128, 256)}
  s = big_spec(input_shapes=shapes, output_shapes={'outputs': (1, 2068), 'next_state_feat': (1, 16384), 'next_state_img': (1, 4, 6, 128, 256)},
               output_slices={})
  assert s.stateful and s.model_hw == (128, 256)
  assert s.state_pairs == {'state_feat': 'next_state_feat', 'state_img': 'next_state_img'}
  assert s.packed_shapes['desire'] == (8,)


def test_the_wire_form_round_trips():
  s = big_spec()
  assert ModelSpec.from_dict(s.to_dict()) == s


def test_the_npbm_request_and_reply_sizes_follow_from_the_spec():
  s = big_spec()
  assert s.infer_req_nbytes == P.HEADER_SIZE + 8 + 12 * 4 + 393216
  assert s.infer_resp_nbytes == P.HEADER_SIZE + 12 + 2068 * 4


def test_queues_refuse_a_graph_without_a_hidden_state_to_feed_back():
  with pytest.raises(ValueError, match='hidden_state'):
    PolicyQueues(big_spec(output_slices={}))


def test_a_float16_queue_feeds_the_same_values_as_a_float32_one_for_whole_numbers():
  s = big_spec(input_shapes={**BIG, 'img': (1, 12, 2, 4), 'big_img': (1, 12, 2, 4), 'features_buffer': (1, 32, 2, 4)},
               output_shapes={'outputs': (1, 20)}, output_slices={'hidden_state': slice(4, 12)})
  a, b = PolicyQueues(s, np.float16), PolicyQueues(s, np.float32)
  rng = np.random.default_rng(1)
  for _ in range(40):
    warped = rng.integers(0, 256, math.prod(s.warped_shape)).astype(np.uint8).reshape(s.warped_shape)
    packed = rng.integers(0, 3, s.packed_nelem).astype(np.float32)
    fa, fb = a.step(warped, packed), b.step(warped, packed)
    out = {'outputs': rng.integers(0, 100, 20).astype(np.float32)}
    a.after_run(out)
    b.after_run(out)
    for k in fa:
      assert np.array_equal(fa[k].astype(np.float32), fb[k].astype(np.float32)), k

"""
Adapted from Jetlink's tests/test_queues.py (MIT, Copyright (c) 2026 Zeph Leggett; notice in nagaspilot/bigmodel/THIRD_PARTY_NOTICES.md).

The history queues against openpilot's own, as golden vectors. The fixture (tests/fixtures/policy_queues_openpilot.json, from Jetlink) holds a
digest of everything openpilot's shift_and_sample / sample_skip / sample_desire fed the model over 300 frames, enough for every ring to wrap twice, for the
big (Chestnut) layout and the small one. Drift here means the phone model silently sees the wrong history. Regenerating the fixture needs
openpilot's tinygrad and is done from Jetlink's checkout; it is not repeated here.
"""
from __future__ import annotations

import hashlib
import json
import math
from pathlib import Path

import numpy as np
import pytest

from nagaspilot.bigmodel.queues import PolicyQueues
from nagaspilot.bigmodel.spec import ModelSpec

FIXTURE = Path(__file__).parent / 'fixtures' / 'policy_queues_openpilot.json'
# the longest ring is desire_q at frame_skip * 33 = 132 rows
FRAMES = 300
# what the queues feed the model, and the dtype openpilot's queues hold it in
FED = {'img': np.uint8, 'big_img': np.uint8, 'desire_pulse': np.float32, 'features_buffer': np.float32}
# the big (chestnut) layout, 4-D features_buffer and 33 desire steps, and the
# small one; images a few pixels across, which the queues treat alike
CONFIGS = {
  'big': {'img': (1, 12, 2, 4), 'big_img': (1, 12, 2, 4), 'desire_pulse': (1, 33, 8), 'traffic_convention': (1, 2),
          'action_t': (1, 2), 'features_buffer': (1, 32, 2, 4)},
  'small': {'img': (1, 12, 2, 4), 'big_img': (1, 12, 2, 4), 'desire_pulse': (1, 25, 8), 'traffic_convention': (1, 2),
            'action_t': (1, 2), 'features_buffer': (1, 24, 8)},
}


def make_spec(shapes, frame_skip=4) -> ModelSpec:
  # the hidden state is the features queue's row, as the model returns it
  feat = math.prod(shapes['features_buffer'][2:])
  return ModelSpec(sha256='0' * 64, nbytes=0, frame_skip=frame_skip, input_shapes=shapes,
                   output_shapes={'outputs': (1, feat + 4)}, output_slices={'hidden_state': slice(2, 2 + feat)},
                   checkpoint=None)


def frame(spec: ModelSpec, i: int):
  """Frame i's warped frame, desire, packed floats and model output. Whole
  numbers, which float32 holds exactly on either side, and a desire that
  pulses about one frame in two."""
  warped = ((np.arange(math.prod(spec.warped_shape)) * 7 + i * 13) % 256).astype(np.uint8).reshape(spec.warped_shape)
  desire = ((np.arange(spec.packed_shapes['desire'][0]) * 3 + i * 7) % 11 == 0).astype(np.float32)
  packed = np.concatenate([desire, np.full(spec.packed_nelem - desire.size, i % 3, np.float32)])
  output = ((np.arange(spec.output_nelem) * 3 + i * 5) % 251).astype(np.float32)
  return warped, desire, packed, output


def fed_bytes(name: str, value: np.ndarray) -> bytes:
  return np.ascontiguousarray(value, dtype=FED[name]).tobytes()


def queue_digests(shapes) -> dict[str, str]:
  spec = make_spec(shapes)
  # float32, so this compares representation as well as ordering; the server
  # runs float16, which only moves the cast
  queues = PolicyQueues(spec, dtype=np.float32)
  digests = {name: hashlib.sha256() for name in FED}
  for i in range(FRAMES):
    warped, _, packed, output = frame(spec, i)
    fed = queues.step(warped, packed)
    for name, digest in digests.items():
      digest.update(fed_bytes(name, fed[name]))
    queues.after_run({'outputs': output})
  return {name: digest.hexdigest() for name, digest in digests.items()}


@pytest.mark.parametrize('name', list(CONFIGS))
def test_every_frame_feeds_what_openpilot_fed(name):
  expected = json.loads(FIXTURE.read_text())
  assert queue_digests(CONFIGS[name]) == expected[name]


def test_reset_returns_to_initial_state():
  spec = make_spec(CONFIGS['big'])
  q = PolicyQueues(spec, dtype=np.float32)
  for i in range(10):
    q.step(*frame(spec, i)[::2][:2])
  q.reset()

  fresh = PolicyQueues(spec, dtype=np.float32)
  warped = np.zeros(spec.warped_shape, np.uint8)
  packed = np.zeros(spec.packed_nelem, np.float32)
  a, b = q.step(warped, packed), fresh.step(warped, packed)
  for k in a:
    assert np.array_equal(a[k], b[k]), k


def test_sampling_picks_oldest_first():
  """sample_skip must return history in order, oldest first, newest last."""
  spec = make_spec(CONFIGS['big'])
  q = PolicyQueues(spec, dtype=np.float32)
  # img_q holds frame_skip*(n_frames-1)+1 = 5 rows; sampling every 4 gives rows 0 and 4.
  for v in range(1, 8):
    out = q.step(np.full(spec.warped_shape, v, np.uint8), np.zeros(spec.packed_nelem, np.float32))
  img = out['img'].reshape(spec.img_shape)
  oldest, newest = img[0, 0, 0, 0], img[0, 6, 0, 0]
  assert newest == 7, 'newest should be the frame just pushed'
  assert oldest == 3, 'oldest should be 4 frames back'

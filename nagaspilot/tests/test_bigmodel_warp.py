import numpy as np
import pytest

from nagaspilot.controls.ngp_bigmodel_frames import FRAME_BYTES
from nagaspilot.runtime import bigmodel_platform as platform
from nagaspilot.runtime.bigmodel_warp import NgpWarp, NgpWarpSource, reset_function


class FakeFrame:
  """DrivingModelFrame: prepare() returns a handle, buffer_from_cl() the (older, newest) buffer."""

  def __init__(self, newest):
    self.newest = newest
    self.prepared = []

  def prepare(self, buf, projection):
    self.prepared.append((buf, projection))
    return 'cl'

  def buffer_from_cl(self, cl):
    assert cl == 'cl'
    return np.concatenate([np.full(FRAME_BYTES, 1, np.uint8), np.full(FRAME_BYTES, self.newest, np.uint8)])


def small_model():
  m = type('M', (), {})()
  m.frames = {'img': FakeFrame(5), 'big_img': FakeFrame(6)}
  m.full_features_buffer = np.ones((1, 100, 512), np.float32)
  m.full_desire = np.ones((1, 100, 8), np.float32)
  m.prev_desire = np.ones(8, np.float32)
  m.numpy_inputs = {'desire': np.ones((1, 25, 8), np.float32), 'features_buffer': np.ones((1, 25, 512), np.float32)}
  return m


def test_the_output_is_the_newest_frame_of_each_camera_narrow_then_wide():
  m = small_model()
  warp = NgpWarp(m.frames)
  bufs = {'img': 'main', 'big_img': 'extra'}
  tfm = {'img': np.eye(3, dtype=np.float64), 'big_img': 2 * np.eye(3)}
  warp.start_buffers(bufs, tfm)
  warp.wait()
  out = np.frombuffer(warp.output, np.uint8).reshape(2, 6, 128, 256)
  assert (out[0] == 5).all() and (out[1] == 6).all()
  assert len(warp.output) == 2 * FRAME_BYTES
  # the warp is given each camera's buffer and a flat float32 projection, as modeld does
  buf, projection = m.frames['big_img'].prepared[0]
  assert buf == 'extra' and projection.dtype == np.float32 and projection.shape == (9,)
  assert projection[0] == 2.0


def test_the_output_is_a_stable_view_that_follows_the_next_frame():
  m = small_model()
  warp = NgpWarp(m.frames)
  view = warp.output
  warp.start_buffers({'img': 0, 'big_img': 0}, {'img': np.eye(3), 'big_img': np.eye(3)})
  m.frames['img'].newest = 9
  warp.start_buffers({'img': 0, 'big_img': 0}, {'img': np.eye(3), 'big_img': np.eye(3)})
  assert np.frombuffer(view, np.uint8)[0] == 9


def test_the_reset_zeroes_the_small_models_recurrent_state_in_place():
  m = small_model()
  reset = reset_function(m)
  feat = m.full_features_buffer
  reset()
  assert feat is m.full_features_buffer
  for a in (m.full_features_buffer, m.full_desire, m.prev_desire, *m.numpy_inputs.values()):
    assert not a.any()


def test_the_source_needs_the_small_model_before_it_makes_a_warp():
  with pytest.raises(RuntimeError):
    NgpWarpSource().make(*platform.DEVICE_CAMERA, 0)


def test_the_source_builds_for_its_geometry_only():
  src = NgpWarpSource()
  assert src.built() and src.geometry() == platform.DEVICE_CAMERA
  src.prepare_reset(small_model())
  assert isinstance(src.make(*platform.DEVICE_CAMERA, 0), NgpWarp)
  with pytest.raises(RuntimeError):
    src.make(*platform.DEVICE_CAMERA[:2], 1024, 512, 0)

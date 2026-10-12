import numpy as np
import pytest

from nagaspilot.controls.ngp_bigmodel_frames import FRAME_BYTES, OUTPUT_SHAPE, fill_output, newest_frame


def two_frames(old=1, new=2):
  return np.concatenate([np.full(FRAME_BYTES, old, np.uint8), np.full(FRAME_BYTES, new, np.uint8)])


def test_the_frame_is_what_the_model_frame_packs():
  assert FRAME_BYTES == 512 * 256 * 3 // 2
  assert OUTPUT_SHAPE == (2, 6, 128, 256)


def test_newest_is_the_second_half():
  f = newest_frame(two_frames(old=1, new=7))
  assert f.shape == (6, 128, 256)
  assert (f == 7).all()


def test_fill_output_places_narrow_then_wide():
  out = np.zeros(OUTPUT_SHAPE, np.uint8)
  fill_output(out, 0, two_frames(new=3))
  fill_output(out, 1, two_frames(new=9))
  assert (out[0] == 3).all() and (out[1] == 9).all()


def test_a_buffer_of_the_wrong_size_is_refused():
  with pytest.raises(ValueError):
    newest_frame(np.zeros(FRAME_BYTES, np.uint8))

"""NGP10's camera path for the big model: the small model's own OpenCL warp.

NGP10 (v0.10.0) warps with OpenCL (`DrivingModelFrame`, selfdrive/modeld/models/commonmodel.cc), which the small model
already runs every frame. NgpWarp reads those same frame objects: each frame the big model needs is the newest frame of
each camera, so there is no second warp and no extra GPU work. See `nagaspilot/controls/ngp_bigmodel_frames.py` for the
layout and nagaspilot/docs/NAVPILOT_BIGMODEL.md.

Not exercised on a device: the OpenCL warp is not numerically identical to the warp the big models were
trained with, which needs a replay comparison before the phone model may drive.
"""
import numpy as np

from nagaspilot.controls.ngp_bigmodel_frames import OUTPUT_SHAPE, fill_output

CAMERA_NAMES = ('img', 'big_img')  # narrow, then wide: the order of the output's first axis


class NgpWarp:
  """start_buffers / wait / output, as RemoteModelState uses a warp."""

  def __init__(self, frames):
    self._frames = frames
    self._out = np.zeros(OUTPUT_SHAPE, dtype=np.uint8)
    # one flat byte view of the array the link sends from; valid once wait() returns
    self.output = memoryview(self._out).cast('B')

  def start_buffers(self, bufs, transforms) -> None:
    # the warp is synchronous: prepare() waits for the OpenCL queue before it returns
    for i, name in enumerate(CAMERA_NAMES):
      cl = self._frames[name].prepare(bufs[name], np.asarray(transforms[name], dtype=np.float32).flatten())
      fill_output(self._out, i, self._frames[name].buffer_from_cl(cl))

  def wait(self) -> None:
    pass


def reset_function(model):
  """The small model's state back to a start-of-drive zero, for a hand-back.

  v0.10.0's recurrent state is all host numpy (feature and desire history and the policy's inputs), so a
  reset is a fill; the OpenCL temporal image buffer is refreshed by every prepare(), also while the big
  model drives. Captured once before driving: nothing is allocated on the failure frame."""
  arrays = [model.full_features_buffer, model.full_desire, model.prev_desire, *model.numpy_inputs.values()]

  def reset() -> None:
    for a in arrays:
      a.fill(0)

  return reset

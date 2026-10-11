"""NGP10's camera path for the big model: the small model's own OpenCL warp.

Jetlink's comma side expects openpilot's compiled tinygrad warp. NGP10 (v0.10.0) warps with OpenCL
(`DrivingModelFrame`, selfdrive/modeld/models/commonmodel.cc), which the small model already runs every
frame. This implements Jetlink's optional `WarpSource` seam over those same frame objects: each frame the
big model needs is the newest frame of each camera, so there is no second warp and no extra GPU work. See
`nagaspilot/controls/ngp_bigmodel_frames.py` for the layout and nagaspilot/docs/JETLINK_BIGMODEL.md.

Not exercised on a device: the OpenCL warp is not numerically identical to the warp the big models were
trained with, which needs a replay comparison before the phone model may drive.
"""
import numpy as np

from nagaspilot.controls.ngp_bigmodel_frames import OUTPUT_SHAPE, fill_output

CAMERA_NAMES = ('img', 'big_img')  # narrow, then wide: the order of the output's first axis
# a comma 3 / 3X road camera and the model frame DrivingModelFrame warps to
DEVICE_GEOMETRY = (1928, 1208, 512, 256)


class NgpWarp:
  """start_buffers / wait / output, as joining and JetlinkModelState use a warp."""

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


class NgpWarpSource:
  """Jetlink's `warp_source` (interface.WarpSource) for NGP10."""

  def __init__(self, geometry=DEVICE_GEOMETRY):
    self._geometry = tuple(geometry)
    self._small = None

  def built(self) -> bool:
    return True  # OpenCL is always there; there is no pickle to build

  def geometry(self) -> tuple[int, int, int, int]:
    return self._geometry

  def prepare_reset(self, small):
    # join() calls this before make(), with the model it will warp for
    self._small = small
    return reset_function(small)

  def make(self, cam_w: int, cam_h: int, model_w: int, model_h: int, frame_size: int) -> NgpWarp:
    if self._small is None:
      raise RuntimeError("the small model was not given before the warp")
    if (model_w, model_h) != (DEVICE_GEOMETRY[2], DEVICE_GEOMETRY[3]):
      raise RuntimeError(f"the warp makes {DEVICE_GEOMETRY[2]}x{DEVICE_GEOMETRY[3]} frames, not {model_w}x{model_h}")
    return NgpWarp(self._small.frames)

"""The frame the big model is sent, taken from NGP10's own warp.

Pure layout helpers. NGP10's `DrivingModelFrame` (selfdrive/modeld/models/commonmodel.cc) warps a camera
frame to 512x256 and packs it as 6 planes of 128x256 (four Y phases, U, V): 196,608 bytes. It keeps two
of them back to back, the older one `temporal_skip` frames back and the newest second. Jetlink's server
keeps its own frame history and wants only the newest frame of each camera, narrow then wide, as a
(2, 6, 128, 256) uint8 array. So the frame to send is the second half of each camera's buffer, which costs
no GPU work beyond the warp the small model already runs.
"""
import numpy as np

PLANES, PLANE_H, PLANE_W = 6, 128, 256
FRAME_BYTES = PLANES * PLANE_H * PLANE_W  # 196,608 = 512 * 256 * 3 / 2
CAMERAS = 2
OUTPUT_SHAPE = (CAMERAS, PLANES, PLANE_H, PLANE_W)


def newest_frame(buffer: np.ndarray) -> np.ndarray:
  """The newest frame of a DrivingModelFrame buffer (older, newest): a (6, 128, 256) view."""
  flat = np.asarray(buffer, dtype=np.uint8).reshape(-1)
  if flat.size != 2 * FRAME_BYTES:
    raise ValueError(f"expected two {FRAME_BYTES} byte frames, got {flat.size} bytes")
  return flat[FRAME_BYTES:].reshape(PLANES, PLANE_H, PLANE_W)


def fill_output(out: np.ndarray, index: int, buffer: np.ndarray) -> None:
  """Copy a camera's newest frame into slot `index` (0 narrow, 1 wide) of `out`."""
  out[index] = newest_frame(buffer)

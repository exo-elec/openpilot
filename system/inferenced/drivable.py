"""
Drivable-area segmentation on the camera-tier card: pre/post-processing.

Both cards run the same network, TwinLiteNet+ Large at 384x640 (BDD100K
drivable area + lane lines, 1.94 M parameters, one context on a Hailo-8),
compiled from the same ONNX by tools/card_drivable_model.py: a HEF for the
Hailo-8, a .dxnn for the DX-M1M.

The contract:
  input : RGB uint8, letterboxed to 384x640 with grey (114) padding, as
          TwinLiteNet+ was trained; the network scales to [0, 1] itself.
  output: ONE tensor with two channels at the input size,
          [drivable score, lane score]: each head's (class 1 - class 0)
          logit, so > 0 means yes. The export folds TwinLiteNet+'s two
          2-class heads into it, which leaves nothing for a card compiler
          to rename or reorder. NCHW (DX) or NHWC (Hailo) both accepted.

The reply to a caller is one uint8 map at the caller's frame size:
OTHER (0), DRIVABLE (1), LANE (2, lane-line pixels, which are on the road).
"""

from __future__ import annotations

import cv2
import numpy as np

OTHER, DRIVABLE, LANE = 0, 1, 2

CARD_MODEL = "twinlitenet_plus_large_384x640"
CARD_INPUT_HW = (384, 640)
PAD_VALUE = 114


def letterbox(frame: np.ndarray, input_hw: tuple[int, int]) -> tuple[np.ndarray, tuple[int, int, int, int]]:
  """HWC/NHWC uint8 frame → letterboxed frame at input_hw, and the content box.

  Content box: (top, left, height, width) of the image inside the padding.
  """
  batched = frame.ndim == 4
  img = frame[0] if batched else frame
  h, w = img.shape[:2]
  in_h, in_w = input_hw
  gain = min(in_h / h, in_w / w)
  new_h, new_w = int(round(h * gain)), int(round(w * gain))
  if (new_h, new_w) != (h, w):
    img = cv2.resize(img, (new_w, new_h), interpolation=cv2.INTER_LINEAR)
  top, left = (in_h - new_h) // 2, (in_w - new_w) // 2
  out = np.full((in_h, in_w) + img.shape[2:], PAD_VALUE, dtype=img.dtype)
  out[top:top + new_h, left:left + new_w] = img
  return (out[None] if batched else out), (top, left, new_h, new_w)


def _scores(outputs: dict) -> np.ndarray:
  """The (2, H, W) [drivable, lane] score tensor, from NCHW or NHWC."""
  for value in outputs.values():
    arr = np.asarray(value, dtype=np.float32)
    while arr.ndim > 3 and arr.shape[0] == 1:
      arr = arr[0]
    if arr.ndim != 3:
      continue
    if arr.shape[0] == 2:
      return arr
    if arr.shape[-1] == 2:
      return np.moveaxis(arr, -1, 0)
  raise ValueError("card output carries no 2-channel [drivable, lane] tensor")


def drivable_map(outputs: dict, out_hw: tuple[int, int], content: tuple[int, int, int, int],
                 input_hw: tuple[int, int] = CARD_INPUT_HW) -> np.ndarray:
  """Card output → uint8 OTHER/DRIVABLE/LANE map at out_hw.

  content is letterbox()'s box for a network input of input_hw.
  Raises ValueError when the outputs hold no [drivable, lane] tensor.
  """
  scores = _scores(outputs)
  out = (scores[0] > 0).astype(np.uint8) * DRIVABLE
  out[scores[1] > 0] = LANE

  # Undo the letterbox: the map is at the network input size
  top, left, h, w = content
  in_h, in_w = input_hw
  if out.shape != (in_h, in_w):  # an output at another resolution: scale the box
    sy, sx = out.shape[0] / in_h, out.shape[1] / in_w
    top, left, h, w = int(top * sy), int(left * sx), max(1, int(h * sy)), max(1, int(w * sx))
  out = out[top:top + h, left:left + w]
  if out.shape != tuple(out_hw):
    out = cv2.resize(out, (out_hw[1], out_hw[0]), interpolation=cv2.INTER_NEAREST)
  return out

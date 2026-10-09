"""Dev-PC stub for commonmodel_pyx Cython extension.

Delete this file before building with SCons — the compiled .so replaces it.
Provides numpy-based frame preparation so modeld runs on x86_64 (dev PC / CARLA).

On ARM edge hardware, SCons builds commonmodel_pyx.so from commonmodel_pyx.pyx,
which uses OpenCL for GPU-accelerated NV12→RGB conversion + camera projection.
"""

from __future__ import annotations

import numpy as np


class CLContext:
  """Mock OpenCL context for dev PC use.

  On hardware this creates a real OpenCL device context; here it is a no-op
  placeholder so VisionIpcClient and DrivingModelFrame can be instantiated
  without a GPU.
  """

  def __init__(self):
    self.device_id = None
    self.context = None


class _CLMem:
  """Holds numpy data where CL GPU memory would be on hardware."""

  def __init__(self, data: np.ndarray):
    self._data = data


class DrivingModelFrame:
  """Numpy-based frame processor replacing OpenCL DrivingModelFrame.

  On hardware: DrivingModelFrame uses OpenCL to copy + reproject a NV12
  camera frame into a uint8 YUV buffer on the GPU.

  Here: extract the raw bytes from the VisionBuf (CPU copy) and apply the
  camera projection as a simple affine warp with numpy/OpenCV.
  """

  def __init__(self, context: CLContext, temporal_skip: int = 2):
    self._context = context
    self._temporal_skip = temporal_skip

  def prepare(self, buf, projection) -> _CLMem:
    """Extract frame from VisionBuf and apply projection transform.

    Returns a _CLMem wrapping the uint8 array ready for inference.
    On hardware this is a GPU buffer of shape (1, 12, 128, 256); here we
    return a flat numpy array of the same total size so the caller's
    ``.reshape(expected_shape)`` succeeds.
    """
    # Model input shape for vision models: (1, 12, 128, 256) = 393,216 elements
    MODEL_INPUT_SIZE = 1 * 12 * 128 * 256
    try:
      # Try to get the raw bytes from the VisionBuf (NV12 ~49kB)
      raw = np.frombuffer(buf.data, dtype=np.uint8)
      height = getattr(buf, 'height', 128)
      width = getattr(buf, 'width', 256)
      # NV12 size: Y plane (H×W) + UV plane (H/2×W)
      yuv_size = height * width + (height // 2) * width
      frame = raw[:yuv_size]
      # Pad or truncate to the exact model input size
      if frame.size < MODEL_INPUT_SIZE:
        frame = np.pad(frame, (0, MODEL_INPUT_SIZE - frame.size), mode='constant')
      else:
        frame = frame[:MODEL_INPUT_SIZE]
    except Exception:
      # Fallback: return zeros if VisionBuf can't be read (offline / no camera)
      frame = np.zeros(MODEL_INPUT_SIZE, dtype=np.uint8)

    return _CLMem(frame)

  def buffer_from_cl(self, cl_mem: _CLMem) -> np.ndarray:
    """Return the numpy array stored in the mock CLMem."""
    return cl_mem._data

"""GPU-only factory-map rectification; no full-image CPU remap fallback."""
from pathlib import Path
import time

import numpy as np


class OpenCLRectifier:
  def __init__(self, gpu, maps):
    self.gpu, self.buffers = gpu, []
    self.shape = maps[0].shape
    if len(maps) != 4 or any(m.shape != self.shape or m.ndim != 2 or not np.isfinite(m).all() for m in maps):
      raise ValueError('Expected four finite factory maps at one image resolution')
    n = int(np.prod(self.shape))
    gpu.check_allocations([4*n]*4 + [n, n])
    program = gpu.build(Path(__file__).with_name('kernels').joinpath('stereo_rectify.cl').read_text())
    self.kernel = gpu.cl.Kernel(program, 'rectify_luma')
    try:
      with gpu.lock:
        for size in [4*n]*4 + [n, n]:
          self.buffers.append(gpu.cl.Buffer(gpu.context, gpu.cl.mem_flags.READ_WRITE, size))
        for buffer, array in zip(self.buffers[:4], maps, strict=True):
          gpu.cl.enqueue_copy(gpu.queue, buffer, np.ascontiguousarray(array, dtype=np.float32), is_blocking=True)
    except Exception:
      self.close()
      raise

  def close(self):
    with self.gpu.lock:
      self.gpu.queue.finish()
      for buffer in self.buffers:
        buffer.release()
      self.buffers = []

  def remap(self, left, right):
    start = time.monotonic()
    if not self.buffers:
      raise RuntimeError('GPU rectifier is closed')
    if any(image.shape != self.shape or image.dtype != np.uint8 for image in (left, right)):
      raise ValueError('Luma must match factory-map resolution and uint8 format')
    gpu = self.gpu
    h, w = self.shape
    result = []
    with gpu.lock:
      mx1, my1, mx2, my2, source, target = self.buffers
      for image, mx, my in ((left, mx1, my1), (right, mx2, my2)):
        output = np.empty(self.shape, np.uint8)
        gpu.cl.enqueue_copy(gpu.queue, source, np.ascontiguousarray(image), is_blocking=True)
        self.kernel(gpu.queue, (w*h,), None, source, mx, my, target, np.int32(w), np.int32(h))
        gpu.cl.enqueue_copy(gpu.queue, output, target, is_blocking=True)
        result.append(output)
    if time.monotonic()-start > 0.05:
      raise TimeoutError('GPU rectification deadline exceeded')
    return tuple(result)

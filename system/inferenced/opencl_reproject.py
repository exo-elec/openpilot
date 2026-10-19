"""GPU projection matching the ROS/OpenCV complete-Q geometry contract.

Coordinates are rectified optical x-right/y-down/z-forward in meters. Invalid
samples are NaN. This is an OpenCL adaptation, not imported Autoware/CUDA code;
see docs/eop/STEREO_POINTCLOUD_UPSTREAM_CONTRACT.md for scope and source evidence.
"""
from pathlib import Path
import time

import numpy as np


class OpenCLReprojector:
  def __init__(self, gpu):
    self.gpu = gpu
    self._buffers = []
    self._shape = None
    self.closed = False
    program = gpu.build(Path(__file__).with_name('kernels').joinpath('stereo_reproject.cl').read_text())
    self._kernel = gpu.cl.Kernel(program, 'stereo_reproject')

  def _allocate(self, shape):
    if shape == self._shape:
      return
    n = int(np.prod(shape))
    sizes = [4*n, 4*n, 128, 12*n]
    self.gpu.check_allocations(sizes)
    self.gpu.queue.finish()
    for buffer in self._buffers:
      buffer.release()
    self._buffers = []
    self._shape = None
    try:
      for size in sizes:
        self._buffers.append(self.gpu.cl.Buffer(self.gpu.context, self.gpu.cl.mem_flags.READ_WRITE, size))
    except Exception:
      for buffer in self._buffers:
        buffer.release()
      self._buffers = []
      raise
    self._shape = shape

  def reproject(self, disparity, q, confidence, *, transform=None, min_confidence=0.3, min_depth=0.5, max_depth=100.0, max_runtime_ms=50.0):
    if self.closed:
      raise RuntimeError('GPU reprojector is closed')
    disparity = np.ascontiguousarray(disparity, dtype=np.float32)
    confidence = np.ascontiguousarray(confidence, dtype=np.float32)
    q = np.ascontiguousarray(q, dtype=np.float32)
    if disparity.ndim != 2 or not disparity.size or confidence.shape != disparity.shape:
      raise ValueError('Disparity and confidence must have the same nonempty image shape')
    if q.shape != (4, 4) or not np.isfinite(q).all() or np.linalg.matrix_rank(q) != 4:
      raise ValueError('Expected a finite full-rank 4x4 calibrated Q')
    transform = np.eye(4, dtype=np.float32) if transform is None else np.asarray(transform, dtype=np.float32)
    if (transform.shape != (4, 4) or not np.isfinite(transform).all() or
        not np.allclose(transform[3], [0, 0, 0, 1], atol=1e-5) or
        not np.allclose(transform[:3, :3] @ transform[:3, :3].T, np.eye(3), atol=1e-4) or
        not np.isclose(np.linalg.det(transform[:3, :3]), 1, atol=1e-4)):
      raise ValueError('Expected a finite rigid output-frame transform')
    geometry = np.ascontiguousarray(np.vstack((q, transform)), dtype=np.float32)
    limits = np.array([min_confidence, min_depth, max_depth, max_runtime_ms])
    if not np.isfinite(limits).all() or not (0 <= min_confidence <= 1 and 0 <= min_depth < max_depth and max_runtime_ms > 0):
      raise ValueError('Invalid confidence/range/deadline limits')
    h, w = disparity.shape
    self.gpu.check_allocations([4*w*h, 4*w*h, 128, 12*w*h])
    output = np.empty((h, w, 3), dtype=np.float32)
    start = time.monotonic()
    gpu = self.gpu
    with gpu.lock:
      self._allocate((h, w))
      db, cb, qb, xyz = self._buffers
      for buffer, array in ((db, disparity), (cb, confidence), (qb, geometry)):
        gpu.cl.enqueue_copy(gpu.queue, buffer, array, is_blocking=True)
      self._kernel(gpu.queue, (w*h,), None, db, cb, qb, xyz, np.int32(w), np.int32(h),
                   np.float32(min_confidence), np.float32(min_depth), np.float32(max_depth))
      gpu.cl.enqueue_copy(gpu.queue, output, xyz, is_blocking=True)
    elapsed = (time.monotonic() - start) * 1000
    if elapsed > max_runtime_ms:
      raise TimeoutError(f'GPU reprojection deadline exceeded: {elapsed:.1f} ms')
    return output

  def close(self):
    with self.gpu.lock:
      if self.closed:
        return
      self.closed = True
      try:
        self.gpu.queue.finish()
      finally:
        for buffer in self._buffers:
          buffer.release()
        self._buffers = []
        self._kernel = None
        self._shape = None

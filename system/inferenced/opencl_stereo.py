"""Bounded GPU four-path census SGM, using a HAL-owned OpenCL context.

All matching, aggregation, selection, filtering and LR validation run as GPU
kernels. Host arrays are only inputs/readback. No CPU matcher is substituted.
"""
from pathlib import Path
import time

import numpy as np


class OpenCLStereo:
  def __init__(self, gpu, config):
    self.gpu, self.config = gpu, config
    self._buffers = []
    self.closed = False
    w, h, d = config.target_width, config.target_height, config.max_disparity
    if not (isinstance(w, int) and isinstance(h, int) and isinstance(d, int) and w >= 8 and h >= 8 and d in (8, 16, 32, 64, 128)):
      raise ValueError('GPU stereo requires dimensions >=8 and disparity count 8/16/32/64/128')
    if config.min_disparity != 0 or config.use_8_path:
      raise ValueError('GPU stereo supports zero minimum disparity and four paths only')
    if not (0 < config.p1 < config.p2 <= 4096 and config.max_runtime_ms > 0):
      raise ValueError('Invalid SGM penalties or deadline')
    if min(int(gpu.device.max_work_group_size), int(gpu.device.max_work_item_sizes[0])) < d:
      raise ValueError('GPU work-group limit is below disparity count')
    n = w * h
    # Cost and aggregation volumes are reused for left and right passes.
    sizes = [n, n, n*4, n*4, n*d, n*d*2] + [n*4]*8
    self.working_set_bytes = gpu.check_allocations(sizes)
    self.program = gpu.build(Path(__file__).with_name('kernels').joinpath('stereo_sgm.cl').read_text())
    self.kernels = {name: gpu.cl.Kernel(self.program, name) for name in
                    ('census5', 'matching_cost', 'aggregate_path', 'select_disparity', 'median3', 'consistency')}
    kernel_limit = self.kernels['aggregate_path'].get_work_group_info(gpu.cl.kernel_work_group_info.WORK_GROUP_SIZE, gpu.device)
    if kernel_limit < d:
      raise ValueError('Compiled SGM kernel work-group limit is below disparity count')
    try:
      for size in sizes:
        self._buffers.append(gpu.cl.Buffer(gpu.context, gpu.cl.mem_flags.READ_WRITE, size))
    except Exception:
      self.close()
      raise
    (self.left, self.right, self.lcensus, self.rcensus, self.cost, self.aggregated,
     self.ldisp, self.rdisp, self.lconf, self.rconf, self.lmedian, self.rmedian,
     self.disparity, self.confidence) = self._buffers

  def _kernel(self, name, size, local, *args):
    return self.kernels[name](self.gpu.queue, size, local, *args)

  def compute(self, left, right):
    if self.closed:
      raise RuntimeError('GPU stereo is closed')
    cfg, gpu = self.config, self.gpu
    w, h, d = cfg.target_width, cfg.target_height, cfg.max_disparity
    for image in (left, right):
      if image.shape != (h, w) or image.dtype != np.uint8:
        raise ValueError('GPU stereo expects rectified uint8 grayscale images at the calibrated resolution')
    left, right = np.ascontiguousarray(left), np.ascontiguousarray(right)
    output = np.empty((h, w), np.float32)
    confidence = np.empty_like(output)
    integer = np.int32
    shape = (integer(w), integer(h))
    start = time.monotonic()
    with gpu.lock:
      # Blocking uploads also keep host memory alive until copies finish.
      gpu.cl.enqueue_copy(gpu.queue, self.left, left, is_blocking=True)
      gpu.cl.enqueue_copy(gpu.queue, self.right, right, is_blocking=True)
      for image, census in ((self.left, self.lcensus), (self.right, self.rcensus)):
        self._kernel('census5', (w*h,), None, image, census, *shape)
      for a, b, direction, disp, conf in ((self.lcensus, self.rcensus, -1, self.ldisp, self.lconf),
                                         (self.rcensus, self.lcensus, 1, self.rdisp, self.rconf)):
        self._kernel('matching_cost', (w*h*d,), None, a, b, self.cost, *shape, integer(d), integer(direction))
        for path in range(4):
          lines = h if path < 2 else w
          self._kernel('aggregate_path', (lines*d,), (d,), self.cost, self.aggregated, *shape,
                       integer(d), integer(cfg.p1), integer(cfg.p2), integer(path))
        self._kernel('select_disparity', (w*h,), None, self.aggregated, disp, conf, *shape,
                     integer(d), integer(direction), integer(cfg.enable_subpixel))
      if cfg.enable_median_filter:
        self._kernel('median3', (w*h,), None, self.ldisp, self.lmedian, *shape)
        self._kernel('median3', (w*h,), None, self.rdisp, self.rmedian, *shape)
      ldisp = self.lmedian if cfg.enable_median_filter else self.ldisp
      rdisp = self.rmedian if cfg.enable_median_filter else self.rdisp
      self._kernel('consistency', (w*h,), None, ldisp, rdisp, self.lconf, self.rconf,
                   self.disparity, self.confidence, *shape)
      gpu.cl.enqueue_copy(gpu.queue, output, self.disparity, is_blocking=True)
      gpu.cl.enqueue_copy(gpu.queue, confidence, self.confidence, is_blocking=True)
    elapsed = (time.monotonic() - start) * 1000
    if elapsed > cfg.max_runtime_ms:
      raise TimeoutError(f'GPU stereo deadline exceeded: {elapsed:.1f} > {cfg.max_runtime_ms:.1f} ms')
    if not np.isfinite(output).all() or not np.isfinite(confidence).all():
      raise RuntimeError('GPU returned non-finite stereo output')
    return output, confidence, elapsed

  def close(self):
    if self.closed:
      return
    self.closed = True
    try:
      self.gpu.queue.finish()
    finally:
      for buffer in self._buffers:
        buffer.release()
      self._buffers.clear()
      self.kernels = {}
      self.program = None

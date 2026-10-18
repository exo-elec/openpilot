"""Optional GPU-only OpenCL compute shared by NGP10 and EOP10.

Exopilot owns the BSP, driver and ICD installation. This module owns only a
process-local compute context. Importing it does not load a driver. Missing or
broken GPU support is an error; CPU OpenCL is never a fallback.
"""
from __future__ import annotations

import importlib
import threading


class GPUUnavailable(RuntimeError):
  """No usable GPU device, driver or kernel execution."""


class GPUCompute:
  """In-order GPU queue and bounded allocations for optional perception work.

  Callers serialize multi-kernel operations with ``lock``. A deadline can reject
  completed work but cannot preempt a hung driver; supervise the worker process.
  """

  def __init__(self, *, cl_module=None):
    self.lock = threading.Lock()
    self._closed = False
    self._programs = {}
    self.context = self.queue = self.device = None
    try:
      self.cl = cl_module if cl_module is not None else importlib.import_module('pyopencl')
      failures = []
      candidates = []
      for platform in self.cl.get_platforms():
        try:
          candidates.extend(platform.get_devices(device_type=self.cl.device_type.GPU))
        except self.cl.Error as exc:
          failures.append(str(exc))
      candidates.sort(key=lambda device: 'mali' not in device.name.lower())
      for device in candidates:
        # Defend against ICDs returning an unexpected device type.
        if not device.type & self.cl.device_type.GPU:
          continue
        try:
          self.context = self.cl.Context(devices=[device])
          self.queue = self.cl.CommandQueue(self.context, device=device, properties=0)
          self.device = device
          return
        except self.cl.Error as exc:
          failures.append(str(exc))
          self.context = self.queue = self.device = None
      raise GPUUnavailable('No usable OpenCL GPU device' + (': ' + '; '.join(failures) if failures else ''))
    except GPUUnavailable:
      raise
    except Exception as exc:
      raise GPUUnavailable(f'OpenCL GPU unavailable: {exc}') from exc

  def build(self, source: str):
    """Build portable OpenCL 1.2 kernels; never enable relaxed finite math."""
    if self._closed:
      raise GPUUnavailable('GPU context is closed')
    if source not in self._programs:
      try:
        self._programs[source] = self.cl.Program(self.context, source).build(options=['-cl-std=CL1.2'])
      except self.cl.Error as exc:
        raise GPUUnavailable(f'GPU kernel build failed: {exc}') from exc
    return self._programs[source]

  def check_allocations(self, sizes: list[int], *, budget_bytes: int = 128 * 1024 * 1024) -> int:
    """Bound total working memory and each allocation before touching the GPU."""
    if self._closed:
      raise GPUUnavailable('GPU context is closed')
    if not sizes or any(not isinstance(size, int) or size <= 0 for size in sizes):
      raise ValueError('GPU allocations must have positive integer sizes')
    if budget_bytes <= 0:
      raise ValueError('GPU memory budget must be positive')
    limit = min(budget_bytes, int(self.device.global_mem_size) // 4)
    total = sum(sizes)
    if total > limit or max(sizes) > int(self.device.max_mem_alloc_size):
      raise GPUUnavailable(f'GPU working set {total} exceeds device/allocation budget')
    return total

  def smoke_test(self) -> None:
    """Compile, execute and verify a small real kernel before claiming readiness."""
    import numpy as np

    with self.lock:
      program = self.build('__kernel void smoke(__global float *out) { int i=get_global_id(0); out[i]=(float)(i*i+1); }')
      output = np.empty(4, dtype=np.float32)
      buffer = self.cl.Buffer(self.context, self.cl.mem_flags.WRITE_ONLY, output.nbytes)
      try:
        kernel = self.cl.Kernel(program, 'smoke')
        kernel(self.queue, (4,), None, buffer)
        self.cl.enqueue_copy(self.queue, output, buffer, is_blocking=True)
        if not np.array_equal(output, np.array([1, 2, 5, 10], dtype=np.float32)):
          raise GPUUnavailable('GPU smoke kernel output mismatch')
      except self.cl.Error as exc:
        raise GPUUnavailable(f'GPU smoke kernel failed: {exc}') from exc
      finally:
        buffer.release()

  def close(self) -> None:
    """Drop owned objects after outstanding commands complete."""
    with self.lock:
      if not self._closed:
        self._closed = True
        try:
          self.queue.finish()
        finally:
          self._programs.clear()
          self.queue = self.context = self.device = None

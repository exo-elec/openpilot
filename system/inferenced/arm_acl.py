#!/usr/bin/env python3
"""Unified ARM Compute Library Backend - CPU (NEON) + GPU (Mali OpenCL) auto-select."""

from __future__ import annotations

import logging
from typing import Any

from openpilot.system.inferenced.compute import HardwareBackend, BackendType, InferenceResult, ModelConfig

logger = logging.getLogger(__name__)


class ACLBackend(HardwareBackend):
  """Unified ARM Compute Library backend - auto-selects GPU or CPU based on availability."""

  def __init__(self):
    super().__init__(BackendType.ACL)
    self._acl_available = False
    self._opencl_available = False
    self._use_gpu = False

  def initialize(self) -> bool:
    """Initialize unified ACL backend."""
    try:
      import ctypes

      # Check for ARM Compute Library (required)
      ctypes.CDLL('libarm_compute.so')
      self._acl_available = True

      # Check for OpenCL (GPU support)
      try:
        ctypes.CDLL('libOpenCL.so')
        self._opencl_available = True
        self._use_gpu = True
      except Exception:
        self._use_gpu = False

      self._initialized = True
      device = "GPU (Mali OpenCL)" if self._use_gpu else "CPU (ARM NEON)"
      logger.info(f"ACL backend initialized - using {device}")
      return True

    except Exception as e:
      logger.warning(f"ACL backend not available: {e}")
      return False

  def release(self) -> None:
    """Release ACL resources."""
    self._initialized = False

  def load_model(self, config: ModelConfig) -> bool:
    """Load model for ACL execution."""
    if not self._initialized:
      return False
    logger.debug(f"ACL prepared for model: {config.name} (device: {'GPU' if self._use_gpu else 'CPU'})")
    return True

  def infer(self, model_name: str, inputs: dict[str, Any]) -> InferenceResult:
    """Execute inference on GPU or CPU."""
    if not self._initialized:
      return InferenceResult(
          backend_type=self.backend_type,
          model_name=model_name,
          success=False,
          error_message="ACL backend not initialized"
      )

    try:
      import time

      start_time = time.monotonic()

      use_gpu = self._should_use_gpu(model_name, inputs)
      if use_gpu:
        output = self._gpu_compute(model_name, inputs)
      else:
        output = self._cpu_compute(model_name, inputs)

      inference_time_ms = (time.monotonic() - start_time) * 1000
      self._stats.tasks_completed += 1
      self._stats.total_exec_time_ms += inference_time_ms

      outputs = output if isinstance(output, dict) else {'output': output}
      return InferenceResult(
          backend_type=self.backend_type,
          model_name=model_name,
          outputs=outputs,
          inference_time_ms=inference_time_ms,
          success=True
      )

    except Exception as e:
      logger.exception("ACL inference error")
      self._stats.tasks_failed += 1
      return InferenceResult(
          backend_type=self.backend_type,
          model_name=model_name,
          success=False,
          error_message=str(e)
      )

  def _gpu_compute(self, model_name: str, inputs: dict[str, Any]) -> Any:
    """GPU compute operations via Mali OpenCL."""
    import numpy as np

    if model_name == 'sgm_stereo':
      # SGM (Semi-global matching) for stereo depth
      logger.debug("ACL GPU: SGM stereo depth computation")
      return np.zeros((inputs.get('height', 480), inputs.get('width', 640)), dtype=np.float32)

    elif model_name == 'gemm':
      # Matrix multiply
      a = inputs.get('a')
      b = inputs.get('b')
      if a is not None and b is not None:
        return np.matmul(np.array(a), np.array(b))

    elif model_name == 'convolution':
      # Convolution operation
      logger.debug("ACL GPU: Convolution")
      return inputs.get('input')

    elif model_name == 'radar_cfar':
      # BGT60TR13C 2-D CA-CFAR offloaded to Mali GPU. Currently unused by
      # radar4d.py (moved to 4x ESP32_RADAR corner nodes doing their own
      # onboard CFAR over WiFi/UDP, see docs/eop/bgt60_radar.md) — kept here
      # since it's shared inferenced infrastructure, not radar4d-owned.
      logger.debug("ACL GPU: radar 2-D CA-CFAR")
      from hal.drivers.radar import dsp_gpu_kernel
      kernel = dsp_gpu_kernel.get_kernel()
      d_idx, r_idx, snr_db = kernel.run(
          power=np.array(inputs['power']),
          guard_cells=(int(inputs['guard_cells'][0]), int(inputs['guard_cells'][1])),
          training_cells=(int(inputs['training_cells'][0]), int(inputs['training_cells'][1])),
          pfa=float(inputs['pfa']),
          max_hits=int(inputs['max_hits']),
      )
      return {
          'doppler_idx': d_idx,
          'range_idx': r_idx,
          'snr_db': snr_db,
      }

    # Fallback
    return inputs.get('input')

  def _should_use_gpu(self, model_name: str, inputs: dict[str, Any]) -> bool:
    """Decide if operation should use GPU or CPU based on model and input characteristics."""
    if not self._opencl_available:
      return False

    # GPU-assigned operations (always use GPU if available)
    if model_name in ('sgm_stereo', 'gemm', 'radar_cfar'):
      return True

    # For other ops, estimate workload - GPU beneficial for large inputs
    input_size = 0
    for val in inputs.values():
      try:
        if isinstance(val, (list, tuple)):
          input_size += len(val)
        elif hasattr(val, '__len__'):
          input_size += len(val)
      except Exception:
        pass

    # Threshold: use GPU for operations with >1000 elements
    return input_size > 1000

  def _cpu_compute(self, model_name: str, inputs: dict[str, Any]) -> Any:
    """CPU compute operations via ARM NEON."""
    import numpy as np

    logger.debug(f"ACL CPU: {model_name}")

    # Placeholder - actual ACL NEON operations would go here
    if 'input' in inputs:
      return np.array(inputs['input'])
    return None

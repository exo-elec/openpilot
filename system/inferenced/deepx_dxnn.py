#!/usr/bin/env python3
"""DEEPX Backend — DEEPX DX-M1 PCIe AI accelerator for camera/ADAS inference.

Camera inference tier alongside Hailo-8. DX-M1 (20 TOPS) replaces retired Hailo-8.
Uses dx_engine (DXRT) runtime with .dxnn model format compiled by DXCOM.

Detection:
  1. lspci -d 1ff4: (vendor 1ff4:0000 = DX_M1)
  2. /dev/dxrt0 device node (requires deepx PCIe driver + udev rule)

Typical lifecycle::

  backend = DeepXBackend()
  backend.initialize()               # probe hardware, import dx_engine
  backend.load_model(config)         # open InferenceEngine session
  result = backend.infer(name, inputs)
  backend.release()                  # close all sessions
"""

from __future__ import annotations

import logging
import os
import subprocess
import sys
import time
from pathlib import Path
from typing import Any

import numpy as np

from openpilot.system.inferenced.compute import HardwareBackend, BackendType, InferenceResult, ModelConfig

logger = logging.getLogger(__name__)


def _detect_deepx() -> bool:
  """Return True if a DEEPX DX-M1 PCIe accelerator is present."""
  try:
    result = subprocess.run(
      ['lspci', '-d', '1ff4:'],
      capture_output=True, text=True, timeout=5
    )
    if '1ff4' in result.stdout:
      return True
  except (FileNotFoundError, subprocess.TimeoutExpired):
    pass
  return os.path.exists('/dev/dxrt0') or os.path.exists('/dev/dxrt1')


class DeepXBackend(HardwareBackend):
  """DEEPX DX-M1 PCIe AI accelerator backend — camera inference tier.

  Workload: camera_inference (PCIe, no device memory, interchangeable with Hailo-8).
  Model zoo: models/deepx/  (.dxnn format, compiled with DXCOM)

  Each loaded model owns its own InferenceEngine session.
  The engine is opened in load_model() and closed in unload_model()/release().
  """

  WORKLOAD_CLASS = 'camera_inference'
  HAS_DEVICE_MEMORY = False  # Host RAM via PCIe DMA (no onboard DRAM)
  MODEL_ZOO_SUBDIR = 'deepx'

  def __init__(self):
    super().__init__(BackendType.DX_M1)
    self._dx = None               # dx_engine module
    self._engines: dict[str, Any] = {}   # model_name → InferenceEngine

  # ---------------------------------------------------------------------------
  # Lifecycle
  # ---------------------------------------------------------------------------

  def initialize(self) -> bool:
    """Probe DEEPX hardware and import dx_engine runtime."""
    if not _detect_deepx():
      logger.debug("DEEPX DX-M1 not detected — skipping backend")
      return False
    try:
      self._setup_paths()
      import dx_engine  # type: ignore
      self._dx = dx_engine
      self._initialized = True
      logger.info("DEEPX DX-M1 backend initialized")
      return True
    except ImportError:
      logger.warning("dx_engine not installed — DEEPX backend unavailable")
      return False
    except Exception as e:
      logger.error(f"DEEPX initialization error: {e}")
      return False

  def release(self) -> None:
    """Close all InferenceEngine sessions."""
    for name in list(self._engines.keys()):
      self.unload_model(name)
    self._initialized = False
    logger.info("DEEPX backend released")

  # ---------------------------------------------------------------------------
  # Model management
  # ---------------------------------------------------------------------------

  def load_model(self, config: ModelConfig) -> bool:
    """Load a .dxnn model and open its InferenceEngine session."""
    if not self._initialized:
      return False
    if config.name in self._engines:
      return True
    if not os.path.exists(config.path):
      logger.error(f"DEEPX model not found: {config.path}")
      return False
    try:
      engine = self._dx.InferenceEngine(config.path)
      engine.__enter__()
      self._engines[config.name] = engine
      config.loaded = True
      logger.info(f"Loaded DEEPX model: {config.name} from {config.path}")
      return True
    except Exception as e:
      logger.error(f"Failed to load DEEPX model {config.name}: {e}")
      return False

  def unload_model(self, name: str) -> bool:
    engine = self._engines.pop(name, None)
    if engine is None:
      return False
    try:
      engine.__exit__(None, None, None)
    except Exception as e:
      logger.warning(f"Error closing DEEPX engine for {name}: {e}")
    return True

  # ---------------------------------------------------------------------------
  # Inference
  # ---------------------------------------------------------------------------

  def infer(self, model_name: str, inputs: dict[str, Any]) -> InferenceResult:
    """Run synchronous inference on the DX-M1 NPU.

    dx_engine.run() takes [np.ndarray(uint8), ...] and returns a list.
    We convert the named-dict inputs → ordered list of uint8 buffers,
    and return outputs keyed as 'output_0', 'output_1', etc.
    """
    if not self._initialized:
      return InferenceResult(
        backend_type=BackendType.DX_M1, model_name=model_name,
        success=False, error_message="DEEPX not initialized"
      )
    engine = self._engines.get(model_name)
    if engine is None:
      return InferenceResult(
        backend_type=BackendType.DX_M1, model_name=model_name,
        success=False, error_message=f"Model not loaded: {model_name}"
      )

    t0 = time.monotonic()
    try:
      # Build input list — use np.empty+fill to force unique physical pages
      # so the PCIe DMA driver doesn't hit EFAULT from COW zero pages.
      input_list = []
      for arr in inputs.values():
        arr = arr if isinstance(arr, np.ndarray) else np.array(arr)
        buf = np.empty(arr.size * arr.itemsize, dtype=np.uint8)
        buf[:] = arr.view(np.uint8).ravel()
        input_list.append(buf)

      raw_outputs = engine.run(input_list)
      outputs = {f"output_{i}": out for i, out in enumerate(raw_outputs)}
      inference_time_ms = (time.monotonic() - t0) * 1000.0

      self._stats.tasks_completed += 1
      self._stats.total_exec_time_ms += inference_time_ms

      return InferenceResult(
        backend_type=BackendType.DX_M1,
        model_name=model_name,
        outputs=outputs,
        inference_time_ms=inference_time_ms,
        success=True
      )
    except Exception as e:
      logger.error(f"DEEPX inference error on {model_name}: {e}")
      self._stats.tasks_failed += 1
      return InferenceResult(
        backend_type=BackendType.DX_M1, model_name=model_name,
        success=False, error_message=str(e)
      )

  # ---------------------------------------------------------------------------
  # Internal helpers
  # ---------------------------------------------------------------------------

  def _setup_paths(self) -> None:
    """Add dx_engine to sys.path when installed under third_party."""
    op_root = Path(__file__).parent.parent.parent
    candidates = [
      op_root / 'third_party' / 'dx_rt' / 'python',
      op_root / 'third_party' / 'dx_rt' / 'lib' / 'python3.10' / 'site-packages',
      op_root / 'third_party' / 'dx_rt' / 'lib' / 'python3.11' / 'site-packages',
    ]
    for p in candidates:
      if p.exists() and str(p) not in sys.path:
        sys.path.insert(0, str(p))

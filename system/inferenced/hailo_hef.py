#!/usr/bin/env python3
"""Hailo Backend — Hailo-8 edge AI processor for neural network inference.

Supports Hailo-8 (26 TOPS) for camera/ADAS inference in OpenPilot.
Interchangeable with DEEPX DX-M1 for the camera inference tier.

SDK: hailo_platform (HailoRT v5.3.0, hailo8 branch)
Model format: .hef
"""

from __future__ import annotations

import logging
import subprocess
import time
from typing import Any

import numpy as np

from openpilot.system.inferenced.compute import HardwareBackend, BackendType, InferenceResult, ModelConfig

logger = logging.getLogger(__name__)


def _detect_hailo_8() -> bool:
  """Return True if a Hailo-8 device is present."""
  try:
    result = subprocess.run(
      ['hailortcli', 'fw-control', 'identify'],
      capture_output=True, text=True, timeout=10
    )
    output = result.stdout.lower()
    if 'hailo-8' in output or 'hailo8' in output:
      return True
  except (FileNotFoundError, subprocess.TimeoutExpired):
    pass
  return __import__('os').path.exists('/dev/hailo0')


class Hailo8Backend(HardwareBackend):
  """Hailo-8 edge AI processor backend — camera inference tier.

  Workload: camera_inference (PCIe, no device memory, interchangeable with DX-M1).
  Model zoo: models/hef/  (.hef format)

  Uses HailoRT v5.3.0 new API:
    VDevice → create_infer_model(path) → InferModel.configure() → ConfiguredInferModel

  Lifecycle::

    backend = Hailo8Backend()
    backend.initialize()                # creates VDevice
    backend.load_model(config)          # loads HEF + creates ConfiguredInferModel
    result = backend.infer(name, inputs)
    backend.release()                   # frees device + models
  """

  WORKLOAD_CLASS = 'camera_inference'
  HAS_DEVICE_MEMORY = False  # Host RAM via PCIe DMA
  MODEL_ZOO_SUBDIR = 'hef'

  def __init__(self):
    super().__init__(BackendType.HAILO_8)
    self._vdevice: Any = None
    # model_name → (InferModel, ConfiguredInferModel)
    self._models: dict[str, tuple[Any, Any]] = {}

  # -------------------------------------------------------------------------
  # Lifecycle
  # -------------------------------------------------------------------------

  def initialize(self) -> bool:
    """Create the Hailo-8 virtual device."""
    if not _detect_hailo_8():
      logger.debug("Hailo-8 not detected — skipping Hailo backend")
      return False
    try:
      from hailo_platform import VDevice
      self._vdevice = VDevice()
      self._initialized = True
      logger.info("Hailo-8 backend initialized")
      return True
    except ImportError:
      logger.warning("hailo_platform not available — Hailo backend unavailable")
      return False
    except Exception as e:
      logger.error(f"Hailo initialization failed: {e}")
      return False

  def release(self) -> None:
    """Release Hailo resources."""
    for name in list(self._models.keys()):
      self.unload_model(name)
    if self._vdevice is not None:
      try:
        self._vdevice.release()
      except Exception as e:
        logger.debug(f"Hailo VDevice release failed: {e}")
      self._vdevice = None
    self._initialized = False

  # -------------------------------------------------------------------------
  # Model management
  # -------------------------------------------------------------------------

  def load_model(self, config: ModelConfig) -> bool:
    """Load a HEF model and configure it for inference.

    Uses HailoRT v5.3.0 API: create_infer_model(path) → configure()
    """
    if not self._initialized or self._vdevice is None:
      return False

    try:
      from hailo_platform import FormatType

      infer_model = self._vdevice.create_infer_model(str(config.path))
      infer_model.set_batch_size(1)
      infer_model.input().set_format_type(FormatType.FLOAT32)
      for out in infer_model.outputs:
        out.set_format_type(FormatType.FLOAT32)

      configured = infer_model.configure()

      self._models[config.name] = (infer_model, configured)
      config.loaded = True
      logger.info(f"Hailo model loaded: {config.name} from {config.path}")
      return True

    except Exception as e:
      logger.error(f"Error loading Hailo model {config.name}: {e}")
      return False

  def unload_model(self, name: str) -> bool:
    entry = self._models.pop(name, None)
    if entry is None:
      return False
    _, configured = entry
    try:
      configured.__exit__(None, None, None)
    except Exception as e:
      logger.debug(f"Hailo model release error for {name}: {e}")
    return True

  # -------------------------------------------------------------------------
  # Inference
  # -------------------------------------------------------------------------

  def infer(self, model_name: str, inputs: dict[str, Any]) -> InferenceResult:
    """Execute Hailo inference using ConfiguredInferModel.run([bindings], timeout_ms)."""
    if not self._initialized or self._vdevice is None:
      return InferenceResult(
          backend_type=self.backend_type,
          model_name=model_name,
          success=False,
          error_message="Hailo not initialized"
      )

    if model_name not in self._models:
      return InferenceResult(
          backend_type=self.backend_type,
          model_name=model_name,
          success=False,
          error_message=f"Model not loaded: {model_name}"
      )

    infer_model, configured_model = self._models[model_name]

    try:
      t0 = time.monotonic()

      output_buffers = {
        name: np.empty(infer_model.output(name).shape, dtype=np.float32)
        for name in infer_model.output_names
      }
      bindings = configured_model.create_bindings(output_buffers=output_buffers)

      input_names = infer_model.input_names
      for i, (key, val) in enumerate(inputs.items()):
        arr = val if isinstance(val, np.ndarray) else np.array(val, dtype=np.float32)
        arr = np.ascontiguousarray(arr, dtype=np.float32)
        input_name = key if key in input_names else input_names[i]
        bindings.input(input_name).set_buffer(arr)

      configured_model.run([bindings], 5000)

      outputs = {name: bindings.output(name).get_buffer() for name in infer_model.output_names}

      inference_time_ms = (time.monotonic() - t0) * 1000.0
      self._stats.tasks_completed += 1
      self._stats.total_exec_time_ms += inference_time_ms

      return InferenceResult(
          backend_type=self.backend_type,
          model_name=model_name,
          outputs=outputs,
          inference_time_ms=inference_time_ms,
          success=True
      )

    except Exception as e:
      logger.error(f"Hailo inference error on {model_name}: {e}")
      self._stats.tasks_failed += 1
      return InferenceResult(
          backend_type=self.backend_type,
          model_name=model_name,
          success=False,
          error_message=str(e)
      )

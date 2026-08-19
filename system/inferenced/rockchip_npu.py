#!/usr/bin/env python3
"""RKNN NPU Backend - Rockchip RKNN Lite runtime (RK3588).

Works on both edge hardware (with real RKNNLite) and dev PC (with mocks).
"""

from __future__ import annotations

import logging
from typing import Any
from pathlib import Path

from openpilot.system.inferenced.compute import HardwareBackend, BackendType, InferenceResult, ModelConfig

logger = logging.getLogger(__name__)

# Minimum rknpu driver version. Below this, fp16 inference on RK3588/RK3576 has
# observed driver bugs; warn loudly rather than silently producing bad output.
MIN_RKNPU_DRIVER_VERSION = "0.9.6"

# Models whose exported RKNN graph expects float16 inputs despite the ONNX
# metadata declaring uint8. Scoped deliberately: other models on this backend
# (sceneseg, ppliteseg, ...) have unverified quantization and must keep
# RKNNLite's uint8 default.
# NOTE: This set is deployment-specific and must be kept in sync with the
# locally-converted RKNN models; do not copy generic defaults from other forks.
_FP16_MODELS = frozenset({"driving_vision", "driving_policy"})


class RKNNBackend(HardwareBackend):
  """Rockchip RKNN Lite backend for NPU inference."""

  WORKLOAD_CLASS = 'safety_inference'
  HAS_DEVICE_MEMORY = False  # shared SoC memory, not discrete device DRAM

  def __init__(self):
    super().__init__(BackendType.NPU)
    self._rknn = None
    self._use_mock = False
    self._loaded_models: dict[str, Any] = {}

  def _check_driver_version(self) -> None:
    try:
      version = self._rknn.get_sdk_version()
      logger.info(f"rknpu driver version: {version}")
      if version and str(version) < MIN_RKNPU_DRIVER_VERSION:
        logger.warning(f"rknpu driver {version} is older than the minimum tested version {MIN_RKNPU_DRIVER_VERSION} — fp16 inference may be unreliable")
    except Exception:
      logger.exception("Could not query RKNN driver version")

  def initialize(self) -> bool:
    """Initialize RKNN backend."""
    try:
      # On-device runtime: RKNNLite (rknnlite package)
      try:
        from rknnlite.api import RKNNLite
        self._rknn = RKNNLite(verbose=False)
        self._use_mock = False
        self._initialized = True
        self._check_driver_version()
        logger.info("RKNN backend initialized (RKNNLite, real hardware)")
        return True
      except ImportError:
        # Dev PC mode — RKNNLite not installed; use mock for testing
        logger.info("RKNNLite not available — using mock mode for dev testing")
        self._use_mock = True
        self._initialized = True
        return True

    except Exception:
      logger.exception("RKNN initialization failed")
      return False

  def release(self) -> None:
    """Release RKNN resources."""
    try:
      if self._rknn is not None and not self._use_mock:
        for rknn in self._loaded_models.values():
          try:
            rknn.release()
          except Exception:
            logger.exception("Error releasing model")
        try:
          self._rknn.release()
        except Exception:
          logger.exception("Error releasing shared RKNN instance")
      self._loaded_models.clear()
    except Exception:
      logger.exception("Error releasing RKNN")
    self._initialized = False
    self._rknn = None

  def load_model(self, config: ModelConfig) -> bool:
    """Load RKNN model file."""
    if not self._initialized:
      return False

    try:
      if self._use_mock:
        # Dev PC: store path and mark as loaded
        self._loaded_models[config.name] = {'path': config.path, 'mock': True}
        config.loaded = True
        logger.debug(f"Mock loaded RKNN model: {config.name}")
        return True

      # Real hardware: load actual RKNN model via on-device RKNNLite
      from rknnlite.api import RKNNLite
      model_path = Path(config.path)

      if not model_path.exists():
        logger.error(f"Model file not found: {config.path}")
        return False

      rknn = RKNNLite(verbose=False)
      rknn.load_rknn(str(model_path))

      # Initialize runtime with platform-aware core mask
      core_mask = config.npu_cores if config.npu_cores > 0 else 0  # 0 = auto
      try:
        rknn.init_runtime(core_mask=core_mask)
      except TypeError:
        # Older RKNN API without core_mask parameter
        rknn.init_runtime()

      self._loaded_models[config.name] = rknn
      config.loaded = True
      logger.info(f"Loaded RKNN model: {config.name} (cores: {core_mask:#x})")
      return True

    except Exception:
      logger.exception("Error loading RKNN model")
      return False

  def infer(self, model_name: str, inputs: dict[str, Any]) -> InferenceResult:
    """Execute RKNN inference."""
    if not self._initialized:
      return InferenceResult(
          backend_type=self.backend_type,
          model_name=model_name,
          success=False,
          error_message="RKNN backend not initialized"
      )

    if model_name not in self._loaded_models:
      return InferenceResult(
          backend_type=self.backend_type,
          model_name=model_name,
          success=False,
          error_message=f"Model not loaded: {model_name}"
      )

    try:
      import time
      import numpy as np

      start_time = time.monotonic()

      output_dict: dict[str, Any]
      if self._use_mock:
        # Dev PC: return mock outputs matching typical model output shapes
        output_dict = {}
        # Return typical model outputs (batch of predictions/features)
        # For vision models: (batch, features) or (batch, height, width, channels)
        # For policy models: (batch, actions)
        input_shapes = list(inputs.values())
        if input_shapes and isinstance(input_shapes[0], np.ndarray):
          input_shape = input_shapes[0].shape
          batch_size = input_shape[0] if len(input_shape) > 0 else 1
          # Return output with reduced spatial dimensions (typical NN behavior)
          output_dict['output'] = np.random.randn(batch_size, 256).astype(np.float32)
        else:
          output_dict['output'] = np.random.randn(1, 256).astype(np.float32)
      else:
        # Real hardware: run actual RKNN inference
        rknn = self._loaded_models[model_name]

        # Preserve dict insertion order — callers must supply keys in model-compiled slot order
        rknn_inputs = []
        for val in inputs.values():
          if not isinstance(val, np.ndarray):
            val = np.array(val)
          rknn_inputs.append(val)

        # Run inference on NPU
        if model_name in _FP16_MODELS:
          rknn_inputs = [arr.astype(np.float16) for arr in rknn_inputs]
          outputs = rknn.inference(rknn_inputs, data_type="float16")
        else:
          outputs = rknn.inference(rknn_inputs)

        # Convert outputs to dict; use 'outputs' (list) for multi-output, 'output' for single
        output_dict = {}
        if isinstance(outputs, (list, tuple)):
          output_dict['outputs'] = [np.array(o) if not isinstance(o, np.ndarray) else o for o in outputs]
        else:
          output_dict['output'] = np.array(outputs) if not isinstance(outputs, np.ndarray) else outputs

      inference_time_ms = (time.monotonic() - start_time) * 1000
      self._stats.tasks_completed += 1
      self._stats.total_exec_time_ms += inference_time_ms

      return InferenceResult(
          backend_type=self.backend_type,
          model_name=model_name,
          outputs=output_dict,
          inference_time_ms=inference_time_ms,
          success=True
      )

    except Exception as e:
      logger.exception("RKNN inference error")
      self._stats.tasks_failed += 1
      return InferenceResult(
          backend_type=self.backend_type,
          model_name=model_name,
          success=False,
          error_message=str(e)
      )

#!/usr/bin/env python3
"""ONNX Runtime Backend — x86_64 dev PC and CPU fallback.

Loads .onnx model files via onnxruntime. Used automatically when RKNN is
not available (dev PC / x86_64). On ARM edge hardware, RKNN takes priority.

Execution providers tried in order: CUDA → CoreML → CPU.
"""

from __future__ import annotations

import logging
import time
from pathlib import Path
from typing import Any

import numpy as np

from openpilot.system.inferenced.compute import (
    HardwareBackend, BackendType, InferenceResult, ModelConfig
)

logger = logging.getLogger(__name__)

# Search paths for ONNX models (in priority order)
# __file__ = <repo>/openpilot/system/inferenced/onnx_backend.py
# parents[3] = <repo root>
_REPO_ROOT = Path(__file__).parents[3]
_ONNX_SEARCH_PATHS = [
    Path("/data/openpilot/models/onnx"),
    _REPO_ROOT / "models" / "onnx",
    _REPO_ROOT / "openpilot" / "models" / "onnx",
]


def _find_model(model_name: str) -> Path | None:
  """Find an ONNX model file by name."""
  for base in _ONNX_SEARCH_PATHS:
    for ext in (".onnx",):
      p = base / f"{model_name}{ext}"
      if p.exists():
        return p
  return None


class OnnxBackend(HardwareBackend):
  """ONNX Runtime inference backend for dev PC and CPU fallback."""

  def __init__(self):
    super().__init__(BackendType.ONNX)
    self._sessions: dict[str, Any] = {}
    self._providers: list[str] = []

  def initialize(self) -> bool:
    """Initialize ONNX Runtime."""
    try:
      import onnxruntime as ort

      # Prefer GPU providers, fall back to CPU
      available = ort.get_available_providers()
      for provider in ("CUDAExecutionProvider", "CoreMLExecutionProvider", "CPUExecutionProvider"):
        if provider in available:
          self._providers.append(provider)
      if not self._providers:
        self._providers = ["CPUExecutionProvider"]

      self._initialized = True
      logger.info(f"ONNX backend initialized (providers: {self._providers})")
      return True

    except ImportError:
      logger.warning("onnxruntime not installed — ONNX backend unavailable")
      return False
    except Exception as e:
      logger.error(f"ONNX initialization failed: {e}")
      return False

  def release(self) -> None:
    """Release all sessions."""
    self._sessions.clear()
    self._initialized = False

  def load_model(self, config: ModelConfig) -> bool:
    """Load an ONNX model."""
    if not self._initialized:
      return False

    try:
      import onnxruntime as ort

      # Resolve path: use config.path if set, otherwise search by name
      path = Path(config.path) if config.path else _find_model(config.name)
      if path is None or not path.exists():
        logger.error(f"ONNX model not found: {config.name} (searched {_ONNX_SEARCH_PATHS})")
        return False

      sess_opts = ort.SessionOptions()
      sess_opts.log_severity_level = 3  # suppress verbose ONNX logs
      session = ort.InferenceSession(str(path), sess_options=sess_opts, providers=self._providers)

      self._sessions[config.name] = session
      config.loaded = True
      logger.info(f"Loaded ONNX model: {config.name} ({path.stat().st_size // 1024 // 1024} MB)")
      return True

    except Exception as e:
      logger.error(f"Failed to load ONNX model '{config.name}': {e}")
      return False

  def infer(self, model_name: str, inputs: dict[str, Any]) -> InferenceResult:
    """Run inference via ONNX Runtime."""
    if not self._initialized:
      return InferenceResult(
          backend_type=self.backend_type,
          model_name=model_name,
          success=False,
          error_message="ONNX backend not initialized",
      )

    session = self._sessions.get(model_name)
    if session is None:
      return InferenceResult(
          backend_type=self.backend_type,
          model_name=model_name,
          success=False,
          error_message=f"Model not loaded: {model_name}",
      )

    try:
      start = time.monotonic()

      # Cast inputs to the dtype each input node expects
      ort_inputs: dict[str, np.ndarray] = {}
      node_map = {n.name: n for n in session.get_inputs()}
      expected_keys = set(node_map.keys())
      provided_keys = set(inputs.keys())
      missing = expected_keys - provided_keys
      unexpected = provided_keys - expected_keys
      if missing:
        raise ValueError(f"ONNX model '{model_name}' missing required inputs: {sorted(missing)}")
      if unexpected:
        logger.warning(f"ONNX model '{model_name}' received unexpected inputs (ignored): {sorted(unexpected)}")
      for key, val in inputs.items():
        if key not in node_map:
          continue
        expected_type = node_map[key].type  # e.g. "tensor(uint8)", "tensor(float16)"
        arr = np.asarray(val)
        if "uint8" in expected_type:
          arr = arr.astype(np.uint8)
        elif "float16" in expected_type:
          arr = arr.astype(np.float16)
        elif "float" in expected_type:
          arr = arr.astype(np.float32)
        ort_inputs[key] = arr

      raw_outputs = session.run(None, ort_inputs)
      output_names = [o.name for o in session.get_outputs()]
      outputs = {name: np.asarray(out) for name, out in zip(output_names, raw_outputs)}

      inference_time_ms = (time.monotonic() - start) * 1000
      self._stats.tasks_completed += 1
      self._stats.total_exec_time_ms += inference_time_ms

      return InferenceResult(
          backend_type=self.backend_type,
          model_name=model_name,
          outputs=outputs,
          inference_time_ms=inference_time_ms,
          success=True,
      )

    except Exception as e:
      self._stats.tasks_failed += 1
      logger.error(f"ONNX inference error ({model_name}): {e}")
      return InferenceResult(
          backend_type=self.backend_type,
          model_name=model_name,
          success=False,
          error_message=str(e),
      )

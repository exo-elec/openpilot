#!/usr/bin/env python3
"""
RKNN model runner using centralized HAL.

All NPU access goes through InferenceClient - no direct RKNNLite usage.
"""

from pathlib import Path
from typing import Any, cast
import numpy as np

# Centralized HAL - ONLY way to access NPU
from openpilot.system.inferenced import InferenceClient, HardwareBackend


class RKNNRunner:
  """RKNN model runner using HAL.

  This class provides a high-level interface for running RKNN models
  through the centralized HAL, abstracting away hardware details.
  """

  _last_async_outputs: dict[str, np.ndarray | None] | None = None

  def __init__(
    self,
    model_path: str | Path,
    input_shapes: dict[str, tuple | None] = None,
    output_shapes: dict[str, tuple | None] = None,
    use_npu_cores: str = "all",
    verbose: bool = False,
  ):
    """Initialize RKNN runner via HAL.

    Args:
        model_path: Path to .rknn model file
        input_shapes: Optional dict of input names to shapes
        output_shapes: Optional dict of output names to shapes
        use_npu_cores: NPU cores to use ('all', '0', '1', '2', '0,1')
        verbose: Enable verbose output
    """
    self.model_path = Path(model_path)
    self.input_shapes = input_shapes or {}
    self.output_shapes = output_shapes or {}
    self.verbose = verbose

    if not self.model_path.exists():
      raise FileNotFoundError(f"RKNN model not found: {self.model_path}")

    # Create HAL client - ONLY way to access NPU
    self.client = InferenceClient("rknn_runner")

    # Get NPU backend
    try:
      self.npu: HardwareBackend = self.client.npu()
    except RuntimeError as e:
      raise RuntimeError(f"NPU not available via HAL: {e}") from e

    # Load model
    self._load_model(use_npu_cores)

    # Get model info from HAL
    self._query_model_info()

    if self.verbose:
      print(f"✅ RKNN model loaded via HAL: {self.model_path.name}")
      print(f"   Inputs: {self.input_shapes}")
      print(f"   Outputs: {self.output_shapes}")

  def _parse_core_mask(self, use_npu_cores: str) -> int:
    """Parse NPU core specification into core mask."""
    if use_npu_cores == "all":
      return 0xFF
    elif use_npu_cores == "0":
      return 0x01
    elif use_npu_cores == "1":
      return 0x02
    elif use_npu_cores == "2":
      return 0x04
    elif "," in use_npu_cores:
      cores = [int(c.strip()) for c in use_npu_cores.split(",")]
      mask = 0
      for core in cores:
        mask |= (1 << core)
      return mask
    else:
      return 0xFF

  def _load_model(self, use_npu_cores: str):
    """Load model through HAL."""
    from openpilot.system.inferenced.compute import ModelConfig

    core_mask = self._parse_core_mask(use_npu_cores)

    config = ModelConfig(
      name='rknn_model',
      path=str(self.model_path),
      npu_cores=core_mask
    )

    if not self.npu.load_model(config):
      raise RuntimeError(f"Failed to load model via HAL: {self.model_path}")

  def _query_model_info(self):
    """No-op: input/output shapes are provided at construction time."""

  def run(
    self,
    inputs: np.ndarray | dict[str, np.ndarray],
    copy_to_cpu: bool = True,
  ) -> np.ndarray | list[np.ndarray] | dict[str, np.ndarray]:
    """Run inference.

    Args:
        inputs: Input tensor(s). Can be:
                - Single numpy array for single-input models
                - dict of name -> array for multi-input models
        copy_to_cpu: Copy outputs to CPU (always True with HAL)

    Returns:
        Output tensor(s) in the same format as inputs
    """
    # Normalize inputs to dict
    if isinstance(inputs, np.ndarray):
      inputs = {'input': inputs}

    # Run inference through HAL
    result = self.npu.infer('rknn_model', inputs)

    if not result.success:
      raise RuntimeError(f"Inference failed: {result.error_message}")

    outputs = result.outputs

    # Return in appropriate format
    if len(outputs) == 1 and 'output' in outputs:
      return cast(np.ndarray, outputs['output'])
    elif len(outputs) == 1:
      return cast(np.ndarray, list(outputs.values())[0])
    else:
      return cast(dict[str, np.ndarray], outputs)

  def run_async(
    self,
    inputs: np.ndarray | dict[str, np.ndarray],
  ) -> str:
    """Run inference synchronously; HAL has no async API."""
    if isinstance(inputs, np.ndarray):
      inputs = {'input': inputs}
    result = self.npu.infer('rknn_model', inputs)
    if not result.success:
      raise RuntimeError(f"Inference failed: {result.error_message}")
    self._last_async_outputs = result.outputs
    return 'sync'

  def get_async_result(self, request_id: str) -> dict[str, np.ndarray | None]:
    """Return outputs from the last run_async() call."""
    outputs = getattr(self, '_last_async_outputs', None)
    self._last_async_outputs = None
    if outputs is None:
      return {}
    return cast(dict[str, np.ndarray | None], outputs)

  def get_perf_detail(self) -> dict[str, Any]:
    """Return available performance stats from the NPU backend."""
    stats = self.npu.get_stats()
    return {
      'tasks_completed': stats.tasks_completed,
      'tasks_failed': stats.tasks_failed,
      'total_exec_time_ms': stats.total_exec_time_ms,
    }

  def release(self):
    """Release resources."""
    self.client.release()

  def __del__(self):
    self.release()

  def __enter__(self):
    return self

  def __exit__(self, exc_type, exc_val, exc_tb):
    self.release()


class RKNNModelPool:
  """Pool of RKNN runners for batch processing."""

  def __init__(
    self,
    model_path: str | Path,
    pool_size: int = 2,
    use_npu_cores: str = "all",
  ):
    """Initialize model pool.

    Args:
        model_path: Path to .rknn model file
        pool_size: Number of runners in pool
        use_npu_cores: NPU cores to use
    """
    self.model_path = Path(model_path)
    self.pool_size = pool_size

    # Create HAL client
    self.client = InferenceClient("rknn_pool")

    # Get NPU backend
    self.npu = self.client.npu()

    # Load model once
    from openpilot.system.inferenced.compute import ModelConfig

    core_mask = 0xFF if use_npu_cores == "all" else int(use_npu_cores)
    config = ModelConfig(
      name='rknn_pool_model',
      path=str(self.model_path),
      npu_cores=core_mask
    )

    if not self.npu.load_model(config):
      raise RuntimeError(f"Failed to load model: {self.model_path}")

  def run_batch(
    self,
    batch_inputs: list[dict[str, np.ndarray]],
  ) -> list[dict[str, np.ndarray]]:
    """Run batch of inferences.

    Args:
        batch_inputs: list of input dicts

    Returns:
        list of output dicts
    """
    results = []

    for inputs in batch_inputs:
      result = self.npu.infer('rknn_pool_model', inputs)
      if result.success:
        results.append(result.outputs)
      else:
        results.append({})

    return results

  def release(self):
    """Release resources."""
    self.client.release()

  def __del__(self):
    self.release()

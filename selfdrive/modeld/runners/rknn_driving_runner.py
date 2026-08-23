#!/usr/bin/env python3
"""Split RKNN driving runner (bukapilot KA2 / RK3588 architecture).

Follows ``../bukapilot``'s KA2 modeld: separate ``driving_vision.rknn`` and
``driving_policy.rknn`` models behind the openpilot driving-runner contract.
All NPU access goes through the centralized inferenced HAL.
"""

from __future__ import annotations

import time

from typing import Any

import numpy as np

from openpilot.common.swaglog import cloudlog
from openpilot.system.inferenced import InferenceClient
from openpilot.system.inferenced.compute import ModelConfig

from openpilot.selfdrive.modeld.runners.driving_runner import (
    DrivingRunner, DrivingModelSpec, DrivingRunnerResult,
)


class RKNNDrivingRunner(DrivingRunner):
  """Split RKNN driving runner using the centralized HAL.

  Loads the split ``driving_vision`` and ``driving_policy`` models (bukapilot
  KA2 architecture) and runs them through ``InferenceClient.npu()``. This
  preserves the existing modeld behavior while fitting the driving-runner
  contract.
  """

  def __init__(
    self,
    vision_spec: DrivingModelSpec,
    policy_spec: DrivingModelSpec,
    client: InferenceClient | None = None,
    backend: Any | None = None,
    use_npu_core: int = 0,
  ) -> None:
    super().__init__(vision_spec, policy_spec)
    self.vision_spec = vision_spec
    self.policy_spec = policy_spec
    self._client = client or InferenceClient("modeld")
    # ``backend`` allows the factory to inject the result of
    # ``client.inference_backend()`` so dev-PC/ONNX behavior is preserved.
    self._npu = backend if backend is not None else self._client.npu()
    self._use_npu_core = use_npu_core
    self._backend_type = self._npu.backend_type.name

  @property
  def backend_name(self) -> str:
    return self._backend_type

  def _to_model_config(self, spec: DrivingModelSpec) -> ModelConfig:
    """Build HAL ModelConfig from runner spec."""
    meta = spec.metadata or {}
    return ModelConfig(
      name=spec.name,
      path=str(spec.path),
      input_shapes=spec.input_shapes,
      output_shapes=spec.output_shapes,
      npu_cores=meta.get("npu_cores", self._use_npu_core),
    )

  def load(self) -> None:
    if self._loaded:
      return

    for spec in (self.vision_spec, self.policy_spec):
      if not spec.path.exists():
        raise FileNotFoundError(f"RKNN model not found: {spec.path}")

    cloudlog.info(f"RKNNDrivingRunner loading vision model: {self.vision_spec.path}")
    if not self._npu.load_model(self._to_model_config(self.vision_spec)):
      raise RuntimeError("Failed to load RKNN vision model")

    cloudlog.info(f"RKNNDrivingRunner loading policy model: {self.policy_spec.path}")
    if not self._npu.load_model(self._to_model_config(self.policy_spec)):
      raise RuntimeError("Failed to load RKNN policy model")

    self._loaded = True
    cloudlog.info("RKNNDrivingRunner models loaded")

  @staticmethod
  def _ordered(spec: DrivingModelSpec, inputs: dict[str, np.ndarray]) -> dict[str, np.ndarray]:
    """Reorder ``inputs`` to match the model's compiled input-slot order.

    The HAL's RKNN backend feeds inputs to ``rknn.inference()`` positionally,
    in dict-insertion order (see rockchip_npu.py) — it has no model-shape
    awareness of its own. ``spec.input_shapes`` (from the model metadata
    pickle) carries the true compiled slot order, so the runner — the only
    layer that knows both the model contract and the raw dict a caller
    handed it — is responsible for aligning the two before dispatch.
    """
    order = list(spec.input_shapes.keys())
    if list(inputs.keys()) == order:
      return inputs
    return {name: inputs[name] for name in order if name in inputs}

  def _run_model(self, model_name: str, inputs: dict[str, np.ndarray]) -> DrivingRunnerResult:
    t0 = time.perf_counter()
    result = self._npu.infer(model_name, inputs)
    exec_time_ms = (time.perf_counter() - t0) * 1000.0

    if not result.success:
      return DrivingRunnerResult(
        success=False,
        outputs={},
        error_message=result.error_message or f"{model_name} inference failed",
        exec_time_ms=exec_time_ms,
      )

    outputs = result.outputs if result.outputs is not None else {}
    # RKNN and ONNX backends may return a single unnamed output; normalize to
    # the ``outputs`` key expected by modeld's Parser.
    if "outputs" not in outputs and len(outputs) == 1:
      outputs = {"outputs": next(iter(outputs.values()))}

    return DrivingRunnerResult(
      success=True,
      outputs=outputs,
      exec_time_ms=exec_time_ms,
    )

  def run_vision(self, inputs: dict[str, np.ndarray]) -> DrivingRunnerResult:
    if not self._loaded:
      raise RuntimeError("RKNNDrivingRunner: models not loaded")
    return self._run_model(self.vision_spec.name, self._ordered(self.vision_spec, inputs))

  def run_policy(self, inputs: dict[str, np.ndarray]) -> DrivingRunnerResult:
    if not self._loaded:
      raise RuntimeError("RKNNDrivingRunner: models not loaded")

    # Reorder against the metadata's ``desire`` key first, then rename for
    # ONNX in place — renaming before reordering would drop the input, since
    # spec.input_shapes (loaded from the shared metadata pkl) always keys it
    # as ``desire``, never ``desire_pulse``.
    inputs = self._ordered(self.policy_spec, inputs)
    if self._backend_type == "ONNX" and "desire" in inputs:
      inputs = {("desire_pulse" if k == "desire" else k): v for k, v in inputs.items()}

    return self._run_model(self.policy_spec.name, inputs)

  def release(self) -> None:
    self._loaded = False
    self._client.release()

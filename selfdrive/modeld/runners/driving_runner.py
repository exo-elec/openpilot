#!/usr/bin/env python3
"""Abstract driving-runner contract for modeld.

A driving runner owns model loading and inference for one driving-model
execution path. Modeld keeps temporal state, preprocessing, output parsing and
messaging; the runner only sees prepared tensors and returns raw outputs.

Two proven shapes are supported, mirroring upstream openpilot and bukapilot:

- Split vision + policy (``is_monolithic == False``): the runner implements
  ``run_vision()`` and ``run_policy()``. This is the bukapilot KA2 (RK3588)
  RKNN architecture — ``driving_vision.rknn`` + ``driving_policy.rknn``.
- Monolithic supercombo (``is_monolithic == True``): the runner implements
  ``run()`` over the full input set and returns one raw output tensor. This is
  the upstream Chestnut external-USB-GPU big-model architecture
  (``big_driving_supercombo`` via a compiled tinygrad JIT artifact).
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import numpy as np


@dataclass(frozen=True)
class DrivingModelSpec:
  """Specification for one model loaded by a driving runner."""
  name: str
  path: Path
  input_shapes: dict[str, tuple[int, ...]]
  output_shapes: dict[str, tuple[int, ...]]
  # Backend-specific hints (e.g. NPU core mask, output_slices for monolithic models).
  metadata: dict[str, Any] | None = None


@dataclass(frozen=True)
class DrivingRunnerResult:
  """Result of one driving-runner inference step."""
  success: bool
  outputs: dict[str, np.ndarray]
  error_message: str = ""
  exec_time_ms: float = 0.0


class DrivingRunner(ABC):
  """One openpilot-compatible driving runner.

  Implementations must:
    - load their model(s) in ``load()``;
    - accept preprocessed tensors exactly as the openpilot supercombo contract
      defines (image tensors, desire, traffic convention, previous action/feature
      history, etc.);
    - return raw output tensors under the key ``outputs`` for modeld's Parser;
    - keep any backend-specific input-name remapping internal;
    - report finite, successful output via ``result.success``;
    - release exclusive resources in ``release()``.
  """

  # True for single-supercombo runners (Chestnut big model); False for split
  # vision+policy runners (RKNN, bukapilot KA2 style).
  is_monolithic: bool = False

  def __init__(self, *specs: DrivingModelSpec) -> None:
    self.specs = specs
    self._loaded = False

  @property
  @abstractmethod
  def backend_name(self) -> str:
    """Human-readable backend identifier (e.g. ``NPU``, ``ONNX``, ``CHESTNUT``)."""

  @property
  def loaded(self) -> bool:
    return self._loaded

  @property
  def output_slices(self) -> dict[str, slice]:
    """Raw-output slices for monolithic models (empty for split runners)."""
    return {}

  @abstractmethod
  def load(self) -> None:
    """Load model(s). Raises on fatal failure."""

  def run_vision(self, inputs: dict[str, np.ndarray]) -> DrivingRunnerResult:
    """Run the vision model (split runners only)."""
    raise NotImplementedError(f"{type(self).__name__} is not a split vision+policy runner")

  def run_policy(self, inputs: dict[str, np.ndarray]) -> DrivingRunnerResult:
    """Run the policy model (split runners only)."""
    raise NotImplementedError(f"{type(self).__name__} is not a split vision+policy runner")

  def run(self, inputs: dict[str, np.ndarray]) -> DrivingRunnerResult:
    """Run one monolithic-supercombo inference on prepared inputs."""
    raise NotImplementedError(f"{type(self).__name__} is not a monolithic runner")

  @abstractmethod
  def release(self) -> None:
    """Release exclusive backend resources."""

  def __enter__(self) -> DrivingRunner:
    return self

  def __exit__(self, exc_type, exc_val, exc_tb) -> None:
    self.release()

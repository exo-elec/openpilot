#!/usr/bin/env python3
"""eGPU external-USB-GPU monolithic driving runner.

Follows the eGPU (ASM2464PD / Chestnut) architecture: the official
``big_driving_supercombo`` monolithic model compiled to a tinygrad JIT
artifact (``big_driving_supercombo_tinygrad.pkl``) and executed on the
external USB GPU owned by ``inferenced``.

Unlike the split RKNN path (proven external split-RKNN style), an eGPU runner executes
the whole supercombo in one ``run()`` call over the full input set and returns
a single raw output tensor that modeld slices with the big-model metadata.

Until the compiled artifact, private multi-tensor transport and replay/HIL
gates are ready, ``load()`` fails closed and modeld falls back to the local
RKNN runner.
"""

from __future__ import annotations

from pathlib import Path

import numpy as np

from openpilot.common.swaglog import cloudlog

from openpilot.selfdrive.modeld.runners.driving_runner import (
    DrivingRunner, DrivingModelSpec, DrivingRunnerResult,
)


# Model/artifact naming follows upstream openpilot's Chestnut / eGPU big model.
EGPU_MODEL_NAME = "big_driving_supercombo"
EGPU_PKL_FILENAME = "big_driving_supercombo_tinygrad.pkl"
EGPU_METADATA_FILENAME = "big_driving_supercombo_metadata.pkl"

# Backward compatibility aliases
CHESTNUT_MODEL_NAME = EGPU_MODEL_NAME
CHESTNUT_PKL_FILENAME = EGPU_PKL_FILENAME
CHESTNUT_METADATA_FILENAME = EGPU_METADATA_FILENAME


class EgpuDrivingRunner(DrivingRunner):
  """ASM2464PD / Chestnut eGPU monolithic driving runner.

  Mirrors upstream's ``big_driving_tinygrad.pkl`` pattern: load a compiled
  tinygrad JIT artifact and execute the official openpilot big supercombo
  model on the external GPU (supporting both custom flashed ASM2464PD and
  comma's official Chestnut firmware). The current implementation is
  intentionally fail-closed until the compiled artifact and validation gates exist.
  """

  is_monolithic = True

  def __init__(self, spec: DrivingModelSpec) -> None:
    super().__init__(spec)
    self.spec = spec
    self.model_path = Path(spec.path)

  @property
  def backend_name(self) -> str:
    return "EGPU"

  @property
  def output_slices(self) -> dict[str, slice]:
    return (self.spec.metadata or {}).get("output_slices", {})

  def load(self) -> None:
    if not self.model_path.exists():
      raise FileNotFoundError(f"eGPU compiled artifact not found: {self.model_path}")
    cloudlog.warning("EgpuDrivingRunner: compiled tinygrad JIT artifact exists but transport and replay/HIL gates are not ready; failing closed")
    raise RuntimeError("eGPU driving path is gated until transport and validation are ready")

  def run(self, inputs: dict[str, np.ndarray]) -> DrivingRunnerResult:
    return DrivingRunnerResult(
      success=False,
      outputs={},
      error_message="eGPU driving runner is stubbed",
    )

  def release(self) -> None:
    self._loaded = False


# Backward compatibility alias
ChestnutDrivingRunner = EgpuDrivingRunner

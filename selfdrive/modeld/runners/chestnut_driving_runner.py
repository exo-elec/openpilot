#!/usr/bin/env python3
"""Chestnut external-USB-GPU monolithic driving runner.

Follows upstream openpilot's Chestnut architecture: the official
``big_driving_supercombo`` monolithic model compiled to a tinygrad JIT
artifact (``big_driving_supercombo_tinygrad.pkl``) and executed on the
external USB GPU owned by ``inferenced``.

Unlike the split RKNN path (bukapilot KA2 style), a Chestnut runner executes
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


# Model/artifact naming follows upstream openpilot's Chestnut big model.
CHESTNUT_MODEL_NAME = "big_driving_supercombo"
CHESTNUT_PKL_FILENAME = "big_driving_supercombo_tinygrad.pkl"
CHESTNUT_METADATA_FILENAME = "big_driving_supercombo_metadata.pkl"


class ChestnutDrivingRunner(DrivingRunner):
  """Chestnut external-GPU monolithic driving runner.

  Mirrors upstream's ``big_driving_tinygrad.pkl`` pattern: load a compiled
  tinygrad JIT artifact and execute the official openpilot big supercombo
  model. The current implementation is intentionally fail-closed until the
  compiled artifact and validation gates exist.
  """

  is_monolithic = True

  def __init__(self, spec: DrivingModelSpec) -> None:
    super().__init__(spec)
    self.spec = spec
    self.model_path = Path(spec.path)

  @property
  def backend_name(self) -> str:
    return "CHESTNUT"

  @property
  def output_slices(self) -> dict[str, slice]:
    return (self.spec.metadata or {}).get("output_slices", {})

  def load(self) -> None:
    if not self.model_path.exists():
      raise FileNotFoundError(f"Chestnut compiled artifact not found: {self.model_path}")
    cloudlog.warning("ChestnutDrivingRunner: compiled tinygrad JIT artifact exists but transport and replay/HIL gates are not ready; failing closed")
    raise RuntimeError("Chestnut driving path is gated until transport and validation are ready")

  def run(self, inputs: dict[str, np.ndarray]) -> DrivingRunnerResult:
    return DrivingRunnerResult(
      success=False,
      outputs={},
      error_message="Chestnut driving runner is stubbed",
    )

  def release(self) -> None:
    self._loaded = False

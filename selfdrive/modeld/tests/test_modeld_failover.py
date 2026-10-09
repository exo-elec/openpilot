#!/usr/bin/env python3
"""Tests for modeld's Chestnut -> RKNN failover state machine."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock, patch  # noqa: TID251

import numpy as np
import pytest

from openpilot.system.inferenced.compute import BackendType, InferenceResult
from openpilot.selfdrive.modeld.runners import (
    DrivingModelSpec,
    DrivingRunner,
    DrivingRunnerResult,
    RKNNDrivingRunner,
)


class _MockBackend:
  def __init__(self, backend_type: BackendType = BackendType.NPU):
    self.backend_type = backend_type

  def load_model(self, config) -> bool:
    return True

  def infer(self, model_name: str, inputs: dict) -> InferenceResult:
    return InferenceResult(success=True, outputs={"outputs": np.zeros((1, 8), dtype=np.float32)})


class _FailingChestnutRunner(DrivingRunner):
  """Monolithic external runner that always fails; exercises the failover raise path."""

  is_monolithic = True

  def __init__(self):
    super().__init__(
      DrivingModelSpec(name="big_driving_supercombo", path=Path("/dev/null"), input_shapes={}, output_shapes={}),
    )
    self.seen_inputs: dict | None = None

  @property
  def backend_name(self) -> str:
    return "CHESTNUT"

  def load(self) -> None:
    self._loaded = True

  def run(self, inputs: dict) -> DrivingRunnerResult:
    self.seen_inputs = inputs
    return DrivingRunnerResult(success=False, outputs={}, error_message="external timeout")

  def release(self) -> None:
    self._loaded = False


class _NonFiniteChestnutRunner(_FailingChestnutRunner):
  """External runner returning non-finite output; must raise for failover."""

  def run(self, inputs: dict) -> DrivingRunnerResult:
    self.seen_inputs = inputs
    bad = np.full(64, np.nan, dtype=np.float32)
    return DrivingRunnerResult(success=True, outputs={"outputs": bad})


def _make_model_state(runner: DrivingRunner):
  """Create a ModelState with mocked frames and the supplied runner."""
  with patch("openpilot.selfdrive.modeld.modeld.DrivingModelFrame") as MockFrame:
    mock_frame = MagicMock()
    mock_frame.prepare.return_value = MagicMock()
    mock_frame.buffer_from_cl.return_value = np.zeros(12 * 128 * 256, dtype=np.uint8)
    MockFrame.return_value = mock_frame

    client = MagicMock()
    client.inference_backend.return_value = _MockBackend(BackendType.NPU)

    from openpilot.selfdrive.modeld.models.commonmodel_pyx import CLContext
    from openpilot.selfdrive.modeld.modeld import ModelState

    ctx = CLContext()
    return ModelState(ctx, client, runner=runner)


def _run_args(model):
  bufs = {name: MagicMock() for name in model.vision_input_names}
  transforms = {name: np.eye(3, dtype=np.float32) for name in model.vision_input_names}
  inputs = {"desire": np.zeros(8, dtype=np.float32), "traffic_convention": np.zeros(2, dtype=np.float32)}
  return bufs, transforms, inputs


def test_model_state_raises_on_external_runner_failure():
  model = _make_model_state(_FailingChestnutRunner())

  with pytest.raises(RuntimeError, match="External driving model failed"):
    model.run(*_run_args(model), prepare_only=False)


def test_model_state_raises_on_non_finite_external_output():
  model = _make_model_state(_NonFiniteChestnutRunner())

  with pytest.raises(RuntimeError, match="not finite"):
    model.run(*_run_args(model), prepare_only=False)


def test_monolithic_runner_receives_full_input_set():
  runner = _FailingChestnutRunner()
  model = _make_model_state(runner)

  with pytest.raises(RuntimeError):
    model.run(*_run_args(model), prepare_only=False)

  assert runner.seen_inputs is not None
  for name in model.vision_input_names:
    assert name in runner.seen_inputs
  for name in ("desire", "traffic_convention", "features_buffer"):
    assert name in runner.seen_inputs


def test_model_state_set_runner_switches_active_runner():
  external = _FailingChestnutRunner()
  backend = _MockBackend(BackendType.NPU)
  client = MagicMock()
  client.inference_backend.return_value = backend

  vision_spec = DrivingModelSpec(
    name="driving_vision", path=Path("/dev/null"),
    input_shapes={"input_imgs": (1, 12, 128, 256)},
    output_shapes={"outputs": (1, 6116)},
  )
  policy_spec = DrivingModelSpec(
    name="driving_policy", path=Path("/dev/null"),
    input_shapes={"desire": (1, 100, 8)},
    output_shapes={"outputs": (1, 176)},
  )
  rknn = RKNNDrivingRunner(vision_spec, policy_spec, client=client, backend=backend)
  rknn.load()

  model = _make_model_state(external)
  assert model.runner.backend_name == "CHESTNUT"
  assert model._external_runner_active

  model.set_runner(rknn)
  assert model.runner.backend_name == "NPU"
  assert not model._external_runner_active

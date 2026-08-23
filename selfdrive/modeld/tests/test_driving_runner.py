#!/usr/bin/env python3
"""Tests for the driving-runner contract and the RKNN/Chestnut implementations."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock  # noqa: TID251

import numpy as np
import pytest

from openpilot.system.inferenced.compute import BackendType, InferenceResult
from openpilot.selfdrive.modeld.runners import (
    DrivingModelSpec,
    DrivingRunnerResult,
    RKNNDrivingRunner,
    ChestnutDrivingRunner,
    create_driving_runner,
)


class _MockBackend:
  """Mock NPU backend that records loaded models and returns fixed outputs."""

  def __init__(self, backend_type: BackendType = BackendType.NPU):
    self.backend_type = backend_type
    self.loaded_models: dict[str, dict] = {}
    self.infer_outputs: dict[str, np.ndarray] = {}
    self.last_inputs: dict[str, np.ndarray] | None = None

  def load_model(self, config) -> bool:
    self.loaded_models[config.name] = {
      "path": config.path,
      "input_shapes": config.input_shapes,
      "output_shapes": config.output_shapes,
    }
    return True

  def infer(self, model_name: str, inputs: dict[str, np.ndarray]) -> InferenceResult:
    self.last_inputs = inputs
    output = self.infer_outputs.get(model_name, np.zeros((1, 8), dtype=np.float32))
    return InferenceResult(success=True, outputs={"outputs": output})


@pytest.fixture
def specs(tmp_path: Path):
  vision_path = tmp_path / "vision.rknn"
  policy_path = tmp_path / "policy.rknn"
  vision_path.write_text("mock")
  policy_path.write_text("mock")

  vision_spec = DrivingModelSpec(
    name="driving_vision",
    path=vision_path,
    input_shapes={"img": (1, 12, 128, 256), "big_img": (1, 12, 128, 256)},
    output_shapes={"outputs": (1, 6116)},
  )
  # Matches the real metadata's compiled RKNN slot order (desire, traffic_convention,
  # features_buffer) — modeld builds the policy_inputs dict in a different order,
  # so the runner must reorder against this before dispatch (see RKNNDrivingRunner._ordered).
  policy_spec = DrivingModelSpec(
    name="driving_policy",
    path=policy_path,
    input_shapes={"desire": (1, 100, 8), "traffic_convention": (1, 2), "features_buffer": (1, 100, 128)},
    output_shapes={"outputs": (1, 176)},
  )
  return vision_spec, policy_spec


def _make_runner(specs, backend_type: BackendType = BackendType.NPU):
  vision_spec, policy_spec = specs
  backend = _MockBackend(backend_type)
  client = MagicMock()
  client.npu.return_value = backend
  runner = RKNNDrivingRunner(vision_spec, policy_spec, client=client)
  runner._npu = backend
  return runner, backend


def test_loads_vision_and_policy_models(specs):
  runner, backend = _make_runner(specs)
  runner.load()
  assert runner.loaded
  assert not runner.is_monolithic
  assert "driving_vision" in backend.loaded_models
  assert "driving_policy" in backend.loaded_models


def test_load_raises_when_model_missing(specs):
  vision_spec, policy_spec = specs
  vision_spec.path.unlink()
  runner, _ = _make_runner((vision_spec, policy_spec))
  with pytest.raises(FileNotFoundError):
    runner.load()


def test_run_vision_returns_outputs_key(specs):
  runner, backend = _make_runner(specs)
  expected = np.zeros((1, 6116), dtype=np.float32)
  backend.infer_outputs["driving_vision"] = expected
  runner.load()

  result = runner.run_vision({
    "img": np.zeros((1, 12, 128, 256), dtype=np.uint8),
    "big_img": np.zeros((1, 12, 128, 256), dtype=np.uint8),
  })
  assert isinstance(result, DrivingRunnerResult)
  assert result.success
  assert "outputs" in result.outputs
  np.testing.assert_array_equal(result.outputs["outputs"], expected)


def test_run_vision_reorders_inputs_to_slot_order(specs):
  runner, backend = _make_runner(specs)
  runner.load()

  # Handed in reverse of vision_spec.input_shapes' compiled order (img, big_img).
  runner.run_vision({
    "big_img": np.full((1, 12, 128, 256), 2, dtype=np.uint8),
    "img": np.full((1, 12, 128, 256), 1, dtype=np.uint8),
  })

  assert list(backend.last_inputs.keys()) == ["img", "big_img"]
  assert backend.last_inputs["img"][0, 0, 0, 0] == 1
  assert backend.last_inputs["big_img"][0, 0, 0, 0] == 2


def test_run_policy_reorders_inputs_to_slot_order(specs):
  runner, backend = _make_runner(specs)
  runner.load()

  # Mirrors modeld.py's actual (wrong) construction order — traffic_convention
  # and features_buffer before desire — which the runner must correct against
  # policy_spec.input_shapes' compiled order (desire, traffic_convention, features_buffer).
  runner.run_policy({
    "traffic_convention": np.zeros((1, 2), dtype=np.float32),
    "features_buffer": np.zeros((1, 100, 128), dtype=np.float32),
    "desire": np.zeros((1, 100, 8), dtype=np.float32),
  })

  assert list(backend.last_inputs.keys()) == ["desire", "traffic_convention", "features_buffer"]


def test_run_policy_normalizes_desire_for_onnx(specs):
  runner, backend = _make_runner(specs, BackendType.ONNX)
  backend.infer_outputs["driving_policy"] = np.zeros((1, 176), dtype=np.float32)
  runner.load()

  inputs = {
    "traffic_convention": np.zeros((1, 2), dtype=np.float32),
    "features_buffer": np.zeros((1, 100, 128), dtype=np.float32),
    "desire": np.zeros((1, 100, 8), dtype=np.float32),
  }
  result = runner.run_policy(inputs)

  assert result.success
  # Renamed for ONNX, but still reordered to the compiled slot order.
  assert list(backend.last_inputs.keys()) == ["desire_pulse", "traffic_convention", "features_buffer"]


def test_run_before_load_raises(specs):
  runner, _ = _make_runner(specs)
  with pytest.raises(RuntimeError):
    runner.run_vision({"img": np.zeros((1, 12, 128, 256))})


def test_failed_inference_returns_unsuccessful_result(specs):
  runner, backend = _make_runner(specs)
  runner.load()

  def failing_infer(model_name, inputs):
    return InferenceResult(success=False, error_message="npu timeout")

  backend.infer = failing_infer
  result = runner.run_vision({"img": np.zeros((1, 12, 128, 256)), "big_img": np.zeros((1, 12, 128, 256))})
  assert not result.success
  assert "npu timeout" in result.error_message


def test_injected_backend_is_used(specs):
  vision_spec, policy_spec = specs
  backend = _MockBackend(BackendType.ONNX)
  client = MagicMock()
  runner = RKNNDrivingRunner(vision_spec, policy_spec, client=client, backend=backend)
  assert runner.backend_name == "ONNX"


def test_split_runner_rejects_monolithic_run(specs):
  runner, _ = _make_runner(specs)
  runner.load()
  with pytest.raises(NotImplementedError):
    runner.run({})


def test_factory_selects_local_runner_and_loads_models(tmp_path: Path):
  vision_path = tmp_path / "vision.rknn"
  policy_path = tmp_path / "policy.rknn"
  vision_path.write_text("mock")
  policy_path.write_text("mock")

  backend = _MockBackend(BackendType.NPU)
  client = MagicMock()
  client.inference_backend.return_value = backend

  runner = create_driving_runner(
    client,
    vision_model_path=vision_path,
    policy_model_path=policy_path,
    vision_metadata_path=tmp_path / "vision_metadata.pkl",
    policy_metadata_path=tmp_path / "policy_metadata.pkl",
    params=MagicMock(),
  )

  assert isinstance(runner, RKNNDrivingRunner)
  assert runner.loaded
  assert "driving_vision" in backend.loaded_models
  assert "driving_policy" in backend.loaded_models


def test_factory_chestnut_fails_closed_when_artifact_missing(tmp_path: Path):
  client = MagicMock()
  with pytest.raises(FileNotFoundError, match="Chestnut compiled artifact not found"):
    create_driving_runner(
      client,
      vision_model_path=tmp_path / "vision.rknn",
      policy_model_path=tmp_path / "policy.rknn",
      vision_metadata_path=tmp_path / "vision_metadata.pkl",
      policy_metadata_path=tmp_path / "policy_metadata.pkl",
      params=MagicMock(),
      use_chestnut=True,
      chestnut_model_path=tmp_path / "big_driving_supercombo_tinygrad.pkl",
      chestnut_metadata_path=tmp_path / "big_driving_supercombo_metadata.pkl",
    )


def test_factory_chestnut_fails_closed_when_gated(tmp_path: Path):
  artifact = tmp_path / "big_driving_supercombo_tinygrad.pkl"
  artifact.write_text("mock")

  client = MagicMock()
  with pytest.raises(RuntimeError, match="Chestnut driving path is gated"):
    create_driving_runner(
      client,
      vision_model_path=tmp_path / "vision.rknn",
      policy_model_path=tmp_path / "policy.rknn",
      vision_metadata_path=tmp_path / "vision_metadata.pkl",
      policy_metadata_path=tmp_path / "policy_metadata.pkl",
      params=MagicMock(),
      use_chestnut=True,
      chestnut_model_path=artifact,
      chestnut_metadata_path=tmp_path / "big_driving_supercombo_metadata.pkl",
    )


def test_chestnut_runner_is_monolithic_and_stubbed(tmp_path: Path):
  spec = DrivingModelSpec(
    name="big_driving_supercombo",
    path=tmp_path / "big_driving_supercombo_tinygrad.pkl",
    input_shapes={},
    output_shapes={},
    metadata={"output_slices": {"plan": slice(0, 10)}},
  )
  runner = ChestnutDrivingRunner(spec)
  assert runner.is_monolithic
  assert runner.backend_name == "CHESTNUT"
  assert runner.output_slices == {"plan": slice(0, 10)}
  assert not runner.run({}).success
  with pytest.raises(NotImplementedError):
    runner.run_vision({})

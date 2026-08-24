#!/usr/bin/env python3
"""Factory for creating the active modeld driving runner.

Selection is intentionally conservative: the split RKNN runner (proven
external split-RKNN architecture) is always available and remains authoritative until the Chestnut
external-GPU driving path has passed replay/HIL/hardware gates. The factory
only returns a Chestnut runner when explicitly requested; it fails closed so
modeld exercises the failover path and falls back to RKNN.
"""

from __future__ import annotations

import pickle
from pathlib import Path
from typing import Any

from openpilot.common.swaglog import cloudlog
from openpilot.common.params import Params
from openpilot.system.inferenced import InferenceClient

from openpilot.selfdrive.modeld.runners.driving_runner import DrivingRunner, DrivingModelSpec
from openpilot.selfdrive.modeld.runners.rknn_driving_runner import RKNNDrivingRunner
from openpilot.selfdrive.modeld.runners.egpu_driving_runner import (
    EgpuDrivingRunner, ChestnutDrivingRunner,
    EGPU_MODEL_NAME, EGPU_PKL_FILENAME, EGPU_METADATA_FILENAME,
    CHESTNUT_MODEL_NAME, CHESTNUT_PKL_FILENAME, CHESTNUT_METADATA_FILENAME,
)


_MODELS_DIR = Path(__file__).parent.parent / "models"


def _load_metadata(path: Path) -> dict[str, Any]:
  """Load pickle metadata or return an empty dict."""
  if path.exists():
    try:
      with open(path, 'rb') as f:
        return pickle.load(f)
    except Exception as e:
      cloudlog.warning(f"Failed to load model metadata {path}: {e}")
  return {}


def build_driving_specs(
  vision_model_path: Path,
  policy_model_path: Path,
  vision_metadata_path: Path,
  policy_metadata_path: Path,
) -> tuple[DrivingModelSpec, DrivingModelSpec]:
  """Build split vision/policy runner specs from modeld's model/metadata paths."""
  vision_meta = _load_metadata(vision_metadata_path)
  policy_meta = _load_metadata(policy_metadata_path)

  vision_spec = DrivingModelSpec(
    name="driving_vision",
    path=vision_model_path,
    input_shapes=vision_meta.get("input_shapes", {"input_imgs": (1, 12, 128, 256), "big_input_imgs": (1, 12, 128, 256)}),
    output_shapes=vision_meta.get("output_shapes", {"outputs": (1, 6116)}),
  )

  policy_spec = DrivingModelSpec(
    name="driving_policy",
    path=policy_model_path,
    input_shapes=policy_meta.get("input_shapes", {"desire": (1, 100, 8), "traffic_convention": (1, 2), "features_buffer": (1, 100, 128)}),
    output_shapes=policy_meta.get("output_shapes", {"outputs": (1, 176)}),
  )

  return vision_spec, policy_spec


def build_egpu_spec(
  model_path: Path | None = None,
  metadata_path: Path | None = None,
) -> DrivingModelSpec:
  """Build the monolithic eGPU (ASM2464PD / Chestnut) big-model spec from its compiled artifact + metadata."""
  model_path = model_path or _MODELS_DIR / EGPU_PKL_FILENAME
  metadata_path = metadata_path or _MODELS_DIR / EGPU_METADATA_FILENAME
  meta = _load_metadata(metadata_path)
  return DrivingModelSpec(
    name=EGPU_MODEL_NAME,
    path=model_path,
    input_shapes=meta.get("input_shapes", {}),
    output_shapes=meta.get("output_shapes", {}),
    metadata={"output_slices": meta.get("output_slices", {})},
  )


# Backward compatibility alias
build_chestnut_spec = build_egpu_spec


def create_driving_runner(
  client: InferenceClient,
  vision_model_path: Path,
  policy_model_path: Path,
  vision_metadata_path: Path,
  policy_metadata_path: Path,
  params: Params | None = None,
  use_egpu: bool = False,
  use_chestnut: bool = False,
  egpu_model_path: Path | None = None,
  egpu_metadata_path: Path | None = None,
  chestnut_model_path: Path | None = None,
  chestnut_metadata_path: Path | None = None,
) -> DrivingRunner:
  """Create and load the active driving runner.

  Selection is fail-closed:
    - ``use_egpu=False`` (default) returns the local split RKNN/ONNX runner
      (proven external split-RKNN architecture).
    - ``use_egpu=True`` (or ``use_chestnut=True``) returns the monolithic eGPU
      external-GPU runner. The current stub refuses to load so modeld exercises the
      failover path and falls back to RKNN. It will be replaced by a real
      tinygrad JIT runner once the private multi-tensor transport and
      validation gates are ready.
  """
  if use_egpu or use_chestnut:
    # TODO: once private transport + compiled artifacts are ready, verify the
    # artifact is advertised by inferenced before constructing the real runner.
    target_model_path = egpu_model_path or chestnut_model_path
    target_metadata_path = egpu_metadata_path or chestnut_metadata_path
    runner = EgpuDrivingRunner(build_egpu_spec(target_model_path, target_metadata_path))
    runner.load()
    return runner

  vision_spec, policy_spec = build_driving_specs(
    vision_model_path, policy_model_path,
    vision_metadata_path, policy_metadata_path,
  )

  # Use the same backend selection modeld previously used directly:
  # NPU on real ARM hardware, ONNX on x86 dev PC, mock NPU as last resort.
  backend = client.inference_backend()
  runner = RKNNDrivingRunner(vision_spec, policy_spec, client=client, backend=backend)
  runner.load()
  return runner

#!/usr/bin/env python3
"""Factory for creating the active modeld driving runner.

The split RKNN/ONNX runner is the on-board driving model. The big model does not come from here: it runs on a
phone over USB (nagaspilot/bigmodel) and is attached by modeld on top of this runner.
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
from openpilot.selfdrive.modeld.runners.rknn_platform import detect_platform, get_core_mask


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


def create_driving_runner(
  client: InferenceClient,
  vision_model_path: Path,
  policy_model_path: Path,
  vision_metadata_path: Path,
  policy_metadata_path: Path,
  params: Params | None = None,
) -> DrivingRunner:
  """Create and load the active driving runner.

  Returns the local split RKNN/ONNX runner.
  """
  vision_spec, policy_spec = build_driving_specs(
    vision_model_path, policy_model_path,
    vision_metadata_path, policy_metadata_path,
  )

  # Use the same backend selection modeld previously used directly:
  # NPU on real ARM hardware, ONNX on x86 dev PC, mock NPU as last resort.
  backend = client.inference_backend()
  core = get_core_mask(detect_platform(), "modeld")
  runner = RKNNDrivingRunner(vision_spec, policy_spec, client=client, backend=backend, use_npu_core=core)
  runner.load()
  return runner

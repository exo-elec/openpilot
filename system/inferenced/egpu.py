#!/usr/bin/env python3
"""eGPU Backend — ASM2464PD-bridge eGPU + desktop AMD Radeon GPU (RDNA4).

Loads and runs .onnx models via tinygrad's ONNX frontend
(tinygrad.nn.onnx.OnnxRunner) on the USB-attached AMD GPU
(runtime/ops_amd.py's USBIface) — see exopilot's
docs/02-HARDWARE/EGPU_ASM2464PD.md for the full hardware/firmware writeup.

The first routed workloads are separate side-camera and rear-camera YOLO
models in shadow mode. Hailo/local detections remain authoritative; eGPU
results are compared and logged only. The backend is deliberately not part
of a WorkloadClass fallback tier, so it cannot replace safety inference by
being merely present.

Requires tinygrad (tinygrad.nn.onnx, tinygrad.tensor), pinned through the
official tinygrad_repo submodule at release tag v0.13.0. initialize() still
guards the import so an incomplete deployment degrades cleanly.

Detection: post-flash device enumerates as 0xADD1:0x0001 or 0x3801:0x0001
(tinygrad corp's own USB-GPU bridge IDs, not comma-specific — see
tinygrad/extra/usbgpu/patch.py and runtime/ops_amd.py's USBIface), with USB
product string "USB 3.2 PCIe TinyEnclosure" (the literal string patch.py
writes into the flashed firmware — checked alongside VID:PID so an
unrelated device reusing those IDs can't false-positive). The pre-flash
ROM-bootloader IDs (0x174C:0x2464, 0x174C:0x2463) are deliberately not
checked here — a ROM-state device needs flashing before it's usable as a
GPU backend, which this backend does not perform.
"""

from __future__ import annotations

import glob
import logging
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from openpilot.system.inferenced.compute import (
    HardwareBackend, BackendType, InferenceResult, ModelConfig
)

logger = logging.getLogger(__name__)

EGPU_VID_PIDS = {("0xadd1", "0x0001"), ("0x3801", "0x0001")}

# The literal USB product string tinygrad's extra/usbgpu/patch.py writes into
# config1's descriptor bytes at flash time (confirmed by reading patch.py
# directly: bytes 64-90 of config1 decode to this ASCII string). Unlike
# comma's Chestnut firmware, which embeds a per-build hash
# (f"custom {CHESTNUT_FW_VERSION}-CLEAN"), tinygrad's generic firmware uses
# this fixed literal — no release process on our side to track a hash
# against, so an exact string match is the right check here.
EGPU_PRODUCT = "USB 3.2 PCIe TinyEnclosure"


def _detect_egpu() -> bool:
  """Return True if a flashed (post-firmware) ASM2464PD is on the USB bus.

  Checks both the VID:PID and the product string tinygrad's patch.py writes,
  so an unrelated device that happens to reuse 0xADD1:0x0001 doesn't false-positive.
  """
  for path in glob.glob('/sys/bus/usb/devices/*'):
    try:
      with open(f'{path}/idVendor') as f:
        vendor = f.read().strip().lower()
      with open(f'{path}/idProduct') as f:
        product_id = f.read().strip().lower()
      with open(f'{path}/product') as f:
        product_str = f.read().strip()
    except OSError:
      continue
    if (f'0x{vendor}', f'0x{product_id}') in EGPU_VID_PIDS and product_str == EGPU_PRODUCT:
      return True
  return False


@dataclass
class _EgpuModelHandle:
  name: str
  path: str
  runner: Any  # tinygrad.nn.onnx.OnnxRunner, weights moved onto the AMD device
  input_names: tuple[str, ...]


class EgpuBackend(HardwareBackend):
  """ASM2464PD-bridge eGPU backend — loads/runs .onnx models via tinygrad's ONNX frontend.

  Deliberately not registered as CAMERA_INFERENCE, VOICE_INFERENCE, or
  SAFETY_INFERENCE. Shadow callers request BackendType.EGPU explicitly.
  """

  WORKLOAD_CLASS = None  # not yet assigned to a tier — see module docstring
  HAS_DEVICE_MEMORY = True  # discrete desktop GPU VRAM, unlike Hailo-8/DX-M1

  def __init__(self):
    super().__init__(BackendType.EGPU)
    self._onnx_runner_cls: Any = None
    self._models: dict[str, _EgpuModelHandle] = {}

  # ------------------------------------------------------------------
  # Backend lifecycle
  # ------------------------------------------------------------------

  def initialize(self) -> bool:
    """Probe for a flashed ASM2464PD and confirm tinygrad is importable."""
    if not _detect_egpu():
      logger.debug("eGPU (ASM2464PD) not detected — skipping backend")
      return False

    try:
      from tinygrad.nn.onnx import OnnxRunner
      self._onnx_runner_cls = OnnxRunner
    except ImportError:
      logger.warning("eGPU (ASM2464PD) detected but tinygrad is not installed — backend unavailable")
      return False

    self._initialized = True
    logger.info("eGPU (ASM2464PD) detected and tinygrad available — backend ready")
    return True

  def release(self) -> None:
    """Release all loaded models."""
    self._models.clear()
    self._initialized = False

  # ------------------------------------------------------------------
  # Model management
  # ------------------------------------------------------------------

  def load_model(self, config: ModelConfig) -> bool:
    """Load an .onnx model via tinygrad's ONNX frontend, weights on the AMD device."""
    if not self._initialized:
      logger.error("eGPU backend not initialized")
      return False

    if config.name in self._models:
      logger.debug(f"eGPU model already loaded: {config.name}")
      return True

    path = Path(config.path)
    if not path.is_file():
      logger.error(f"eGPU model not found: {config.path}")
      return False

    try:
      runner = self._onnx_runner_cls(str(path)).to("AMD")
      input_names = tuple(runner.graph_inputs)
      self._models[config.name] = _EgpuModelHandle(name=config.name, path=str(path), runner=runner, input_names=input_names)
      config.loaded = True
      logger.info(f"Loaded eGPU model: {config.name} ({path.stat().st_size // 1024 // 1024} MB)")
      return True
    except Exception:
      logger.exception(f"Failed to load eGPU model '{config.name}'")
      return False

  def unload_model(self, name: str) -> bool:
    """Unload a model."""
    return self._models.pop(name, None) is not None

  # ------------------------------------------------------------------
  # Inference
  # ------------------------------------------------------------------

  def infer(self, model_name: str, inputs: dict[str, Any]) -> InferenceResult:
    """Run inference through tinygrad's ONNX frontend on the USB-attached AMD GPU."""
    if not self._initialized:
      return InferenceResult(
          backend_type=BackendType.EGPU, model_name=model_name,
          success=False, error_message="eGPU backend not initialized",
      )

    handle = self._models.get(model_name)
    if handle is None:
      return InferenceResult(
          backend_type=BackendType.EGPU, model_name=model_name,
          success=False, error_message=f"Model not loaded on eGPU backend: {model_name}",
      )

    try:
      from tinygrad.tensor import Tensor

      start = time.monotonic()
      self._stats.tasks_submitted += 1
      # Inputs are placed on the AMD device explicitly (not via the DEV env
      # var) so this backend can't interfere with any other tinygrad device
      # this process might use — see module docstring. Guard against an
      # already-a-Tensor input (Tensor(existing_tensor) raises — tinygrad
      # has no such-case handling, unlike OnnxRunner._parse_input, which
      # this mirrors).
      model_inputs = inputs
      # The current cereal IPC contract calls its sole input "input", while
      # exported ONNX models commonly call it "images". Remap only when both
      # sides are unambiguously single-input; multi-input models must use their
      # real names through a future multi-tensor transport.
      if set(inputs) == {'input'} and len(handle.input_names) == 1:
        model_inputs = {handle.input_names[0]: inputs['input']}

      tg_inputs = {}
      for name, value in model_inputs.items():
        tensor = value.to("AMD") if isinstance(value, Tensor) else Tensor(value, device="AMD")
        input_spec = handle.runner.graph_inputs.get(name)
        if input_spec is not None and tensor.dtype is not input_spec.dtype:
          # Camera tensors can travel as FP16 to halve USB traffic, then
          # cast in VRAM to the model's declared dtype (normally FP32).
          tensor = tensor.cast(input_spec.dtype)
        tg_inputs[name] = tensor
      raw_outputs = handle.runner(tg_inputs)
      outputs = {k: v.numpy() for k, v in raw_outputs.items()}
      inference_time_ms = (time.monotonic() - start) * 1000

      self._stats.tasks_completed += 1
      self._stats.total_exec_time_ms += inference_time_ms

      return InferenceResult(
          backend_type=BackendType.EGPU, model_name=model_name,
          outputs=outputs, inference_time_ms=inference_time_ms, success=True,
      )
    except Exception as e:
      self._stats.tasks_failed += 1
      logger.exception(f"eGPU inference error ({model_name})")
      return InferenceResult(
          backend_type=BackendType.EGPU, model_name=model_name,
          success=False, error_message=str(e),
      )

  # ------------------------------------------------------------------
  # Device info
  # ------------------------------------------------------------------

  def get_device_info(self) -> dict[str, Any]:
    info = super().get_device_info()
    info['device'] = 'ASM2464PD eGPU'
    info['vendor_ids'] = sorted(f"{v}:{p}" for v, p in EGPU_VID_PIDS)
    info['loaded_models'] = list(self._models.keys())
    return info

#!/usr/bin/env python3
"""USB GPU Backend — ASM2464PD-bridge eGPU + desktop AMD Radeon GPU (RDNA4).

Loads and runs .onnx models via tinygrad's ONNX frontend
(tinygrad.nn.onnx.OnnxRunner) on the USB-attached AMD GPU
(runtime/ops_amd.py's USBIface) — see exopilot's
docs/02-HARDWARE/EGPU_ASM2464PD.md for the full hardware/firmware writeup.

Not wired into any WorkloadClass tier yet: the additive-tier design
(always-loaded local model stays authoritative, eGPU loads a bigger model
on top opportunistically, soft-disable on absence or failure — matching
upstream openpilot's modeld.py load_big()/small_model pattern) needs a
real driving-model asset to route to a WorkloadClass, which doesn't exist
yet (dev/NGP10's big_driving_vision.onnx/big_driving_policy.onnx turned
out to be symlinks to the small models, not real weights — see exopilot
doc §11/§15/§16) — and hardware to verify the driving path against, which
this backend does not perform on its own. What this class does do for
real: given any .onnx file and a loaded model name, run inference on it
through the eGPU, same as any other backend in this HAL
(OnnxBackend/DeepXBackend/Hailo8Backend) — nothing routes a model to it
yet, but the class itself is not a stub.

Requires tinygrad (tinygrad.nn.onnx, tinygrad.tensor). Not currently a
dependency of this branch — dev/EOP10 has no vendored tinygrad_repo/ and
no `tinygrad` pip package, unlike dev/NGP10 which vendors a pinned
tinygrad_repo submodule. initialize() guards the import the same way
OnnxBackend guards `onnxruntime` and DeepXBackend guards `dx_engine`:
absence makes initialize() return False, same as a missing runtime
dependency for any other backend in this HAL — it is not treated as a
harder failure than that.

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

POST_FLASH_VID_PIDS = {("0xadd1", "0x0001"), ("0x3801", "0x0001")}

# The literal USB product string tinygrad's extra/usbgpu/patch.py writes into
# config1's descriptor bytes at flash time (confirmed by reading patch.py
# directly: bytes 64-90 of config1 decode to this ASCII string). Unlike
# comma's Chestnut firmware, which embeds a per-build hash
# (f"custom {CHESTNUT_FW_VERSION}-CLEAN"), tinygrad's generic firmware uses
# this fixed literal — no release process on our side to track a hash
# against, so an exact string match is the right check here.
POST_FLASH_PRODUCT = "USB 3.2 PCIe TinyEnclosure"


def _detect_usb_gpu() -> bool:
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
    if (f'0x{vendor}', f'0x{product_id}') in POST_FLASH_VID_PIDS and product_str == POST_FLASH_PRODUCT:
      return True
  return False


@dataclass
class _UsbGpuModelHandle:
  name: str
  path: str
  runner: Any  # tinygrad.nn.onnx.OnnxRunner, weights moved onto the AMD device


class UsbGpuBackend(HardwareBackend):
  """ASM2464PD-bridge eGPU backend — loads/runs .onnx models via tinygrad's ONNX frontend.

  Deliberately not registered as CAMERA_INFERENCE, VOICE_INFERENCE, or
  SAFETY_INFERENCE: no WorkloadClass routes to this backend yet (no real
  driving-model asset exists to route — see module docstring).
  """

  WORKLOAD_CLASS = None  # not yet assigned to a tier — see module docstring
  HAS_DEVICE_MEMORY = True  # discrete desktop GPU VRAM, unlike Hailo-8/DX-M1

  def __init__(self):
    super().__init__(BackendType.USB_GPU)
    self._onnx_runner_cls: Any = None
    self._models: dict[str, _UsbGpuModelHandle] = {}

  # ------------------------------------------------------------------
  # Backend lifecycle
  # ------------------------------------------------------------------

  def initialize(self) -> bool:
    """Probe for a flashed ASM2464PD and confirm tinygrad is importable."""
    if not _detect_usb_gpu():
      logger.debug("USB GPU (ASM2464PD) not detected — skipping backend")
      return False

    try:
      from tinygrad.nn.onnx import OnnxRunner
      self._onnx_runner_cls = OnnxRunner
    except ImportError:
      logger.warning("USB GPU (ASM2464PD) detected but tinygrad is not installed — backend unavailable")
      return False

    self._initialized = True
    logger.info("USB GPU (ASM2464PD) detected and tinygrad available — backend ready")
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
      logger.error("USB GPU backend not initialized")
      return False

    if config.name in self._models:
      logger.debug(f"USB GPU model already loaded: {config.name}")
      return True

    path = Path(config.path)
    if not path.is_file():
      logger.error(f"USB GPU model not found: {config.path}")
      return False

    try:
      runner = self._onnx_runner_cls(str(path)).to("AMD")
      self._models[config.name] = _UsbGpuModelHandle(name=config.name, path=str(path), runner=runner)
      config.loaded = True
      logger.info(f"Loaded USB GPU model: {config.name} ({path.stat().st_size // 1024 // 1024} MB)")
      return True
    except Exception:
      logger.exception(f"Failed to load USB GPU model '{config.name}'")
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
          backend_type=BackendType.USB_GPU, model_name=model_name,
          success=False, error_message="USB GPU backend not initialized",
      )

    handle = self._models.get(model_name)
    if handle is None:
      return InferenceResult(
          backend_type=BackendType.USB_GPU, model_name=model_name,
          success=False, error_message=f"Model not loaded on USB GPU backend: {model_name}",
      )

    try:
      from tinygrad.tensor import Tensor

      start = time.monotonic()
      # Inputs are placed on the AMD device explicitly (not via the DEV env
      # var) so this backend can't interfere with any other tinygrad device
      # this process might use — see module docstring. Guard against an
      # already-a-Tensor input (Tensor(existing_tensor) raises — tinygrad
      # has no such-case handling, unlike OnnxRunner._parse_input, which
      # this mirrors).
      tg_inputs = {k: (v.to("AMD") if isinstance(v, Tensor) else Tensor(v, device="AMD")) for k, v in inputs.items()}
      raw_outputs = handle.runner(tg_inputs)
      outputs = {k: v.numpy() for k, v in raw_outputs.items()}
      inference_time_ms = (time.monotonic() - start) * 1000

      self._stats.tasks_completed += 1
      self._stats.total_exec_time_ms += inference_time_ms

      return InferenceResult(
          backend_type=BackendType.USB_GPU, model_name=model_name,
          outputs=outputs, inference_time_ms=inference_time_ms, success=True,
      )
    except Exception as e:
      self._stats.tasks_failed += 1
      logger.exception(f"USB GPU inference error ({model_name})")
      return InferenceResult(
          backend_type=BackendType.USB_GPU, model_name=model_name,
          success=False, error_message=str(e),
      )

  # ------------------------------------------------------------------
  # Device info
  # ------------------------------------------------------------------

  def get_device_info(self) -> dict[str, Any]:
    info = super().get_device_info()
    info['device'] = 'ASM2464PD eGPU'
    info['vendor_ids'] = sorted(f"{v}:{p}" for v, p in POST_FLASH_VID_PIDS)
    info['loaded_models'] = list(self._models.keys())
    return info

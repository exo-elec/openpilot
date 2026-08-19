#!/usr/bin/env python3
"""USB GPU Backend — ASM2464PD-bridge eGPU + desktop AMD Radeon GPU (RDNA4).

Presence-detection stub only. Driven by tinygrad's extra/usbgpu backend
(runtime/ops_amd.py's USBIface) once flashed with generic ASM2464PD
firmware — see exopilot's docs/02-HARDWARE/EGPU_ASM2464PD.md for the full
hardware/firmware writeup. Not wired into any WorkloadClass tier yet: the
additive-tier design (always-loaded local model stays authoritative, eGPU
loads a bigger model on top opportunistically, soft-disable on absence or
failure — matching upstream openpilot's modeld.py load_big()/small_model
pattern) requires migrating this branch's ModelState to a pipeline shape
that can host it, which is a design task tracked separately, not something
this backend performs. This class exists so is_available() is checkable
today without any of that: it reports whether a flashed ASM2464PD is on
the bus and nothing else.

Detection: post-flash device enumerates as 0xADD1:0x0001 or 0x3801:0x0001
(tinygrad corp's own USB-GPU bridge IDs, not comma-specific — see
tinygrad/extra/usbgpu/patch.py and runtime/ops_amd.py's USBIface), with USB
product string "USB 3.2 PCIe TinyEnclosure" (the literal string patch.py
writes into the flashed firmware — checked alongside VID:PID so an
unrelated device reusing those IDs can't false-positive). The pre-flash
ROM-bootloader IDs (0x174C:0x2464, 0x174C:0x2463) are deliberately not
checked here — a ROM-state device needs flashing before it's usable as a
GPU backend, which this stub does not perform.

Typical lifecycle::

  backend = UsbGpuBackend()
  backend.initialize()               # probe for a flashed ASM2464PD
  backend.is_available()             # False until wired to a real driver
"""

from __future__ import annotations

import glob
import logging
from typing import Any

from openpilot.system.inferenced.compute import HardwareBackend, BackendType, InferenceResult

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


class UsbGpuBackend(HardwareBackend):
  """ASM2464PD-bridge eGPU backend — presence detection only, not yet an inference tier.

  Deliberately not registered as CAMERA_INFERENCE, VOICE_INFERENCE, or
  SAFETY_INFERENCE: no WorkloadClass routes to this backend yet.
  """

  WORKLOAD_CLASS = None  # not yet assigned to a tier — see module docstring
  HAS_DEVICE_MEMORY = True  # discrete desktop GPU VRAM, unlike Hailo-8/DX-M1

  def __init__(self):
    super().__init__(BackendType.USB_GPU)

  def initialize(self) -> bool:
    """Probe for a flashed ASM2464PD. Does not load tinygrad or open the device."""
    if not _detect_usb_gpu():
      logger.debug("USB GPU (ASM2464PD) not detected — skipping backend")
      return False
    logger.info("USB GPU (ASM2464PD) detected — backend is presence-only, not yet driving inference")
    self._initialized = True
    return True

  def release(self) -> None:
    self._initialized = False

  def infer(self, model_name: str, inputs: dict[str, Any]) -> InferenceResult:
    return InferenceResult(
      backend_type=BackendType.USB_GPU, model_name=model_name,
      success=False, error_message="USB GPU backend is presence-detection only — not yet wired to a driver"
    )

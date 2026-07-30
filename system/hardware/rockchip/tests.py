#!/usr/bin/env python3
"""Validation tests for RK3588 Rockchip ctypes bindings.

Run on target RK3588 Ubuntu 22.04 device:
  python3 -m openpilot.system.hardware.rockchip.tests

On non-RK3588 hosts (will skip hardware tests with warnings):
  pytest system/hardware/rockchip/tests.py
"""

from __future__ import annotations

import logging
import sys

import numpy as np

from openpilot.system.hardware.rockchip import RockchipBackendFactory
from openpilot.system.hardware.rockchip.mpp import MPPCodec, MPPDecoderConfig, MPPEncoderConfig

LOG = logging.getLogger(__name__)


def test_platform_detection() -> bool:
  print("\n[1/4] Platform Detection")
  try:
    with open('/proc/device-tree/compatible') as f:
      compat = f.read().lower()
      if 'rk3588' in compat:
        print("  ✅ RK3588 detected")
        return True
      else:
        print(f"  ⚠️  Not RK3588: {compat[:40]}")
        return False
  except Exception as e:
    print(f"  ⚠️  Detection failed: {e}")
    return False


def test_npu() -> bool:
  print("\n[2/4] NPU (RKNN)")
  backend = RockchipBackendFactory.create("rknn")
  if backend is None:
    print("  ⚠️  RKNN backend not available (librknnrt.so missing)")
    return False

  info = backend.get_device_info()
  print(f"  Library: {info.get('library_path', 'unknown')}")
  print("  ✅ NPU backend initialized")
  backend.release()
  return True


def test_rga() -> bool:
  print("\n[3/4] RGA (2D Accelerator)")
  backend = RockchipBackendFactory.create("rga")
  if backend is None:
    print("  ⚠️  RGA backend not available (librga.so missing)")
    return False

  info = backend.get_device_info()
  print(f"  Library: {info.get('library_path', 'unknown')}")

  src = np.random.randint(0, 255, (720, 1280, 3), dtype=np.uint8)

  # Resize
  dst = backend.resize(src, 640, 360)
  assert dst.shape == (360, 640, 3), f"Resize shape mismatch: {dst.shape}"
  print("  Resize: ✅")

  # Crop
  dst = backend.crop(src, 100, 100, 200, 200)
  assert dst.shape == (200, 200, 3), f"Crop shape mismatch: {dst.shape}"
  print("  Crop: ✅")

  # cvtColor
  dst = backend.cvtColor(src, "BGR")
  assert dst.shape == src.shape, f"cvtColor shape mismatch: {dst.shape}"
  print("  cvtColor: ✅")

  # Rotate
  dst = backend.rotate(src, 90)
  assert dst.shape == (1280, 720, 3), f"Rotate shape mismatch: {dst.shape}"
  print("  Rotate 90°: ✅")

  # Flip
  dst = backend.flip(src, 1)
  assert dst.shape == src.shape, f"Flip shape mismatch: {dst.shape}"
  print("  Flip: ✅")

  backend.release()
  return True


def test_mpp() -> bool:
  print("\n[4/4] MPP (Video Codec)")
  backend = RockchipBackendFactory.create("mpp")
  if backend is None:
    print("  ⚠️  MPP backend not available (librockchip_mpp.so missing)")
    return False

  info = backend.get_device_info()
  print(f"  Library: {info.get('library_path', 'unknown')}")

  # Decoder context
  ok = backend.create_decoder("test_dec", MPPDecoderConfig(codec=MPPCodec.H264))
  print(f"  H.264 decoder context: {'✅' if ok else '❌'}")

  # Encoder context
  ok = backend.create_encoder("test_enc", MPPEncoderConfig(codec=MPPCodec.H264, width=1280, height=720))
  print(f"  H.264 encoder context: {'✅' if ok else '❌'}")

  # JPEG fallback
  img = np.random.randint(0, 255, (480, 640, 3), dtype=np.uint8)
  jpeg = backend.encode_jpeg(img, quality=90)
  assert len(jpeg) > 0, "JPEG encode produced empty output"
  print(f"  JPEG encode: ✅ ({len(jpeg)} bytes)")

  decoded = backend.decode_jpeg(jpeg)
  assert decoded.shape == img.shape, f"JPEG decode shape mismatch: {decoded.shape}"
  print("  JPEG decode: ✅")

  backend.release()
  return True


def main() -> int:
  logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")
  print("=" * 60)
  print("RK3588 Hardware Validation")
  print("=" * 60)

  results = [
    ("Platform", test_platform_detection()),
    ("NPU", test_npu()),
    ("RGA", test_rga()),
    ("MPP", test_mpp()),
  ]

  print("\n" + "=" * 60)
  passed = sum(1 for _, r in results if r)
  total = len(results)
  for name, ok in results:
    print(f"  {name:12s} {'✅ PASS' if ok else '❌ FAIL'}")
  print(f"\n{passed}/{total} tests passed")

  if passed == total:
    print("\n🎉 All RK3588 hardware backends are operational!")
    return 0
  else:
    print("\n⚠️  Some backends failed. Check submodule prebuilts or vendor image.")
    return 1


if __name__ == "__main__":
  sys.exit(main())

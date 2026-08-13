"""Validation tests for RK3588 Rockchip ctypes bindings.

These tests run on any host, but tests that need real hardware or vendor
libraries are skipped automatically when the backend is not available.
"""

from __future__ import annotations

import pytest

from openpilot.system.hardware.rockchip import RockchipBackendFactory
from openpilot.system.hardware.rockchip.mpp import MPPCodec, MPPDecoderConfig, MPPEncoderConfig
import numpy as np


def _is_rk3588() -> bool:
  try:
    with open('/proc/device-tree/compatible') as f:
      return 'rk3588' in f.read().lower()
  except OSError:
    return False


@pytest.mark.skipif(not _is_rk3588(), reason="Not running on an RK3588 device")
def test_platform_detection() -> None:
  """Verify the device tree reports an RK3588 SoC."""
  with open('/proc/device-tree/compatible') as f:
    compat = f.read().lower()
  assert 'rk3588' in compat, f"expected rk3588 in compatible, got {compat[:40]!r}"


@pytest.fixture
def rknn_backend():
  backend = RockchipBackendFactory.create("rknn")
  if backend is None:
    pytest.skip("RKNN backend not available (librknnrt.so missing)")
  yield backend
  backend.release()


def test_npu(rknn_backend) -> None:
  """Verify the RKNN backend can report device info."""
  info = rknn_backend.get_device_info()
  assert "library_path" in info


@pytest.fixture
def rga_backend():
  backend = RockchipBackendFactory.create("rga")
  if backend is None:
    pytest.skip("RGA backend not available (librga.so missing)")
  yield backend
  backend.release()


def test_rga_resize(rga_backend) -> None:
  src = np.random.randint(0, 255, (720, 1280, 3), dtype=np.uint8)
  dst = rga_backend.resize(src, 640, 360)
  assert dst.shape == (360, 640, 3)


def test_rga_crop(rga_backend) -> None:
  src = np.random.randint(0, 255, (720, 1280, 3), dtype=np.uint8)
  dst = rga_backend.crop(src, 100, 100, 200, 200)
  assert dst.shape == (200, 200, 3)


def test_rga_cvtcolor(rga_backend) -> None:
  src = np.random.randint(0, 255, (720, 1280, 3), dtype=np.uint8)
  dst = rga_backend.cvtColor(src, "BGR")
  assert dst.shape == src.shape


def test_rga_rotate(rga_backend) -> None:
  src = np.random.randint(0, 255, (720, 1280, 3), dtype=np.uint8)
  dst = rga_backend.rotate(src, 90)
  assert dst.shape == (1280, 720, 3)


def test_rga_flip(rga_backend) -> None:
  src = np.random.randint(0, 255, (720, 1280, 3), dtype=np.uint8)
  dst = rga_backend.flip(src, 1)
  assert dst.shape == src.shape


@pytest.fixture
def mpp_backend():
  backend = RockchipBackendFactory.create("mpp")
  if backend is None:
    pytest.skip("MPP backend not available (librockchip_mpp.so missing)")
  yield backend
  backend.release()


def test_mpp_decoder_context(mpp_backend) -> None:
  ok = mpp_backend.create_decoder("test_dec", MPPDecoderConfig(codec=MPPCodec.H264))
  assert ok


def test_mpp_encoder_context(mpp_backend) -> None:
  ok = mpp_backend.create_encoder("test_enc", MPPEncoderConfig(codec=MPPCodec.H264, width=1280, height=720))
  assert ok


def test_mpp_jpeg_roundtrip(mpp_backend) -> None:
  img = np.random.randint(0, 255, (480, 640, 3), dtype=np.uint8)
  jpeg = mpp_backend.encode_jpeg(img, quality=90)
  assert len(jpeg) > 0
  decoded = mpp_backend.decode_jpeg(jpeg)
  assert decoded.shape == img.shape

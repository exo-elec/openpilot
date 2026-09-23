"""Tests for rknn_platform.py's platform detection and NPU core allocation,
covering RK3588. No hardware required — uses the
RKNN_PLATFORM environment-variable override that detect_platform()
already supports for testing.
"""

import os

import pytest

from openpilot.selfdrive.modeld.runners.rknn_platform import (
  PlatformType, detect_platform, get_core_count, get_core_mask,
  NPUPlatformConfig, NPU_ALLOCATION_MAP,
)


@pytest.fixture(autouse=True)
def _clear_platform_env():
  old = os.environ.pop('RKNN_PLATFORM', None)
  yield
  if old is not None:
    os.environ['RKNN_PLATFORM'] = old
  else:
    os.environ.pop('RKNN_PLATFORM', None)


def test_detect_platform_rk3588_via_env():
  os.environ['RKNN_PLATFORM'] = 'rk3588'
  assert detect_platform() == PlatformType.RK3588


def test_detect_platform_unknown_when_unset_and_no_device_tree():
  # No env override, and this dev machine has no /proc/device-tree.
  assert detect_platform() == PlatformType.UNKNOWN


def test_core_count_rk3588_is_three():
  assert get_core_count(PlatformType.RK3588) == 3


def test_core_count_unknown_defaults_to_three_for_safety():
  assert get_core_count(PlatformType.UNKNOWN) == 3


def test_get_core_mask_rk3588_uses_real_allocation_not_fallback():
  # RK3588's fallback allocation map (_FallbackNpuTuning, used when hal isn't
  # installed) assigns driving_vision to core 1 and stereod to core 2 --
  # different values, proving this isn't just the same fallback-to-1 path.
  assert get_core_mask(PlatformType.RK3588, 'driving_vision') == 1
  assert get_core_mask(PlatformType.RK3588, 'stereod') == 2


def test_npu_platform_config_rk3588_core_availability():
  cfg = NPUPlatformConfig(PlatformType.RK3588)
  assert cfg.core_count == 3
  assert cfg.is_core_available(2) is True
  assert cfg.is_core_available(3) is False
  assert cfg.is_rk3588 is True

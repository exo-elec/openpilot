"""Tests for rknn_platform.py's platform detection and NPU core allocation,
covering both RK3588 and RK3576. No hardware required — uses the
VISIONPILOT_PLATFORM environment-variable override that detect_platform()
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
  old = os.environ.pop('VISIONPILOT_PLATFORM', None)
  yield
  if old is not None:
    os.environ['VISIONPILOT_PLATFORM'] = old
  else:
    os.environ.pop('VISIONPILOT_PLATFORM', None)


def test_detect_platform_rk3588_via_env():
  os.environ['VISIONPILOT_PLATFORM'] = 'rk3588'
  assert detect_platform() == PlatformType.RK3588


def test_detect_platform_rk3576_via_env():
  os.environ['VISIONPILOT_PLATFORM'] = 'rk3576'
  assert detect_platform() == PlatformType.RK3576


def test_detect_platform_unknown_when_unset_and_no_device_tree():
  # No env override, and this dev machine has no /proc/device-tree.
  assert detect_platform() == PlatformType.UNKNOWN


def test_core_count_rk3588_is_three():
  assert get_core_count(PlatformType.RK3588) == 3


def test_core_count_rk3576_is_two():
  assert get_core_count(PlatformType.RK3576) == 2


def test_core_count_unknown_defaults_to_three_for_safety():
  assert get_core_count(PlatformType.UNKNOWN) == 3


def test_rk3576_has_no_per_task_allocation_yet():
  """Documents the current state: RK3576 has no hal.tuning.npu data, so its
  allocation map entry is an empty dict, not missing entirely (missing would
  fall through to RK3588's map via get_core_mask's .get() default, which
  would be wrong -- RK3588's core indices/masks don't all fit on a 2-core
  chip)."""
  assert NPU_ALLOCATION_MAP[PlatformType.RK3576] == {}


def test_get_core_mask_rk3576_falls_back_to_core_one_for_every_task():
  for task in ('modeld', 'driving_vision', 'stereod', 'monod', 'policy'):
    assert get_core_mask(PlatformType.RK3576, task) == 1


def test_get_core_mask_rk3588_uses_real_allocation_not_fallback():
  # RK3588's fallback allocation map (_FallbackNpuTuning, used when hal isn't
  # installed) assigns driving_vision to core 1 and stereod to core 2 --
  # different values, proving this isn't just the same fallback-to-1 path.
  assert get_core_mask(PlatformType.RK3588, 'driving_vision') == 1
  assert get_core_mask(PlatformType.RK3588, 'stereod') == 2


def test_npu_platform_config_rk3576_core_availability():
  cfg = NPUPlatformConfig(PlatformType.RK3576)
  assert cfg.core_count == 2
  assert cfg.is_core_available(0) is True
  assert cfg.is_core_available(1) is True
  assert cfg.is_core_available(2) is False, "RK3576 only has 2 NPU cores"
  assert cfg.is_rk3588 is False


def test_npu_platform_config_rk3588_core_availability():
  cfg = NPUPlatformConfig(PlatformType.RK3588)
  assert cfg.core_count == 3
  assert cfg.is_core_available(2) is True
  assert cfg.is_core_available(3) is False
  assert cfg.is_rk3588 is True

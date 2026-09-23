"""Tests for rknn_platform.py's platform detection and NPU core allocation,
covering RK3576. No hardware required — uses the
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


def test_detect_platform_rk3576_via_env():
  os.environ['RKNN_PLATFORM'] = 'rk3576'
  assert detect_platform() == PlatformType.RK3576


def test_detect_platform_unknown_when_unset_and_no_device_tree():
  # No env override, and this dev machine has no /proc/device-tree.
  assert detect_platform() == PlatformType.UNKNOWN


def test_core_count_rk3576_is_two():
  assert get_core_count(PlatformType.RK3576) == 2


def test_core_count_unknown_defaults_conservatively():
  # An unknown platform must not be told it has more cores than it might.
  assert get_core_count(PlatformType.UNKNOWN) <= 3


def test_rk3576_has_no_per_task_allocation_yet():
  """Documents the current state: RK3576 has no hal.tuning.npu data, so its
  allocation map entry is an empty dict. get_core_mask() then falls through to
  core 0 for every task -- correct and slow, rather than borrowing another
  board's core indices, which need not exist on this silicon."""
  assert NPU_ALLOCATION_MAP[PlatformType.RK3576] == {}


def test_get_core_mask_rk3576_falls_back_to_core_one_for_every_task():
  for task in ('modeld', 'driving_vision', 'stereod', 'monod', 'policy'):
    assert get_core_mask(PlatformType.RK3576, task) == 1


def test_npu_platform_config_rk3576_core_availability():
  cfg = NPUPlatformConfig(PlatformType.RK3576)
  assert cfg.core_count == 2
  assert cfg.is_core_available(0) is True
  assert cfg.is_core_available(1) is True
  assert cfg.is_core_available(2) is False, "RK3576 only has 2 NPU cores"
  assert cfg.is_rk3576 is True



"""Tests for rknn_platform.py's platform detection and NPU core allocation,
covering RK3588 and RK3576. No hardware required — uses the
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


def test_get_core_mask_rk3576_uses_computed_allocation_not_fallback():
  # RK3576's allocation is computed by npu_pack.py's LPT packer (no
  # per-platform hand-authored table), the same function RK3588 uses --
  # driving_vision and stereod land on different cores, proving this isn't
  # just the fallback-to-core-0 path.
  assert get_core_mask(PlatformType.RK3576, 'driving_vision') == 1
  assert get_core_mask(PlatformType.RK3576, 'stereod') == 2


def test_rk3576_aliases_share_one_core_with_their_canonical_task():
  assert get_core_mask(PlatformType.RK3576, 'modeld') == get_core_mask(PlatformType.RK3576, 'driving_vision')
  assert get_core_mask(PlatformType.RK3576, 'monod') == get_core_mask(PlatformType.RK3576, 'mono_detect')


def test_rk3576_masks_name_only_existing_cores():
  # A mask naming core 2 (0x4) on a 2-core NPU is what gridd's YOLO used to do.
  assert all(m in (1, 2) for m in NPU_ALLOCATION_MAP[PlatformType.RK3576].values())


def test_unknown_task_or_platform_gets_core0():
  assert get_core_mask(PlatformType.RK3576, 'not_a_task') == 1
  assert get_core_mask(PlatformType.UNKNOWN, 'modeld') == 1


def test_npu_platform_config_rk3576_core_availability():
  cfg = NPUPlatformConfig(PlatformType.RK3576)
  assert cfg.core_count == 2
  assert cfg.is_core_available(0) is True
  assert cfg.is_core_available(1) is True
  assert cfg.is_core_available(2) is False, "RK3576 only has 2 NPU cores"
  assert cfg.is_rk3576 is True


@pytest.mark.parametrize('platform, cores', [(PlatformType.RK3588, 3), (PlatformType.RK3576, 2)])
def test_masks_are_valid_for_each_physical_soc(platform, cores):
  assert get_core_count(platform) == cores
  assert all(0 < mask < (1 << cores) for mask in NPU_ALLOCATION_MAP[platform].values())
  config = NPUPlatformConfig(platform)
  assert config.core_count == cores
  assert not config.is_core_available(cores)


def test_rk3588_env_and_aliases_remain_supported():
  os.environ['RKNN_PLATFORM'] = 'rk3588s2'
  assert detect_platform() == PlatformType.RK3588
  assert get_core_mask(PlatformType.RK3588, 'modeld') == get_core_mask(PlatformType.RK3588, 'driving_vision')

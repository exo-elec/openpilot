"""Shared RKNN detection and task packing for RK3588 and RK3576."""

from __future__ import annotations

import os
from pathlib import Path
from enum import Enum

from openpilot.selfdrive.modeld.runners.npu_pack import NPUTaskGroup, pack_tasks_to_cores

from openpilot.system.hardware import HARDWARE

npu_tuning = HARDWARE.hal_import("tuning.npu")
if npu_tuning is None:
    # hal not installed (dev PC) — fall back to the stock allocation
    # documented under NPUPlatformConfig below. Values mirror hal.tuning.npu;
    # no NPU exists here anyway, so this only keeps imports/tests working.
    class _FallbackNpuTuning:
        CORE_ALLOCATION: dict[str, int] = {}
        TOPS_PER_CORE = 3.0
        UTILIZATION_SAFETY_LIMIT = 0.85
        TASK_TOPS = {
            "modeld": 2.0, "driving_vision": 2.0, "stereod": 1.4, "stereo_seg": 1.4,
            "monod": 0.9, "mono_detect": 0.9, "policy": 0.5, "yolo": 0.4,
            "ppliteseg": 0.3, "autospeed": 0.6, "scene3d": 0.25, "domainseg": 0.2,
        }

    npu_tuning = _FallbackNpuTuning()


class PlatformType(Enum):
    """Supported Rockchip platforms."""
    RK3588 = "rk3588"
    RK3576 = "rk3576"      # 2 NPU cores × 3 TOPS
    UNKNOWN = "unknown"


def get_core_count(platform: PlatformType) -> int:
    """Get NPU core count for platform."""
    core_counts = {
        PlatformType.RK3588: 3,
        PlatformType.RK3576: 2,
        PlatformType.UNKNOWN: 3,  # Default to 3 for safety
    }
    return core_counts.get(platform, 3)


# Physical NPU workloads. Some daemons look up the same physical model under
# more than one name (a split driving runner's vision half is queried as
# both "modeld" and "driving_vision"); grouping aliases here means the
# packer below counts each workload's TOPS once, and every alias always
# lands on the same core. Values are hal.tuning.npu's estimates -- see
# npu_bench.py, which replaces them with real per-model measurements.
# Identical to dev/01M's list -- the workload set is the same across
# boards, only the hardware constants below differ.
_TASK_GROUPS: tuple[NPUTaskGroup, ...] = (
    NPUTaskGroup(("modeld", "driving_vision"), npu_tuning.TASK_TOPS.get("modeld", 2.0)),
    NPUTaskGroup(("stereod", "stereo_seg"), npu_tuning.TASK_TOPS.get("stereod", 1.4)),
    NPUTaskGroup(("monod", "mono_detect"), npu_tuning.TASK_TOPS.get("monod", 0.9)),
    NPUTaskGroup(("policy",), npu_tuning.TASK_TOPS.get("policy", 0.5)),
    NPUTaskGroup(("autospeed",), npu_tuning.TASK_TOPS.get("autospeed", 0.6)),
    NPUTaskGroup(("yolo",), npu_tuning.TASK_TOPS.get("yolo", 0.4)),
    NPUTaskGroup(("ppliteseg",), npu_tuning.TASK_TOPS.get("ppliteseg", 0.3)),
    NPUTaskGroup(("scene3d",), npu_tuning.TASK_TOPS.get("scene3d", 0.25)),
    NPUTaskGroup(("domainseg",), npu_tuning.TASK_TOPS.get("domainseg", 0.2)),
)

# Model-to-core allocation. hal.tuning.npu.CORE_ALLOCATION is real, measured
# data (see npu_bench.py) when present and non-empty, and always wins over a
# computed guess. Otherwise this board-agnostic packer bin-packs the
# estimated TASK_TOPS onto this platform's real core count -- the same
# function every ExoPilot board calls, so no board needs its own
# hand-authored allocation table. Replaces the old VisionPilot-borrowed
# split (core 0 = driving+policy, core 1 = everything else), which put
# ~4.05 estimated TOPS on core 1 against a 2.55 TOPS/core budget.
def _allocation(platform):
    allocation = getattr(npu_tuning, 'CORE_ALLOCATION', {})
    cores = get_core_count(platform)
    # A measured HAL map applies only to the HAL's actual device, never the other SoC.
    actual = HARDWARE.get_device_type()
    if actual == platform.value and allocation:
        if all(isinstance(mask, int) and 0 < mask < (1 << cores) for mask in allocation.values()):
            return allocation
    return pack_tasks_to_cores(_TASK_GROUPS, cores)


NPU_ALLOCATION_MAP = {platform: _allocation(platform)
                      for platform in (PlatformType.RK3588, PlatformType.RK3576)}


def detect_platform() -> PlatformType:
    """Detect Rockchip SoC from device tree compatible string.

    Checks /proc/device-tree/compatible for platform identification.
    Also supports the RKNN_PLATFORM environment variable for testing.
    """
    # Allow environment override for testing
    env_platform = os.environ.get('RKNN_PLATFORM', '').lower()
    if 'rk3588' in env_platform:
        return PlatformType.RK3588
    if 'rk3576' in env_platform:
        return PlatformType.RK3576

    # Check device tree
    compat_path = Path('/proc/device-tree/compatible')
    if compat_path.exists():
        try:
            compat = compat_path.read_bytes().decode('utf-8', errors='ignore').lower()
            if 'rk3588' in compat:
                return PlatformType.RK3588
            if 'rk3576' in compat:
                return PlatformType.RK3576
        except Exception:
            pass

    return PlatformType.UNKNOWN


def rknn_soc_tag() -> str:
    """The SoC tag that appears in RKNN artifact filenames.

    An RKNN binary is compiled for one SoC and must never be loaded on
    another (CLAUDE.md, "Never reuse an RKNN binary across target SoCs"), so
    model search paths are built from the running board's tag. A board with
    no matching artifact then finds nothing -- which is the correct outcome,
    and far better than silently loading the other board's binary.

    Returns "unknown" off-device, where there is no NPU to load into anyway.
    """
    return detect_platform().value


def get_core_mask(platform: PlatformType, task: str) -> int:
    """Get appropriate NPU core mask for task on given platform.

    Example:
        >>> platform = detect_platform()
        >>> mask = get_core_mask(platform, 'monod')
    """
    # An unknown platform or task gets RKNN_NPU_CORE_0 rather than a borrowed
    # map: slow but always valid, where another board's map may name cores
    # that do not exist on this silicon.
    allocation = NPU_ALLOCATION_MAP.get(platform, {})
    return allocation.get(task, 1)  # 1 = RKNN_NPU_CORE_0


class NPUPlatformConfig:
    """Configuration for NPU allocation on current platform.

    NPU Budget Strategy (85% safety limit):
    - RK3576: 2 cores x 3 TOPS = 6 TOPS total, 2.55 TOPS/core budget.
      Per-task placement is computed (NPU_ALLOCATION_MAP, npu_pack.py's LPT
      packer over hal.tuning.npu's estimated TASK_TOPS), not hand-assigned;
      run npu_bench.py on real hardware to replace the estimate with a
      measured CORE_ALLOCATION, which then overrides the computed one.
    """

    def __init__(self, platform: PlatformType | None = None):
        """Initialize config for platform (auto-detect if not specified)."""
        self.platform = platform or detect_platform()
        self.core_count = get_core_count(self.platform)

    def get_core_mask(self, task: str) -> int:
        """Get core mask for a task."""
        return get_core_mask(self.platform, task)

    def is_core_available(self, core_id: int) -> bool:
        """Check if a core ID is valid for this platform."""
        return 0 <= core_id < self.core_count

    @property
    def is_rk3588(self) -> bool:
        return self.platform == PlatformType.RK3588

    @property
    def is_rk3576(self) -> bool:
        """True if running on RK3576 (ExoPilot 02M)."""
        return self.platform == PlatformType.RK3576


def get_platform_npu_config() -> NPUPlatformConfig:
    """Get NPU configuration for current platform.

    This is the main entry point for platform-aware NPU allocation.

    Example:
        >>> config = get_platform_npu_config()
        >>> print(f"Platform: {config.platform.value}, Cores: {config.core_count}")
        >>> mask = config.get_core_mask('monod')
    """
    return NPUPlatformConfig()

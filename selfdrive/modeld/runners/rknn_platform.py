"""RKNN Platform Detection and NPU Core Allocation for RK3576.

RK3576 (ExoPilot 02M): 6 TOPS = 2 NPU cores x 3 TOPS/core, 85% budget =
2.55 TOPS/core.

Per-task core allocation (NPU_ALLOCATION_MAP) follows VisionPilot's RK3576
budget (visionpilot src/common/common/constants.py, NPU_CORE_0/1_ALLOCATION;
perception_inference NPU_ALLOCATION): core 0 = driving model + policy
(2.5 TOPS), core 1 = all perception (2.4 TOPS), each under the 85% line.
That split is VisionPilot's model set; per-task TOPS for this branch's
models have not been measured on RK3576 -- measure before moving tasks.

Values are RKNN core MASKS (RKNN_NPU_CORE_0 = 1, CORE_1 = 2), passed as
ModelConfig.npu_cores straight to RKNNLite.init_runtime(core_mask=...).

This branch supports 02M hardware only -- RK3588 (ExoPilot 01M) lives on
dev/01M, see the branch model in CLAUDE.md.
"""

from __future__ import annotations

import os
from pathlib import Path
from enum import Enum

try:
    from hal.tuning import npu as npu_tuning
except ImportError:
    # hal not installed (dev PC) — fall back to the stock allocation
    # documented under NPUPlatformConfig below. Values mirror hal.tuning.npu;
    # no NPU exists here anyway, so this only keeps imports/tests working.
    class _FallbackNpuTuning:
        CORE_ALLOCATION = {
            "modeld": 1, "driving_vision": 1,
            "stereod": 2, "stereo_seg": 2, "yolo": 2, "ppliteseg": 2,
            "domainseg": 2, "scene3d": 2,
            "monod": 4, "mono_detect": 4, "policy": 4, "autospeed": 4,
        }
        TOPS_PER_CORE = 2.0
        UTILIZATION_SAFETY_LIMIT = 0.85
        TASK_TOPS = {
            "modeld": 2.0, "driving_vision": 2.0, "stereod": 1.4, "stereo_seg": 1.4,
            "monod": 0.9, "mono_detect": 0.9, "policy": 0.5, "yolo": 0.4,
            "ppliteseg": 0.3, "autospeed": 0.6, "scene3d": 0.25, "domainseg": 0.2,
        }

    npu_tuning = _FallbackNpuTuning()


class PlatformType(Enum):
    """Supported Rockchip platforms."""
    RK3576 = "rk3576"      # 2 NPU cores × 3 TOPS
    UNKNOWN = "unknown"


RKNN_NPU_CORE_AUTO = 0
RKNN_NPU_CORE_0 = 1
RKNN_NPU_CORE_1 = 2

# RK3576 (2 cores): VisionPilot's split, see module docstring.
_RK3576_ALLOCATION = {
    # Core 0: driving model + policy (VisionPilot: 2.5 TOPS)
    "modeld": RKNN_NPU_CORE_0,
    "driving_vision": RKNN_NPU_CORE_0,
    "policy": RKNN_NPU_CORE_0,
    # Core 1: all perception (VisionPilot: 2.4 TOPS)
    "stereod": RKNN_NPU_CORE_1,
    "stereo_seg": RKNN_NPU_CORE_1,
    "yolo": RKNN_NPU_CORE_1,
    "ppliteseg": RKNN_NPU_CORE_1,
    "domainseg": RKNN_NPU_CORE_1,
    "scene3d": RKNN_NPU_CORE_1,
    "monod": RKNN_NPU_CORE_1,
    "mono_detect": RKNN_NPU_CORE_1,
    "autospeed": RKNN_NPU_CORE_1,
}

NPU_ALLOCATION_MAP: dict[PlatformType, dict[str, int]] = {
    PlatformType.RK3576: _RK3576_ALLOCATION,
}


def detect_platform() -> PlatformType:
    """Detect Rockchip SoC from device tree compatible string.

    Checks /proc/device-tree/compatible for platform identification.
    Also supports the RKNN_PLATFORM environment variable for testing.
    """
    # Allow environment override for testing
    env_platform = os.environ.get('RKNN_PLATFORM', '').lower()
    if 'rk3576' in env_platform:
        return PlatformType.RK3576

    # Check device tree
    compat_path = Path('/proc/device-tree/compatible')
    if compat_path.exists():
        try:
            compat = compat_path.read_bytes().decode('utf-8', errors='ignore').lower()
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


def get_core_count(platform: PlatformType) -> int:
    """Get NPU core count for platform."""
    core_counts = {
        PlatformType.RK3576: 2,
        PlatformType.UNKNOWN: 3,  # Default to 3 for safety
    }
    return core_counts.get(platform, 3)


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
    return allocation.get(task, RKNN_NPU_CORE_0)


class NPUPlatformConfig:
    """Configuration for NPU allocation on current platform.

    NPU Budget Strategy (85% safety limit):
    - RK3576: 2 cores × 3 TOPS = 6 TOPS total
      * Per-core budget: 3.0 × 0.85 = 2.55 TOPS (safe)
      * Core 0: driving model + policy; core 1: perception (VisionPilot's
        split, NPU_ALLOCATION_MAP). Per-task TOPS still to be measured on 02M.
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

"""RKNN Platform Detection and NPU Core Allocation for RK3588.

This module provides NPU core mask allocation for RK3588 (3 NPU cores).

RK3588: 6 TOPS = 3 NPU cores × 2 TOPS/core, 85% budget = 1.7 TOPS/core

Strategy: Maximize NPU utilization up to 85% safety limit per core.
Core 0 is not exclusive - models can share if budget permits.

Usage:
    from openpilot.selfdrive.modeld.runners.rknn_platform import get_platform_npu_config

    config = get_platform_npu_config()
    core_mask = config.get_core_mask('modeld')  # Returns appropriate core for platform
"""

from __future__ import annotations

import os
from pathlib import Path
from dataclasses import dataclass
from enum import Enum

try:
    from hal.tuning import npu as npu_tuning
except ImportError:
    # hal not installed (dev PC) — fall back to the stock RK3588 allocation
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
    RK3588 = "rk3588"      # 3 NPU cores × 2 TOPS (RK3588 and RK3588S2 share same NPU)
    UNKNOWN = "unknown"


class NPUMask(Enum):
    """NPU core mask values (from rknn_api.h).

    These are the actual values expected by RKNNLite.init_runtime().
    """
    AUTO = 0        # RKNN_NPU_CORE_AUTO
    CORE_0 = 1      # RKNN_NPU_CORE_0
    CORE_1 = 2      # RKNN_NPU_CORE_1
    CORE_2 = 4      # RKNN_NPU_CORE_2
    CORE_0_1 = 3    # RKNN_NPU_CORE_0 | RKNN_NPU_CORE_1
    CORE_0_1_2 = 7  # RKNN_NPU_CORE_0 | RKNN_NPU_CORE_1 | RKNN_NPU_CORE_2


# Model-to-core allocation and per-model TOPS tuning data lives in the closed
# hal package (hal.tuning.npu) — this is ExoPilot's model-set-specific tuning,
# not generic RK3588 platform logic.
NPU_ALLOCATION_MAP: dict[PlatformType, dict[str, int]] = {
    PlatformType.RK3588: npu_tuning.CORE_ALLOCATION,
}


def detect_platform() -> PlatformType:
    """Detect Rockchip SoC from device tree compatible string.

    Checks /proc/device-tree/compatible for platform identification.
    Also supports VISIONPILOT_PLATFORM environment variable for testing.

    Returns:
        PlatformType enum value
    """
    # Allow environment override for testing
    env_platform = os.environ.get('VISIONPILOT_PLATFORM', '').lower()
    if 'rk3588' in env_platform:
        return PlatformType.RK3588

    # Check device tree
    compat_path = Path('/proc/device-tree/compatible')
    if compat_path.exists():
        try:
            compat = compat_path.read_bytes().decode('utf-8', errors='ignore').lower()
            if 'rk3588' in compat:
                return PlatformType.RK3588
        except Exception:
            pass

    return PlatformType.UNKNOWN


def get_core_count(platform: PlatformType) -> int:
    """Get NPU core count for platform.

    Args:
        platform: Platform type

    Returns:
        Number of NPU cores
    """
    core_counts = {
        PlatformType.RK3588: 3,
        PlatformType.UNKNOWN: 3,  # Default to 3 for safety
    }
    return core_counts.get(platform, 3)


def get_core_mask(platform: PlatformType, task: str) -> int:
    """Get appropriate NPU core mask for task on given platform.
    
    This is the main function for platform-aware NPU allocation.
    
    Args:
        platform: Target platform
        task: Task name (e.g., 'modeld', 'stereod', 'monod')
        
    Returns:
        NPU core mask value for RKNNLite.init_runtime()
        
    Example:
        >>> platform = detect_platform()
        >>> mask = get_core_mask(platform, 'monod')
        >>> # On RK3588: returns 4 (CORE_2)
    """
    allocation = NPU_ALLOCATION_MAP.get(platform, NPU_ALLOCATION_MAP[PlatformType.RK3588])
    return allocation.get(task, NPUMask.CORE_0.value)


def validate_core_mask(platform: PlatformType, requested_mask: int) -> int:
    """Validate a core mask for platform.

    RK3588 has 3 cores, so any mask is valid.

    Args:
        platform: Target platform
        requested_mask: Requested core mask value

    Returns:
        Valid core mask for the platform
    """
    return requested_mask


def get_available_cores(platform: PlatformType) -> list[int]:
    """Get list of available NPU core IDs for platform.
    
    Args:
        platform: Target platform
        
    Returns:
        List of core IDs (e.g., [0, 1] or [0, 1, 2])
    """
    core_count = get_core_count(platform)
    return list(range(core_count))


@dataclass
class NPUPlatformConfig:
    """Configuration for NPU allocation on current platform.

    Provides a convenient interface for getting platform-aware core masks.

    NPU Budget Strategy (85% safety limit):
    - RK3588: 3 cores × 2 TOPS = 6 TOPS total
      * Per-core budget: 2.0 × 0.85 = 1.7 TOPS (safe)
      * Core 0: modeld (2.0 TOPS) - at limit, minimal sharing
      * Core 1: stereod (1.4 TOPS) - 0.3 TOPS headroom for sharing
      * Core 2: monod+policy (1.5 TOPS) - 0.2 TOPS headroom
    """
    platform: PlatformType
    core_count: int

    # Safety limit for NPU utilization (avoid thermal/performance issues)
    UTILIZATION_SAFETY_LIMIT: float = npu_tuning.UTILIZATION_SAFETY_LIMIT

    # TOPS per core by platform
    TOPS_PER_CORE: dict[PlatformType, float] = None

    def __post_init__(self):
        """Initialize TOPS_PER_CORE after dataclass init."""
        if self.TOPS_PER_CORE is None:
            object.__setattr__(
                self, 'TOPS_PER_CORE',
                {
                    PlatformType.RK3588: npu_tuning.TOPS_PER_CORE,
                    PlatformType.UNKNOWN: npu_tuning.TOPS_PER_CORE,
                }
            )
    
    def __init__(self, platform: PlatformType | None = None):
        """Initialize config for platform (auto-detect if not specified)."""
        self.platform = platform or detect_platform()
        self.core_count = get_core_count(self.platform)
        self.TOPS_PER_CORE = None
        self.__post_init__()

    def get_core_mask(self, task: str) -> int:
        """Get core mask for a task."""
        return get_core_mask(self.platform, task)
    
    def validate_mask(self, mask: int) -> int:
        """Validate a core mask for this platform."""
        return validate_core_mask(self.platform, mask)
    
    def get_available_cores(self) -> list[int]:
        """Get list of available core IDs."""
        return get_available_cores(self.platform)
    
    def is_core_available(self, core_id: int) -> bool:
        """Check if a core ID is valid for this platform."""
        return 0 <= core_id < self.core_count
    
    @property
    def is_rk3588(self) -> bool:
        """True if running on RK3588 (including S2)."""
        return self.platform == PlatformType.RK3588
    
    def get_tops_per_core(self) -> float:
        """Get TOPS capacity per core for this platform."""
        return self.TOPS_PER_CORE.get(self.platform, 2.0)
    
    def get_safe_budget_tops(self) -> float:
        """Get safe TOPS budget per core (85% of capacity).
        
        Returns:
            Safe TOPS budget per core at 85% utilization
        """
        return self.get_tops_per_core() * self.UTILIZATION_SAFETY_LIMIT
    
    def can_fit_on_core(self, model_tops: float, current_utilization: float = 0.0) -> bool:
        """Check if a model can fit on a core within safe budget.
        
        Args:
            model_tops: TOPS requirement of the model
            current_utilization: Current TOPS already allocated on core
            
        Returns:
            True if model fits within 85% safety limit
        """
        safe_budget = self.get_safe_budget_tops()
        return (current_utilization + model_tops) <= safe_budget
    
    def get_core_headroom_tops(self, current_utilization: float = 0.0) -> float:
        """Get remaining TOPS headroom on a core.
        
        Args:
            current_utilization: Current TOPS already allocated on core
            
        Returns:
            Remaining safe TOPS budget on core
        """
        safe_budget = self.get_safe_budget_tops()
        return max(0.0, safe_budget - current_utilization)
    
    def recommend_sharing(self, task: str) -> bool:
        """Recommend whether a task can share its core with others.
        
        Based on 85% safety limit and typical model TOPS requirements.
        
        Args:
            task: Task name (e.g., 'modeld', 'monod', 'stereod')
            
        Returns:
            True if sharing is recommended (has headroom)
        """
        model_tops = npu_tuning.TASK_TOPS.get(task, 1.0)
        safe_budget = self.get_safe_budget_tops()
        
        # Model can share if it doesn't use full budget alone
        return model_tops < safe_budget


def get_platform_npu_config() -> NPUPlatformConfig:
    """Get NPU configuration for current platform.
    
    This is the main entry point for platform-aware NPU allocation.
    
    Returns:
        NPUPlatformConfig for the detected platform
        
    Example:
        >>> config = get_platform_npu_config()
        >>> print(f"Platform: {config.platform.value}, Cores: {config.core_count}")
        >>> mask = config.get_core_mask('monod')
    """
    return NPUPlatformConfig()


# Convenience function for direct core mask lookup
def get_task_core_mask(task: str, platform: str | None = None) -> int:
    """Get core mask for a task, optionally specifying platform.
    
    Args:
        task: Task name (e.g., 'modeld', 'monod', 'stereod')
        platform: Optional platform name override ('rk3588', etc.)
        
    Returns:
        Core mask value for RKNNLite.init_runtime()
    """
    if platform:
        plat = PlatformType(platform)
    else:
        plat = detect_platform()
    return get_core_mask(plat, task)


# Backward compatibility alias
get_npu_core_mask = get_task_core_mask

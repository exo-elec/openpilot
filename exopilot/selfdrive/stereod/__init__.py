# stereod - Stereo Depth Daemon
from exopilot.selfdrive.stereod.stereod import StereoD, main

# SGM implementation (unified ACL-based)
from exopilot.selfdrive.stereod.sgm import (
    SGM,
    SGMConfig,
    SGMResult,
    SGMError,
    SGMDeviceError,
    SGMTimeoutError,
    compute_disparity as compute_disparity_acl,
)

__all__ = [
    'StereoD',
    'main',
    # Unified SGM
    'SGM',
    'SGMConfig',
    'SGMResult',
    'SGMError',
    'SGMDeviceError',
    'SGMTimeoutError',
    'compute_disparity_acl',
]

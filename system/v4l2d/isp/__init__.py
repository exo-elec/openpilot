"""RKIAQ ISP integration for Rockchip platforms.

Provides hardware 3A (AE/AWB/AF) via RKIAQ library bindings.
"""

from openpilot.system.v4l2d.isp.rkiaq_wrapper import RKIAQWrapper, ISPMetadata

__all__ = [
    "RKIAQWrapper",
    "ISPMetadata",
]

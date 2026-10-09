#!/usr/bin/env python3
"""Compatibility exports for ExoPilot's hardware adapter.

The product-specific platform selection lives in ``nagaspilot.hardware``;
shared openpilot callers keep importing this stable path. Exports are lazy
because the product adapter itself depends on ``system.hardware.base``.
"""

__all__ = [
    'HARDWARE', 'HardwareBase', 'HardwareCapability', 'PlatformRegistry',
    'RK3588', 'RK3588_DETECTED', 'RK3588Hardware',
    'RK3576', 'RK3576_DETECTED', 'RK3576Hardware', 'ROCKCHIP', 'TICI',
    'PC', 'HAS_SPEAKER', 'HAS_VOICE_INPUT', 'HAS_SIDE_CAMERAS', 'HAS_REAR_CAMERA',
]


def __getattr__(name):
    if name not in __all__:
        raise AttributeError(name)
    from nagaspilot.hardware import hal
    return getattr(hal, name)

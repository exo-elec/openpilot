#!/usr/bin/env python3
"""Hardware Platform Registry for Rockchip RK3588 (ExoPilot 01M)."""

from __future__ import annotations

import os
from openpilot.system.hardware.base import HardwareBase


class PlatformRegistry:
    """Registry for ExoPilot hardware platforms."""
    
    _platforms: dict[str, type[HardwareBase]] = {}
    _aliases: dict[str, str] = {}
    
    @classmethod
    def register(cls, name: str, hardware_class: type[HardwareBase], aliases: list[str] = None):
        """Register a hardware platform."""
        cls._platforms[name] = hardware_class
        if aliases:
            for alias in aliases:
                cls._aliases[alias] = name
    
    @classmethod
    def create(cls, platform: str = None) -> HardwareBase:
        """Create hardware instance for platform."""
        if platform is None:
            platform = cls.detect()
        
        # Resolve alias
        if platform in cls._aliases:
            platform = cls._aliases[platform]
        
        if platform not in cls._platforms:
            raise ValueError(f"Unknown platform: {platform}")
        
        return cls._platforms[platform]()
    
    @classmethod
    def detect(cls) -> str:
        """Auto-detect hardware platform."""
        # Check device tree
        try:
            with open('/proc/device-tree/compatible') as f:
                compatible = f.read().lower()
                if 'rk3588' in compatible:
                    return 'rk3588'
        except Exception:
            pass
        
        # Check environment variable for testing
        if 'HARDWARE' in os.environ:
            return os.environ['HARDWARE']

        # No RK device tree: dev PC (upstream semantics)
        return 'pc'
    
    @classmethod
    def clear(cls):
        """Clear all registrations (for testing)."""
        cls._platforms.clear()
        cls._aliases.clear()


# Auto-register platforms
def _auto_register():
    """Auto-register the RK3588 platform."""
    try:
        from openpilot.system.hardware.rk3588.hardware import RK3588Hardware
        PlatformRegistry.register('rk3588', RK3588Hardware,
                                  aliases=['rk3588s', 'rk3588s2', 'exopilot01m'])
    except ImportError:
        pass

    try:
        from openpilot.system.hardware.pc.hardware import Pc
        PlatformRegistry.register('pc', Pc)
    except ImportError:
        pass


_auto_register()

#!/usr/bin/env python3
"""
Hardware Abstraction Layer for Rockchip RK3588 (ExoPilot 01M)

Provides platform detection, hardware access, and device configuration.
"""

from __future__ import annotations

from typing import cast

# Core hardware exports
from openpilot.system.hardware.base import HardwareBase, HardwareCapability
from openpilot.system.hardware.registry import PlatformRegistry

# Platform exports
from openpilot.system.hardware.rk3588.hardware import RK3588Hardware

# Singleton hardware instance
HARDWARE = cast(HardwareBase, PlatformRegistry.create())

# Platform detection flags
RK3588 = HARDWARE.get_device_type() == 'rk3588'
RK3588_DETECTED = RK3588

# Combined Rockchip platform flag
ROCKCHIP = RK3588

# Legacy compatibility alias (TICI = any Rockchip platform)
TICI = ROCKCHIP

# Platform detection helper
PC = not ROCKCHIP

# Speaker detection (for alert tones, TTS output)
HAS_SPEAKER = HARDWARE.has_speaker() if hasattr(HARDWARE, 'has_speaker') else False

# Voice input detection (mic + PCIe accelerator for Whisper STT) — RK3588 has no on-board mic
HAS_VOICE_INPUT = HARDWARE.has_voice_input() if hasattr(HARDWARE, 'has_voice_input') else False

# Side camera detection (UVC via USB 3.0 hub RTS5411S)
HAS_SIDE_CAMERAS = HARDWARE.has_side_cameras() if hasattr(HARDWARE, 'has_side_cameras') else False

# Rear camera detection (USB UVC)
HAS_REAR_CAMERA = HARDWARE.has_rear_camera() if hasattr(HARDWARE, 'has_rear_camera') else False

__all__ = [
    # Core
    'HARDWARE',
    'HardwareBase',
    'HardwareCapability',
    'PlatformRegistry',
    # Platforms
    'RK3588',
    'RK3588_DETECTED',
    'RK3588Hardware',
    # Combined flags
    'ROCKCHIP',
    'TICI',  # Legacy compatibility
    # Detection
    'PC',
    'HAS_SPEAKER',
    'HAS_VOICE_INPUT',
    'HAS_SIDE_CAMERAS',
    'HAS_REAR_CAMERA',
]

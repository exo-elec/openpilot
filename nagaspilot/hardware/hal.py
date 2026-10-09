#!/usr/bin/env python3
"""Shared hardware facade for the independent RK3588 and RK3576 adapters."""

from __future__ import annotations

from typing import cast

# Core hardware exports
from nagaspilot.hardware.base import HardwareBase, HardwareCapability
from nagaspilot.hardware.registry import PlatformRegistry

# Platform exports
from openpilot.system.hardware.rockchip_base import RockchipHardware
from openpilot.system.hardware.rk3576.hardware import RK3576Hardware
from openpilot.system.hardware.rk3588.hardware import RK3588Hardware

# Singleton hardware instance
HARDWARE = cast(HardwareBase, PlatformRegistry.create())

# Platform detection flags
RK3588 = HARDWARE.get_device_type() == 'rk3588'
RK3588_DETECTED = RK3588
RK3576 = HARDWARE.get_device_type() == 'rk3576'
RK3576_DETECTED = RK3576

# "Is this an ExoPilot board?" Asks the shared base, not RK3588Hardware:
# RK3576Hardware used to subclass RK3588Hardware, so this was answered by
# "is this an 01M" -- true only by accident of the class hierarchy, and
# false the moment the two became siblings.
#
# Named ROCKCHIP because both boards are Rockchip parts, but the set is not
# "any Rockchip SoC": it is exactly the boards ExoPilot ships, one per
# branch (01M/RK3588 here, 02M/RK3576 on dev/02M). 03M/RK3688 is not
# supported yet and is DoraPilot's, not this tree's -- adding a board here
# is a deliberate act, not something that should happen for free.
ROCKCHIP = isinstance(HARDWARE, RockchipHardware)

# Legacy compatibility alias (TICI = running on an ExoPilot board)
TICI = ROCKCHIP

# Platform detection helper
PC = not ROCKCHIP

# Speaker detection (for alert tones, TTS output)
HAS_SPEAKER = HARDWARE.has_speaker() if hasattr(HARDWARE, 'has_speaker') else False

# Voice input = an on-board microphone: True on 01M and 02M (2-mic I2S pair),
# on a PC when an input device exists.
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
    'RK3576',
    'RK3576_DETECTED',
    'RK3576Hardware',
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

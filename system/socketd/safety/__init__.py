#!/usr/bin/env python3
"""
SocketD Safety Layer - 1st Layer Safety Check
Shared between openpilot and visionpilot

This module provides software-level safety enforcement for Tesla protocol.
It acts as the first layer of safety, with TC275 providing the second layer.

Architecture:
    Layer 1 (Software): openpilot/visionpilot + socketd safety (this module)
    Layer 2 (Hardware): TC275 gateway with tighter limits

Safety Philosophy:
    - Layer 1 catches software bugs and provides smooth control limits
    - Layer 2 (TC275) provides hardware-enforced final safety net
    - TC275 has TIGHTER limits than this layer
"""

from openpilot.system.socketd.safety.tesla_safety import TeslaSafety, SafetyLimits
from openpilot.system.socketd.safety.safety_manager import SafetyManager, SafetyViolation

__all__ = ['TeslaSafety', 'SafetyLimits', 'SafetyManager', 'SafetyViolation']

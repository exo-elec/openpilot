#!/usr/bin/env python3
"""
Vehicle Safety Layer - 1st Layer (Tesla protocol)

Compatibility shim.  The implementation lives in the canonical module
`openpilot.system.socketd.safety.tesla_safety` (the TC275 cross-core checks
that used to be unique to this copy were merged into it).  Import from here
only for backwards compatibility; new code should import tesla_safety directly.
"""

from openpilot.system.socketd.safety.tesla_safety import (
    SafetyLimits,
    SafetyState,
    SafetyViolation,
    TeslaSafety,
    VehicleSafetyLayer,
)

__all__ = [
    "SafetyLimits",
    "SafetyState",
    "SafetyViolation",
    "TeslaSafety",
    "VehicleSafetyLayer",
]

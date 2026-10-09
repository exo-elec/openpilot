#!/usr/bin/env python3
"""Compatibility alias for ExoPilot's power management policy."""

import sys
from nagaspilot.manager import power_monitoring as _implementation

sys.modules[__name__] = _implementation

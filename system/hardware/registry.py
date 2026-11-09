#!/usr/bin/env python3
"""Compatibility alias for ExoPilot's product platform registry."""

import sys
from exopilot.system.hardware import registry as _implementation

sys.modules[__name__] = _implementation

#!/usr/bin/env python3
"""Compatibility alias for ExoPilot's product paths adapter."""

import sys
from exopilot.hardware import paths as _implementation

sys.modules[__name__] = _implementation

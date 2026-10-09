#!/usr/bin/env python3
"""Compatibility alias for ExoPilot's hardware platform base."""

import sys
from nagaspilot.hardware import base as _implementation

sys.modules[__name__] = _implementation

"""Compatibility import for the relocated EOP radar3d daemon."""
import importlib
import sys
_implementation = importlib.import_module("openpilot.exopilot.daemons.radar3d.radar3d")
sys.modules[__name__] = _implementation

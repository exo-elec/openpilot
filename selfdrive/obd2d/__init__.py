"""Compatibility import for the relocated obd2d daemon."""
import importlib
import sys
_implementation = importlib.import_module("openpilot.exopilot.daemons.obd2d")
sys.modules[__name__] = _implementation

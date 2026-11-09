"""Compatibility import for the relocated tripd daemon."""
import importlib
import sys
_implementation = importlib.import_module("openpilot.exopilot.daemons.tripd.tripd")
sys.modules[__name__] = _implementation

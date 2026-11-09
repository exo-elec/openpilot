"""Compatibility import for the relocated steamd daemon."""
import importlib
import sys
_implementation = importlib.import_module("openpilot.exopilot.system.steamd.inputs")
sys.modules[__name__] = _implementation

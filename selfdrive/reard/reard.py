"""Compatibility import for the relocated reard daemon."""
import importlib
import sys
_implementation = importlib.import_module("openpilot.exopilot.selfdrive.reard.reard")
sys.modules[__name__] = _implementation

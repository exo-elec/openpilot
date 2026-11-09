"""Compatibility import for the relocated reard daemon."""
import importlib
import sys
_implementation = importlib.import_module("openpilot.exopilot.daemons.reard.reard")
sys.modules[__name__] = _implementation

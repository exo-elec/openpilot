"""Compatibility import for the relocated obd2d daemon."""
import importlib
import sys
_implementation = importlib.import_module("openpilot.exopilot.system.obd2d.adapters.udsoncan_adapter")
sys.modules[__name__] = _implementation

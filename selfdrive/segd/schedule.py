"""Compatibility import for the relocated segd daemon."""
import importlib
import sys
_implementation = importlib.import_module("openpilot.exopilot.selfdrive.segd.schedule")
sys.modules[__name__] = _implementation

"""Compatibility import for the relocated gridd daemon."""
import importlib
import sys
_implementation = importlib.import_module("openpilot.exopilot.selfdrive.gridd.__main__")
sys.modules[__name__] = _implementation

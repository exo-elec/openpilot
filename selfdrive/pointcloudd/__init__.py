"""Compatibility import for the relocated pointcloudd daemon."""
import importlib
import sys

_implementation = importlib.import_module("openpilot.exopilot.selfdrive.pointcloudd")
sys.modules[__name__] = _implementation

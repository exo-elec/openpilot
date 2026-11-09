"""Compatibility import for the relocated SurfaceD daemon."""
import importlib
import sys

_implementation = importlib.import_module("openpilot.exopilot.selfdrive.surfaced.surfaced")
sys.modules[__name__] = _implementation

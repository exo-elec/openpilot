"""Compatibility import for SurfaceD's relocated detector."""
import importlib
import sys

_implementation = importlib.import_module("openpilot.exopilot.selfdrive.surfaced.surface_detector")
sys.modules[__name__] = _implementation

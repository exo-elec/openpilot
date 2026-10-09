"""Compatibility import for SurfaceD's relocated PCD matcher."""
import importlib
import sys

_implementation = importlib.import_module("openpilot.nagaspilot.daemons.surfaced.pcd_matcher")
sys.modules[__name__] = _implementation

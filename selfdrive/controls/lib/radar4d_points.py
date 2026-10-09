"""Compatibility import for the relocated 02M radar4d module."""
import importlib
import sys
_implementation = importlib.import_module("openpilot.nagaspilot.daemons.radar4d.radar4d_points")
sys.modules[__name__] = _implementation

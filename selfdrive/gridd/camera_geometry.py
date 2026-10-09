"""Compatibility import for the relocated gridd daemon."""
import importlib
import sys
_implementation = importlib.import_module("openpilot.nagaspilot.daemons.gridd.camera_geometry")
sys.modules[__name__] = _implementation

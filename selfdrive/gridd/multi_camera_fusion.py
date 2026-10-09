"""Compatibility import for the relocated gridd daemon."""
import importlib
import sys
_implementation = importlib.import_module("openpilot.nagaspilot.daemons.gridd.multi_camera_fusion")
sys.modules[__name__] = _implementation

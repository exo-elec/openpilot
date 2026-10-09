"""Compatibility import for ExoPilot multi-camera calibration."""
import importlib
import sys
_implementation = importlib.import_module("openpilot.nagaspilot.daemons.camera_calibrationd.camera_calibrationd")
sys.modules[__name__] = _implementation

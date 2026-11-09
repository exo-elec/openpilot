"""Compatibility import for ExoPilot multi-camera calibration."""
import importlib
import sys
_implementation = importlib.import_module("openpilot.exopilot.selfdrive.camera_calibrationd.camera_calibrationd")
sys.modules[__name__] = _implementation

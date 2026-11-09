"""Compatibility import for relocated MonoD."""
import importlib
import sys
_implementation = importlib.import_module("openpilot.exopilot.selfdrive.monod.calibration_fusion")
sys.modules[__name__] = _implementation

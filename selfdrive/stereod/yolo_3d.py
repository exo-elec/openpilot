"""Compatibility import for the relocated stereod daemon."""
import importlib
import sys

_implementation = importlib.import_module("openpilot.nagaspilot.daemons.stereod.yolo_3d")
sys.modules[__name__] = _implementation

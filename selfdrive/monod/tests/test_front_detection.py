"""Compatibility import for relocated MonoD."""
import importlib
import sys
_implementation = importlib.import_module("openpilot.exopilot.selfdrive.monod.tests.test_front_detection")
sys.modules[__name__] = _implementation

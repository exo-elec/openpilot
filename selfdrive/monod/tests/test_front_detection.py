"""Compatibility import for relocated MonoD."""
import importlib
import sys
_implementation = importlib.import_module("openpilot.nagaspilot.daemons.monod.tests.test_front_detection")
sys.modules[__name__] = _implementation

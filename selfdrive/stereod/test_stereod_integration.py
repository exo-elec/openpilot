"""Compatibility import for the relocated stereod daemon."""
import importlib
import sys

_implementation = importlib.import_module("openpilot.nagaspilot.daemons.stereod.test_stereod_integration")
sys.modules[__name__] = _implementation

"""Compatibility import for the relocated EOP radar3d daemon."""
import importlib
import sys
_implementation = importlib.import_module("openpilot.nagaspilot.daemons.radar3d.tests.test_radar3d")
sys.modules[__name__] = _implementation

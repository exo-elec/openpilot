"""Compatibility import for the relocated sided daemon."""
import importlib
import sys
_implementation = importlib.import_module("openpilot.nagaspilot.daemons.sided.tests.test_ground_plane")
sys.modules[__name__] = _implementation

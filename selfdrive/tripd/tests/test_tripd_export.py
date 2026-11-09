"""Compatibility import for the relocated tripd daemon."""
import importlib
import sys
_implementation = importlib.import_module("openpilot.exopilot.daemons.tripd.tests.test_tripd_export")
sys.modules[__name__] = _implementation

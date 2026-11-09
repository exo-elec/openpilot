"""Compatibility import for the relocated gridd daemon."""
import importlib
import sys
_implementation = importlib.import_module("openpilot.exopilot.selfdrive.gridd.tests.test_bev_grid")
sys.modules[__name__] = _implementation

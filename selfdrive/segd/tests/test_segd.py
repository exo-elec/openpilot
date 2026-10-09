"""Compatibility import for the relocated segd daemon."""
import importlib
import sys
_implementation = importlib.import_module("openpilot.nagaspilot.daemons.segd.tests.test_segd")
sys.modules[__name__] = _implementation

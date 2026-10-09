"""Compatibility import for the relocated recordd daemon."""
import importlib
import sys
_implementation = importlib.import_module("openpilot.nagaspilot.daemons.recordd.recordd")
sys.modules[__name__] = _implementation

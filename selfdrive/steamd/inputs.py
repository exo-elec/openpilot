"""Compatibility import for the relocated steamd daemon."""
import importlib
import sys
_implementation = importlib.import_module("openpilot.nagaspilot.daemons.steamd.inputs")
sys.modules[__name__] = _implementation

"""Compatibility import for the relocated adaptd daemon."""
import importlib
import sys
_implementation = importlib.import_module("openpilot.nagaspilot.daemons.adaptd.adaptd")
sys.modules[__name__] = _implementation

"""Compatibility import for the relocated adaptd daemon."""
import importlib
import sys
_implementation = importlib.import_module("openpilot.exopilot.selfdrive.adaptd")
sys.modules[__name__] = _implementation

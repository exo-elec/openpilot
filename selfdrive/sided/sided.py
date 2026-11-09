"""Compatibility import for the relocated sided daemon."""
import importlib
import sys
_implementation = importlib.import_module("openpilot.exopilot.selfdrive.sided.sided")
sys.modules[__name__] = _implementation

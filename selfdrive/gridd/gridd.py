"""Compatibility import for the relocated GridD daemon."""
import importlib
import sys
_implementation = importlib.import_module("openpilot.exopilot.daemons.gridd.gridd")
sys.modules[__name__] = _implementation

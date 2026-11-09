"""Compatibility import for the relocated MonoD daemon."""
import importlib
import sys
_implementation = importlib.import_module("openpilot.exopilot.selfdrive.monod.rknn")
sys.modules[__name__] = _implementation

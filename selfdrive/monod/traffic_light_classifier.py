"""Compatibility import for relocated MonoD."""
import importlib
import sys
_implementation = importlib.import_module("openpilot.exopilot.daemons.monod.traffic_light_classifier")
sys.modules[__name__] = _implementation

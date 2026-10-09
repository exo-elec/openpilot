"""Compatibility import for the relocated stereod daemon."""
import importlib
import sys

_implementation = importlib.import_module("openpilot.nagaspilot.daemons.stereod.depth_pipeline")
sys.modules[__name__] = _implementation

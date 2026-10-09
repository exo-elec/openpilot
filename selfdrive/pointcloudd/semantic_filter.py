"""Compatibility import for the relocated pointcloudd daemon."""
import importlib
import sys

_implementation = importlib.import_module("openpilot.nagaspilot.daemons.pointcloudd.semantic_filter")
sys.modules[__name__] = _implementation

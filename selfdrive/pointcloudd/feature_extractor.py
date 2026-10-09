"""Compatibility import for the relocated pointcloudd daemon."""
import importlib
import sys

_implementation = importlib.import_module("openpilot.nagaspilot.daemons.pointcloudd.feature_extractor")
sys.modules[__name__] = _implementation

"""Compatibility import for the relocated pathd daemon."""
import importlib
import sys
_implementation = importlib.import_module("openpilot.nagaspilot.daemons.pathd.lane_change")
sys.modules[__name__] = _implementation

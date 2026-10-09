"""Compatibility import for the relocated pathd daemon."""
import importlib
import sys
_implementation = importlib.import_module("openpilot.nagaspilot.daemons.pathd.long_horizon_planner")
sys.modules[__name__] = _implementation

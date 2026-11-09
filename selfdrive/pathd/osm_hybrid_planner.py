"""Compatibility import for the relocated pathd daemon."""
import importlib
import sys
_implementation = importlib.import_module("openpilot.exopilot.daemons.pathd.osm_hybrid_planner")
sys.modules[__name__] = _implementation

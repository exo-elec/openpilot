"""Compatibility import for the relocated pathd daemon."""
import importlib
import sys
_implementation = importlib.import_module("openpilot.nagaspilot.daemons.pathd.soc")
sys.modules[__name__] = _implementation

"""Compatibility import for the relocated pathd daemon."""
import importlib
import sys
_implementation = importlib.import_module("openpilot.exopilot.daemons.pathd.test.test_path_corridor_fusion")
sys.modules[__name__] = _implementation

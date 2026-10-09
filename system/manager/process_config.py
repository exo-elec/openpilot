"""Compatibility hook for the ExoPilot process registry."""
import importlib
import sys

_implementation = importlib.import_module("openpilot.exopilot.system.manager.process_config")
sys.modules[__name__] = _implementation

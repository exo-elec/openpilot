"""Compatibility import for the relocated sided daemon."""
import importlib
import sys
_implementation = importlib.import_module("openpilot.nagaspilot.daemons.sided.handover_manager")
sys.modules[__name__] = _implementation

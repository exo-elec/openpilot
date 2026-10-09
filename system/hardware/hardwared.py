"""Compatibility import for ExoPilot's hardware state daemon."""
import importlib
import sys

_implementation = importlib.import_module("openpilot.nagaspilot.daemons.hardwared.hardwared")
sys.modules[__name__] = _implementation

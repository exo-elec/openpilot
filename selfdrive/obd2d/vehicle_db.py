"""Compatibility import for the relocated obd2d daemon."""
import importlib
import sys
_implementation = importlib.import_module("openpilot.nagaspilot.daemons.obd2d.vehicle_db")
sys.modules[__name__] = _implementation

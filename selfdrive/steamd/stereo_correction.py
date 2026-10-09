"""Compatibility import for relocated SteamD stereo correction."""
import importlib
import sys
_implementation = importlib.import_module("openpilot.nagaspilot.daemons.steamd.stereo_correction")
sys.modules[__name__] = _implementation

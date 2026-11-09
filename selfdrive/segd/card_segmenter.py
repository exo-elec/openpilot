"""Compatibility import for the relocated segd daemon."""
import importlib
import sys
_implementation = importlib.import_module("openpilot.exopilot.daemons.segd.card_segmenter")
sys.modules[__name__] = _implementation

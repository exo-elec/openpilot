"""Compatibility alias; the EOP-origin implementation is eop_curve_speed."""
import sys
from importlib import import_module

sys.modules[__name__] = import_module("exopilot.controls.eop_curve_speed")

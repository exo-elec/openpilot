"""Compatibility alias; the EOP-origin implementation is eop_mtsc."""
import sys
from importlib import import_module

sys.modules[__name__] = import_module("nagaspilot.controls.eop_mtsc")

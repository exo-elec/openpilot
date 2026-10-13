"""What differs per branch in the big-model link (T16/T17): the one file a branch rewrites.

Param names (a branch's naming rule), how to tell a real device from a PC, the camera geometry, and how the gadget owner drives the port. Everything else under nagaspilot/bigmodel/,
nagaspilot/runtime/bigmodel*.py and nagaspilot/controls/ngp_bigmodel_*.py is byte-identical across NGP10, EOP10, 01M
and 02M. Standard library only at import time: the resident gadget owner imports it.
"""
from pathlib import Path

# the setting: 0 off; 3 USB tethering, the phone is the USB device and the comma its host (RECOMMENDED, same for iPhone and Android);
# 2 an iPhone as host of the comma's CDC-NCM gadget; 1 an Android phone as host of a FunctionFS bulk gadget. One NPBM protocol on all
# three (nagaspilot/bigmodel/framing.py adds the bulk padding for 1)
LINK = "ngp_bigmodel_link"

# written by modeld, read by selfdrived's gate (nagaspilot/controls/ngp_bigmodel_gate.py)
LOADING = "BigModelLoading"
ACTIVE = "BigModelActive"
# (cam_w, cam_h, model_w, model_h): a comma 3 / 3X road camera and the model frame DrivingModelFrame warps to
DEVICE_CAMERA = (1928, 1208, 512, 256)

# how the gadget script treats the USB-C port: 'agnos' is the Qualcomm comma; 'static' is a board whose port role is fixed by
# its device tree (peripheral); 'pd' is a board with a PD controller (kernel Type-C class): bigmodeld asks for the device role through
# /sys/class/typec (nagaspilot/bigmodel/typec.py). Unverified on a board (docs/NAVPILOT_BIGMODEL.md)
USB_PLATFORM = "agnos"


def on_device() -> bool:
  """A real device rather than a PC (decides the params directory and where models and logs live)."""
  return Path('/AGNOS').is_file()

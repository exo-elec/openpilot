"""What differs per branch in the big-model link (T16/T17): the one file a branch rewrites.

Param names (a branch's naming rule), how to tell a real device from a PC, whether the USB-C port is taken by the
eGPU, the camera geometry, and how the gadget owner drives the port. Everything else under nagaspilot/bigmodel/,
nagaspilot/runtime/bigmodel*.py and nagaspilot/controls/ngp_bigmodel_*.py is byte-identical across NGP10, EOP10, 01M
and 02M. Standard library only at import time: the resident gadget owner imports it.
"""
from pathlib import Path

# the setting: 0 off, nonzero on (the one transport is TCP over a CDC-NCM USB network; 1 and 2 both mean on)
LINK = "ngp_bigmodel_link"

# written by modeld, read by selfdrived's gate (nagaspilot/controls/ngp_bigmodel_gate.py)
LOADING = "BigModelLoading"
ACTIVE = "BigModelActive"
# (cam_w, cam_h, model_w, model_h): a comma 3 / 3X road camera and the model frame DrivingModelFrame warps to
DEVICE_CAMERA = (1928, 1208, 512, 256)

# how the gadget script treats the USB-C port: 'agnos' is the Qualcomm comma; 'static' is a board whose port role is fixed by
# its device tree (peripheral). The script does no USB-PD handling in either case yet (docs/NAVPILOT_BIGMODEL.md, unverified)
USB_PLATFORM = "agnos"


def on_device() -> bool:
  """A real device rather than a PC (decides the params directory and where models and logs live)."""
  return Path('/AGNOS').is_file()


def egpu_in_use() -> bool:
  """Is the eGPU on the USB-C port the phone would use? Then the link must stay off (one port, one role)."""
  from openpilot.selfdrive.modeld.egpu_detect import egpu_present
  return egpu_present() is not None

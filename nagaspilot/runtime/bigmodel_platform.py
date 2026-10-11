"""What differs per branch in the big-model link (T16/T17): the one file a branch rewrites.

Param names (a branch's naming rule), how to tell a real device from a PC, whether the USB-C port is taken by the
eGPU, the camera geometry, and how Jetlink's gadget owner drives the port. Everything else under
nagaspilot/runtime/bigmodel*.py and jetlink_adapter.py, and nagaspilot/controls/ngp_bigmodel_*.py, is byte-identical
across NGP10, EOP10, 01M and 02M. Standard library only at import time: the resident gadget owner imports it.
"""
from pathlib import Path

# the setting (an INT index into jetlink's MODES: 0 off, 1 USB/Android, 2 iOS) and Jetlink's JSON records
LINK = "ngp_bigmodel_link"
PROGRESS = "ngp_bigmodel_progress"
SPEC = "ngp_bigmodel_spec"
POINTERS = "ngp_bigmodel_pointers"
CHARGE_PHONE = "ngp_bigmodel_charge_phone"

# written by modeld, read by selfdrived's gate (nagaspilot/controls/ngp_bigmodel_gate.py)
LOADING = "BigModelLoading"
ACTIVE = "BigModelActive"
# (cam_w, cam_h, model_w, model_h): a comma 3 / 3X road camera and the model frame DrivingModelFrame warps to
DEVICE_CAMERA = (1928, 1208, 512, 256)

# how jetlink-root.sh drives the USB-C port: 'agnos' is the Qualcomm comma (USB-PD role swaps, charger voter);
# 'static' is a board whose port role is fixed by its device tree (peripheral) and needs no PD handling
USB_PLATFORM = "agnos"


def on_device() -> bool:
  """A real device rather than a PC (decides the params directory and where models and logs live)."""
  return Path('/AGNOS').is_file()


def egpu_in_use() -> bool:
  """Is the eGPU on the USB-C port the phone would use? Then the link must stay off (one port, one role)."""
  from openpilot.selfdrive.modeld.egpu_detect import egpu_present
  return egpu_present() is not None

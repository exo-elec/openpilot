"""What differs per branch in the big-model link (T16/T17): the one file a branch rewrites.

This is dev/EOP10's (RK3588 / ExoPilot). Param names follow the EOP rule; the USB-C port role is fixed by the
device tree (peripheral, see exopilot's kernel overlays), so Jetlink's gadget owner needs no USB-PD handling.
Everything else under nagaspilot/runtime/bigmodel*.py and jetlink_adapter.py, and nagaspilot/controls/ngp_bigmodel_*.py,
is byte-identical to NGP10's. Standard library only at import time: the resident gadget owner imports it.
"""

# the setting (an INT index into jetlink's MODES: 0 off, 1 USB/Android, 2 iOS) and Jetlink's JSON records
LINK = "EOPBigModelLink"
PROGRESS = "EOPBigModelProgress"
SPEC = "EOPBigModelSpec"
POINTERS = "EOPBigModelPointers"
CHARGE_PHONE = "EOPBigModelChargePhone"

# written by modeld, read by selfdrived's gate (nagaspilot/controls/ngp_bigmodel_gate.py)
LOADING = "EOPBigModelLoading"
ACTIVE = "EOPBigModelActive"

# (cam_w, cam_h, model_w, model_h): only the model frame matters to the warp source, which warps with the small
# model's own DrivingModelFrame; the camera size comes from camerad when the link attaches
DEVICE_CAMERA = (1928, 1208, 512, 256)

# 'static': the port role is fixed by the device tree and there is no PD controller to drive
USB_PLATFORM = "static"


def on_device() -> bool:
  """A real device rather than a PC (decides the params directory and where models and logs live)."""
  from openpilot.system.hardware import PC
  return not PC


def egpu_in_use() -> bool:
  """Is the eGPU on the USB-C port the phone would use? Then the link must stay off (one port, one role)."""
  from openpilot.selfdrive.modeld.egpu_detect import egpu_present
  return egpu_present() is not None

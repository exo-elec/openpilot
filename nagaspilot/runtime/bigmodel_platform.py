"""What differs per branch in the big-model link (T16/T17): the one file a branch rewrites.

This is dev/EOP10's (RK3588 / ExoPilot). Param names are the same as on NGP10 (the feature is NGP-origin, D12); the USB-C port role is fixed by the
device tree (peripheral, see exopilot's kernel overlays) when a gadget mode is used. Everything else under
nagaspilot/bigmodel/, nagaspilot/runtime/bigmodel*.py and nagaspilot/controls/ngp_bigmodel_*.py is byte-identical to NGP10's. Standard library only at import time: the resident gadget owner imports it.
"""

# the setting: 0 off; 1 an Android phone as host of a FunctionFS bulk gadget; 2 an iPhone as host of the board's CDC-NCM gadget (the
# direct modes, the default way: D11; they need exopilot's device-mode overlay); 3 USB tethering, the phone the USB device and the
# board its host (optional). One NPBM protocol on all three.
LINK = "ngp_bigmodel_link"

# written by modeld, read by selfdrived's gate (nagaspilot/controls/ngp_bigmodel_gate.py)
LOADING = "ngp_bigmodel_loading"
ACTIVE = "ngp_bigmodel_active"

# (cam_w, cam_h, model_w, model_h): only the model frame matters to the warp source, which warps with the small
# model's own DrivingModelFrame; the camera size comes from camerad when the link attaches
DEVICE_CAMERA = (1928, 1208, 512, 256)

# 'static': the port role is fixed by the device tree and there is no PD controller to drive.
# 'pd': the board has a PD controller (kernel Type-C class); bigmodeld asks for the device role through /sys/class/typec
# (nagaspilot/bigmodel/typec.py; exopilot docs/02-HARDWARE/DIRECT_USB_LINK.md). Set this when the PD board exists. Unverified on a board.
USB_PLATFORM = "static"


def on_device() -> bool:
  """A real device rather than a PC (decides the params directory and where models and logs live)."""
  from openpilot.system.hardware import PC
  return not PC

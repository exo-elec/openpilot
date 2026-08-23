#!/usr/bin/env python3
"""ASM2464PD USB eGPU firmware detection.

Split out from modeld.py so it's unit-testable without pulling in modeld's
full (heavy, hardware-oriented) import chain — matches upstream openpilot's
own pattern of keeping this kind of detection in a standalone helpers module.
"""

from __future__ import annotations

import glob

# Post-flash ASM2464PD USB IDs. Shared by both firmware images this project
# recognizes on the same physical bridge chip: our own generic bridge
# firmware (what we flash ourselves — see exopilot's
# docs/02-HARDWARE/EGPU_ASM2464PD.md) and comma's official Chestnut firmware.
# These IDs are identical to upstream openpilot's own
# common/hardware/usb.py:CHESTNUT_USB_IDS — confirmed by reading it directly,
# not assumed. EGPU only opts in when the board is actually enumerated
# present, so a stale/forgotten env var can never leave modeld pointed at a
# missing device.
EGPU_VID_PIDS = {('0xadd1', '0x0001'), ('0x3801', '0x0001')}

# Literal USB product string tinygrad's extra/usbgpu/patch.py writes into our
# own flashed firmware (confirmed by reading patch.py directly) — this stays
# the primary/default target. Comma's Chestnut firmware instead reports
# "custom {CHESTNUT_FW_VERSION}-CLEAN" (matches upstream's
# common/hardware/usb.py:CHESTNUT_FW_VERSION exactly); recognized alongside
# ours so either flashed image is detected, never a third-party device
# reusing the same VID:PID.
EGPU_PRODUCT_OWN = "USB 3.2 PCIe TinyEnclosure"
CHESTNUT_FW_VERSION = "ed4e39b7"
EGPU_PRODUCT_CHESTNUT = f"custom {CHESTNUT_FW_VERSION}-CLEAN"
EGPU_PRODUCTS = {EGPU_PRODUCT_OWN: 'own', EGPU_PRODUCT_CHESTNUT: 'chestnut'}


def egpu_present() -> str | None:
  """Return which firmware was detected ('own' / 'chestnut'), or None."""
  for path in glob.glob('/sys/bus/usb/devices/*'):
    try:
      with open(f'{path}/idVendor') as f:
        vendor = f.read().strip().lower()
      with open(f'{path}/idProduct') as f:
        product = f.read().strip().lower()
      with open(f'{path}/product') as f:
        product_str = f.read().strip()
    except OSError:
      continue
    if (f'0x{vendor}', f'0x{product}') in EGPU_VID_PIDS and product_str in EGPU_PRODUCTS:
      return EGPU_PRODUCTS[product_str]
  return None

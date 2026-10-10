#!/usr/bin/env python3
"""ASM2464PD USB eGPU firmware detection.

Split out from modeld.py so it's unit-testable without pulling in modeld's
full (heavy, hardware-oriented) import chain — matches upstream openpilot's
own pattern of keeping this kind of detection in a standalone helpers module.
"""

from __future__ import annotations

import glob

# Official comma Chestnut USB IDs and firmware product string, plus the existing
# generic TinyEnclosure compatibility ID. No Exopilot HAL is required.
EGPU_VID_PIDS = {('0xadd1', '0x0001'), ('0x3801', '0x0001')}

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


def wait_for_chestnut(timeout=10.0, clock=None, sleep=None):
  import time
  clock, sleep = clock or time.monotonic, sleep or time.sleep
  start = clock()
  while (firmware := egpu_present()) is None:
    if clock() - start >= timeout:
      raise TimeoutError('Chestnut did not enumerate')
    sleep(0.1)
  return firmware

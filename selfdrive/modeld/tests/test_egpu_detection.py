#!/usr/bin/env python3
"""Tests for modeld's dual ASM2464PD firmware detection (egpu_detect.egpu_present).

Recognizes two firmware images on the same physical bridge chip: our own
generic bridge firmware (tinygrad's extra/usbgpu/patch.py) and comma's
official Chestnut firmware. Both share the same VID:PID pair, which is
confirmed identical to upstream openpilot's own
common/hardware/usb.py:CHESTNUT_USB_IDS -- only the USB product string
distinguishes which is actually flashed."""

from __future__ import annotations

from pathlib import Path

import pytest

from openpilot.selfdrive.modeld import egpu_detect


def _write_usb_device(devices_dir: Path, name: str, vendor: str, product_id: str, product_str: str) -> None:
  d = devices_dir / name
  d.mkdir()
  (d / "idVendor").write_text(vendor)
  (d / "idProduct").write_text(product_id)
  (d / "product").write_text(product_str)


@pytest.fixture
def usb_devices_dir(tmp_path: Path, monkeypatch):
  devices_dir = tmp_path / "usb_devices"
  devices_dir.mkdir()
  monkeypatch.setattr(egpu_detect.glob, "glob", lambda pattern: [str(p) for p in devices_dir.glob("*")])
  return devices_dir


def test_detects_own_firmware(usb_devices_dir):
  _write_usb_device(usb_devices_dir, "1-1", "adD1", "0001", "USB 3.2 PCIe TinyEnclosure")
  assert egpu_detect.egpu_present() == "own"


def test_detects_chestnut_firmware(usb_devices_dir):
  _write_usb_device(usb_devices_dir, "1-1", "adD1", "0001", "custom ed4e39b7-CLEAN")
  assert egpu_detect.egpu_present() == "chestnut"


def test_detects_chestnut_firmware_second_vid_pid(usb_devices_dir):
  _write_usb_device(usb_devices_dir, "1-1", "3801", "0001", "custom ed4e39b7-CLEAN")
  assert egpu_detect.egpu_present() == "chestnut"


def test_ignores_matching_vid_pid_with_unknown_product_string(usb_devices_dir):
  _write_usb_device(usb_devices_dir, "1-1", "adD1", "0001", "some other device")
  assert egpu_detect.egpu_present() is None


def test_ignores_matching_product_string_with_unrelated_vid_pid(usb_devices_dir):
  _write_usb_device(usb_devices_dir, "1-1", "1234", "5678", "USB 3.2 PCIe TinyEnclosure")
  assert egpu_detect.egpu_present() is None


def test_no_devices_present(usb_devices_dir):
  assert egpu_detect.egpu_present() is None


def test_skips_unreadable_device_entries(usb_devices_dir):
  # A device dir missing idVendor/idProduct/product (e.g. a hub) must not crash the scan.
  (usb_devices_dir / "1-0:1.0").mkdir()
  _write_usb_device(usb_devices_dir, "1-1", "adD1", "0001", "USB 3.2 PCIe TinyEnclosure")
  assert egpu_detect.egpu_present() == "own"

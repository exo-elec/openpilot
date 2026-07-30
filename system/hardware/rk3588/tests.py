#!/usr/bin/env python3
"""Host-side validation tests for RK3588 configuration (no hardware required).

Run on any machine to verify camera configs, imports, and data structures:
    python3 -m openpilot.system.hardware.rk3588.tests
"""

from __future__ import annotations

import sys


def test_imports() -> bool:
    """Verify all RK3588 modules can be imported."""
    try:
        from openpilot.system.hardware.rk3588.hardware import RK3588Hardware
        from openpilot.system.hardware.rk3588.camera_config import USB_CAMERAS, get_camera
        return True
    except Exception as e:
        print(f"  FAIL: import error: {e}")
        return False


def test_camera_count() -> bool:
    """Verify 3 USB cameras are defined (MIPI cameras are in v4l2d)."""
    from openpilot.system.hardware.rk3588.camera_config import USB_CAMERAS, get_camera
    if len(USB_CAMERAS) != 3:
        print(f"  FAIL: expected 3 USB cameras, got {len(USB_CAMERAS)}")
        return False
    if get_camera("side_left") is None:
        print("  FAIL: side_left not found")
        return False
    if get_camera("side_right") is None:
        print("  FAIL: side_right not found")
        return False
    if get_camera("rear_camera") is None:
        print("  FAIL: rear_camera not found")
        return False
    return True


def test_camera_layout() -> bool:
    """Verify USB camera ordering: side_left, side_right, rear_camera."""
    from openpilot.system.hardware.rk3588.camera_config import USB_CAMERAS

    if USB_CAMERAS[0].name != "side_left":
        print(f"  FAIL: USB[0] should be side_left, got {USB_CAMERAS[0].name}")
        return False
    if USB_CAMERAS[1].name != "side_right":
        print(f"  FAIL: USB[1] should be side_right, got {USB_CAMERAS[1].name}")
        return False
    if USB_CAMERAS[2].name != "rear_camera":
        print(f"  FAIL: USB[2] should be rear_camera, got {USB_CAMERAS[2].name}")
        return False

    return True


def test_usb_cameras_sdr() -> bool:
    """Verify all USB cameras use SDR (UVC has no HDR support)."""
    from openpilot.system.hardware.rk3588.camera_config import USB_CAMERAS, HDRMode

    for cam in USB_CAMERAS:
        if cam.hdr != HDRMode.SDR:
            print(f"  FAIL: {cam.name} must use SDR, got {cam.hdr.value}")
            return False
    return True


def test_no_tele_road_in_usb() -> bool:
    """Verify USB config does not contain tele_road."""
    from openpilot.system.hardware.rk3588.camera_config import USB_CAMERAS
    for cam in USB_CAMERAS:
        if cam.name == "tele_road":
            print("  FAIL: USB config should not contain tele_road")
            return False
    return True


def test_hardware_creation() -> bool:
    """Verify RK3588Hardware can be instantiated."""
    try:
        from openpilot.system.hardware.rk3588.hardware import RK3588Hardware
        hw = RK3588Hardware()
        cfg = hw.get_camera_array_config()
        assert cfg["num_cameras"] == 7, f"expected 7 cameras, got {cfg['num_cameras']}"
        assert cfg["stereo_baseline_mm"] == 80.0
        assert cfg["has_tele_road"] is False
        assert hw.has_side_cameras() is True
        assert hw.has_rear_camera() is True
        assert hw.get_max_reliable_depth_m() == 80.0
        return True
    except Exception as e:
        print(f"  FAIL: hardware creation error: {e}")
        return False


def test_registry() -> bool:
    """Verify RK3588 is registered in platform registry."""
    try:
        from openpilot.system.hardware.registry import PlatformRegistry
        assert "rk3588" in PlatformRegistry._platforms
        return True
    except Exception as e:
        print(f"  FAIL: registry error: {e}")
        return False


def main() -> int:
    tests = [
        ("Imports", test_imports),
        ("Camera count", test_camera_count),
        ("Camera layout", test_camera_layout),
        ("USB cameras SDR", test_usb_cameras_sdr),
        ("No tele_road", test_no_tele_road_in_usb),
        ("Hardware creation", test_hardware_creation),
        ("Platform registry", test_registry),
    ]

    passed = 0
    failed = 0
    for name, fn in tests:
        print(f"  {name}...", end=" ")
        if fn():
            print("OK")
            passed += 1
        else:
            print("FAIL")
            failed += 1

    print(f"\n{passed}/{len(tests)} passed")
    return 0 if failed == 0 else 1


if __name__ == "__main__":
    sys.exit(main())

"""Host-side validation tests for RK3588 configuration (no hardware required).

These tests run on any dev machine. Tests that need the closed
`hal.platform.rk3588_camera_geometry` package are skipped when it is not
installed, matching the graceful-degradation behavior of the production code.
"""

from __future__ import annotations

import pytest

from openpilot.system.hardware.rk3588.hardware import RK3588Hardware
from openpilot.system.hardware.rk3588 import camera_config
from openpilot.system.hardware.registry import PlatformRegistry


try:
  from hal.platform import rk3588_camera_geometry as _cam_geo
  HAL_CAMERA_GEO_AVAILABLE = _cam_geo is not None
except ImportError:
  HAL_CAMERA_GEO_AVAILABLE = False


HAL_CAMERA_GEO_UNAVAILABLE_REASON = "hal.platform.rk3588_camera_geometry is not installed"


def test_imports() -> None:
  """Verify all RK3588 modules can be imported."""
  from openpilot.system.hardware.rk3588.hardware import RK3588Hardware as _RK3588Hardware
  from openpilot.system.hardware.rk3588.camera_config import USB_CAMERAS, get_camera
  assert _RK3588Hardware is not None
  assert USB_CAMERAS is not None
  assert get_camera is not None


@pytest.mark.skipif(not HAL_CAMERA_GEO_AVAILABLE, reason=HAL_CAMERA_GEO_UNAVAILABLE_REASON)
def test_camera_count() -> None:
  """Verify 3 USB cameras are defined (MIPI cameras are in v4l2d)."""
  assert len(camera_config.USB_CAMERAS) == 3
  assert camera_config.get_camera("side_left") is not None
  assert camera_config.get_camera("side_right") is not None
  assert camera_config.get_camera("rear_camera") is not None


@pytest.mark.skipif(not HAL_CAMERA_GEO_AVAILABLE, reason=HAL_CAMERA_GEO_UNAVAILABLE_REASON)
def test_camera_layout() -> None:
  """Verify USB camera ordering: side_left, side_right, rear_camera."""
  assert camera_config.USB_CAMERAS[0].name == "side_left"
  assert camera_config.USB_CAMERAS[1].name == "side_right"
  assert camera_config.USB_CAMERAS[2].name == "rear_camera"


@pytest.mark.skipif(not HAL_CAMERA_GEO_AVAILABLE, reason=HAL_CAMERA_GEO_UNAVAILABLE_REASON)
def test_usb_cameras_sdr() -> None:
  """Verify all USB cameras use SDR (UVC has no HDR support)."""
  for cam in camera_config.USB_CAMERAS:
    assert cam.hdr == camera_config.HDRMode.SDR, f"{cam.name} must use SDR, got {cam.hdr.value}"


@pytest.mark.skipif(not HAL_CAMERA_GEO_AVAILABLE, reason=HAL_CAMERA_GEO_UNAVAILABLE_REASON)
def test_no_tele_road_in_usb() -> None:
  """Verify USB config does not contain tele_road."""
  for cam in camera_config.USB_CAMERAS:
    assert cam.name != "tele_road"


def test_hardware_creation() -> None:
  """Verify RK3588Hardware can be instantiated and returns consistent config."""
  hw = RK3588Hardware()
  cfg = hw.get_camera_array_config()

  if HAL_CAMERA_GEO_AVAILABLE:
    assert cfg["num_cameras"] == 7, f"expected 7 cameras, got {cfg['num_cameras']}"
    assert cfg["stereo_baseline_mm"] == 80.0
    assert cfg["has_tele_road"] is False
    # has_side_cameras()/has_rear_camera() probe real device files (/dev/video-*)
    # and lsusb output — these depend on physical hardware being present, not
    # just hal's camera-geometry *data* being on the path. This is a host-side,
    # no-hardware test (see module docstring), so only check these are callable
    # and return a bool, not that they detect hardware that isn't there. Bug
    # found 2026-08-26: this used to assert `is True` unconditionally here,
    # which fails on any dev PC that happens to have hal installed.
    assert isinstance(hw.has_side_cameras(), bool)
    assert isinstance(hw.has_rear_camera(), bool)
  else:
    # Without the hal package only the platform metadata is available.
    assert cfg["num_cameras"] == 0
    assert cfg["stereo_baseline_mm"] == 0.0
    assert cfg["has_tele_road"] is False
    assert cfg["cameras"] == []

  assert hw.get_max_reliable_depth_m() == 80.0


def test_registry() -> None:
  """Verify RK3588 is registered in platform registry."""
  assert "rk3588" in PlatformRegistry._platforms

"""Host-side validation tests for RK3576 configuration (no hardware required).

Mirrors system/hardware/rk3588/tests/test_rk3588.py's structure. Tests that
need the closed `hal.platform.rk3576_camera_geometry` package are skipped
when it is not installed, matching the graceful-degradation behavior of the
production code.
"""

from __future__ import annotations

import pytest

from openpilot.system.hardware.rk3576.hardware import RK3576Hardware
from openpilot.system.hardware.rk3576 import camera_config
from openpilot.system.hardware.registry import PlatformRegistry


try:
  from hal.platform import rk3576_camera_geometry as _cam_geo
  HAL_CAMERA_GEO_AVAILABLE = _cam_geo is not None
except ImportError:
  HAL_CAMERA_GEO_AVAILABLE = False


HAL_CAMERA_GEO_UNAVAILABLE_REASON = "hal.platform.rk3576_camera_geometry is not installed"


def test_imports() -> None:
  """Verify all RK3576 modules can be imported."""
  from openpilot.system.hardware.rk3576.hardware import RK3576Hardware as _RK3576Hardware
  from openpilot.system.hardware.rk3576.camera_config import USB_CAMERAS, get_camera
  assert _RK3576Hardware is not None
  assert USB_CAMERAS is not None
  assert get_camera is not None


@pytest.mark.skipif(not HAL_CAMERA_GEO_AVAILABLE, reason=HAL_CAMERA_GEO_UNAVAILABLE_REASON)
def test_camera_count() -> None:
  """Verify 3 USB cameras are defined (5 MIPI cameras are in v4l2d, not built yet — see Phase B in docs/eop/RK3576_02M_SUPPORT.md)."""
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


def test_hardware_creation() -> None:
  """Verify RK3576Hardware can be instantiated and returns consistent config."""
  hw = RK3576Hardware()
  cfg = hw.get_camera_array_config()

  if HAL_CAMERA_GEO_AVAILABLE:
    assert cfg["num_cameras"] == 8, f"expected 8 cameras, got {cfg['num_cameras']}"
    assert cfg["stereo_baseline_mm"] == 160.0
    assert cfg["has_tele_road"] is True
    assert [c["name"] for c in cfg["cameras"][:5]] == [
      "mono_narrow", "mono_wide", "mono_tele", "stereo_left", "stereo_right",
    ]
    # Host-side test, no real hardware — only check these are callable and
    # return a bool, not that they detect hardware that isn't there (see
    # the bug fixed in test_rk3588.py's equivalent assertion, 2026-08-26).
    assert isinstance(hw.has_side_cameras(), bool)
    assert isinstance(hw.has_rear_camera(), bool)
  else:
    # Without the hal package only the platform metadata is available.
    assert cfg["num_cameras"] == 0
    assert cfg["stereo_baseline_mm"] == 0.0
    assert cfg["has_tele_road"] is True
    assert cfg["cameras"] == []

  assert cfg["platform"] == "ExoPilot 02M"
  assert cfg["soc"] == "RK3576"
  assert hw.get_device_type() == "rk3576"
  assert hw.has_voice_input() is True


def test_shares_camera_array_logic_with_rk3588() -> None:
  """RK3576Hardware must not reimplement get_camera_array_config()/
  get_stereo_baseline_mm() — it should inherit them from RK3588Hardware and
  only override the class-level platform/geometry attributes. Regression
  test for that refactor."""
  from openpilot.system.hardware.rk3588.hardware import RK3588Hardware
  assert RK3576Hardware.get_camera_array_config is RK3588Hardware.get_camera_array_config
  assert RK3576Hardware.get_stereo_baseline_mm is RK3588Hardware.get_stereo_baseline_mm


def test_modem_power_gpio_lookup_does_not_crash_without_hal() -> None:
  """modem_power_on()/off() must fail closed (return False), never raise,
  when hal.platform.rk3576_pins isn't available (GPIO == {})."""
  if RK3576Hardware.GPIO:
    pytest.skip("hal.platform.rk3576_pins is installed — GPIO dict is populated")
  assert RK3576Hardware.modem_power_on() is False
  assert RK3576Hardware.modem_power_off() is False


def test_registry() -> None:
  """Verify RK3576 is registered in platform registry, alongside RK3588."""
  assert "rk3576" in PlatformRegistry._platforms
  assert "rk3588" in PlatformRegistry._platforms
  assert PlatformRegistry._aliases.get("exopilot02m") == "rk3576"
  assert type(PlatformRegistry.create("rk3576")) is RK3576Hardware

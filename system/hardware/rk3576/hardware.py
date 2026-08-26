#!/usr/bin/env python3
"""RK3576 Hardware Implementation (ExoPilot 02M).

Thin subclass of RK3588Hardware: RK3576 is the same Rockchip/Linux userspace
family (reboot/shutdown/network/power methods are all generic subprocess
calls, unchanged), so only what's actually different between the two boards
is overridden here — board identity, pin/camera-geometry data sources, the
5-camera MIPI array (vs. 01M's 4, no telephoto), and cellular modem power
control (02M wires EC25 as direct GPIO bit-bang, not through a Mini-PCIe
USB-mode mux like 01M).
"""

from __future__ import annotations

import os

from openpilot.system.hardware.rk3588.hardware import RK3588Hardware
from openpilot.system.hardware.base import HardwareCapability
from openpilot.system.hardware.rk3576 import camera_config


class RK3576Hardware(RK3588Hardware):
    """RK3576 platform hardware (ExoPilot 02M).

    Board bring-up data (GPIO/UART/I2C/cellular pin assignments) ships from
    the closed exopilot hal package, same as RK3588Hardware. As of
    2026-08-26 only the BGT60TR13C radar SPI/GPIO and EC25 cellular GPIO are
    populated in hal.platform.rk3576_pins — WiFi/BT chip data has not been
    ported yet, so those attributes stay empty here (graceful degradation,
    same as RK3588Hardware without hal at all).
    """

    try:
        from hal.platform import rk3576_pins
        GPIO = rk3576_pins.GPIO
        CELLULAR = rk3576_pins.CELLULAR
    except ImportError:
        GPIO = {}
        CELLULAR = {}
    # Not yet ported into hal.platform.rk3576_pins (see class docstring).
    WIFI_CHIP = WIFI_INTERFACE = WIFI_TYPE = BT_CHIP = BT_TYPE = BT_HCI = ""
    UART = {}
    I2C = {}
    USB = {}

    try:
        from hal.platform import rk3576_camera_geometry as _cam_geo
    except ImportError:
        _cam_geo = None

    PLATFORM_NAME = "ExoPilot 02M"
    SOC_NAME = "RK3576"
    MIPI_CAMERA_NAMES = ("mono_narrow", "mono_wide", "mono_tele", "stereo_left", "stereo_right")
    HAS_TELE_ROAD = True
    _usb_cameras = camera_config.USB_CAMERAS

    @staticmethod
    def detect() -> bool:
        """Detect RK3576 hardware."""
        try:
            with open('/proc/device-tree/compatible') as f:
                return 'rk3576' in f.read().lower()
        except OSError:
            return False

    def get_device_type(self) -> str:
        return "rk3576"

    def get_platform(self) -> str:
        return "ExoPilot 02M"

    @staticmethod
    def get_cellular_interface() -> str:
        """Return active cellular modem interface for EC25.

        Same EC25 chip and interface naming as ExoPilot 01M
        (wwan0/cdc-wdm in QMI mode, usb0 ECM/RNDIS fallback) — only the
        power-control circuit differs (see modem_power_on/off below), which
        doesn't affect interface detection.
        """
        if os.path.exists("/sys/class/net/wwan0"):
            return "wwan0"
        if os.path.exists("/sys/class/net/usb0"):
            return "usb0"
        return "wwan0"  # Default for ExoPilot 02M QMI mode

    @staticmethod
    def modem_power_on() -> bool:
        """Enable EC25 via direct GPIO bit-bang (ExoPilot 02M).

        Unlike ExoPilot 01M's Mini-PCIe USB-mode mux (no PCIe/USB signal
        switch needed here — EC25 is wired directly), this only needs to
        assert the power-enable GPIO and pulse reset. GPIO numbers come from
        hal.platform.rk3576_pins (EC25_PWR_EN/EC25_RST_N), themselves marked
        `"confirmed": False` there — this sequence (order, pulse widths,
        polarity) has not been validated against a schematic or real
        hardware. Do not treat this as bring-up-verified.
        """
        import time
        try:
            gpio_pwr = RK3576Hardware.GPIO["EC25_PWR_EN"]["num"]
            gpio_rst = RK3576Hardware.GPIO["EC25_RST_N"]["num"]
            for gpio in (gpio_pwr, gpio_rst):
                if not os.path.exists(f"/sys/class/gpio/gpio{gpio}"):
                    with open("/sys/class/gpio/export", "w") as f:
                        f.write(str(gpio))
                with open(f"/sys/class/gpio/gpio{gpio}/direction", "w") as f:
                    f.write("out")

            with open(f"/sys/class/gpio/gpio{gpio_pwr}/value", "w") as f:
                f.write("1")
            time.sleep(0.2)

            # Pulse reset
            with open(f"/sys/class/gpio/gpio{gpio_rst}/value", "w") as f:
                f.write("1")
            time.sleep(0.2)
            with open(f"/sys/class/gpio/gpio{gpio_rst}/value", "w") as f:
                f.write("0")
            time.sleep(0.2)
            with open(f"/sys/class/gpio/gpio{gpio_rst}/value", "w") as f:
                f.write("1")
            return True
        except Exception:
            pass
        return False

    @staticmethod
    def modem_power_off() -> bool:
        """Disable EC25 power-enable GPIO (ExoPilot 02M). See modem_power_on
        for the same "not bring-up-verified" caveat."""
        try:
            gpio_pwr = RK3576Hardware.GPIO["EC25_PWR_EN"]["num"]
            if os.path.exists(f"/sys/class/gpio/gpio{gpio_pwr}"):
                with open(f"/sys/class/gpio/gpio{gpio_pwr}/value", "w") as f:
                    f.write("0")
                return True
        except Exception:
            pass
        return False

    def get_capabilities(self) -> set:
        return {
            HardwareCapability.GPIO,
            HardwareCapability.CAMERA_MIPI,
            HardwareCapability.CAMERA_USB,
            HardwareCapability.V4L2,
            HardwareCapability.NPU,
            HardwareCapability.RGA,
            HardwareCapability.PCIE,
            HardwareCapability.MICROPHONE,
            HardwareCapability.VOICE_INPUT,
        }

    def has_speaker(self) -> bool:
        """ExoPilot 02M has HDMI/DSI display audio paths but no dedicated
        alert speaker confirmed yet (unlike 01M's I2S DAC) — treat as
        unavailable until confirmed, matching this class's fail-closed
        convention for unconfirmed hardware."""
        return False

    def has_voice_input(self) -> bool:
        """ExoPilot 02M has an on-board mic array (BOARD_DATA["exopilot02m"]
        ["features"]["mic"] = True), unlike 01M."""
        return True

    def has_side_cameras(self) -> bool:
        """Detect side cameras at runtime (UVC).

        Unlike RK3588Hardware, does not also probe for an RTS5411S USB hub —
        02M's USB hub chip (if any) has not been confirmed in boards.py, so
        only direct device-path detection is used here.
        """
        left = self._detect_uvc_device("/dev/video-side-left")
        right = self._detect_uvc_device("/dev/video-side-right")
        return left or right

    def get_max_reliable_depth_m(self) -> float:
        """RK3576's wider 160mm stereo baseline (vs. 01M's 80mm) roughly
        doubles reliable depth range — not yet measured on real hardware,
        this is a first-principles estimate (depth accuracy scales with
        baseline), not a validated figure."""
        return 160.0

    def get_camera_config(self, name: str) -> camera_config.CameraConfig | None:
        """Return camera configuration by name."""
        return camera_config.get_camera(name)

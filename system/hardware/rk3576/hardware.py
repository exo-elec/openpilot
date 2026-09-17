#!/usr/bin/env python3
"""RK3576 Hardware Implementation (ExoPilot 02M).

A sibling of RK3588Hardware, not a subclass of it. Both inherit the shared
Rockchip/Linux half from RockchipHardware; what differs between the boards is
here -- board identity, pin and camera-geometry data sources, the 5-camera
MIPI array (against 01M's 4, and this one has a telephoto), and cellular
modem power control (02M wires the EC25 as a direct GPIO bit-bang rather than
through 01M's Mini-PCIe USB-mode mux).

This used to subclass RK3588Hardware. That made 01M's class load-bearing for
02M, so neither board's support could be removed from a branch without
breaking the other's.
"""

from __future__ import annotations

import os

from openpilot.system.hardware.base import HardwareCapability
from openpilot.system.hardware.rk3576 import camera_config
from openpilot.system.hardware.rockchip_base import RockchipHardware


class RK3576Hardware(RockchipHardware):
    """RK3576 platform hardware (ExoPilot 02M).

    Board bring-up data (GPIO/UART/I2C/cellular pin assignments) ships from
    the closed exopilot hal package, same as RK3588Hardware. WiFi/BT chip
    identity (AP6256, current trial board; being revised to AP6275S — see
    exopilot's docs/02-HARDWARE/wifi_corner_nodes.md) and GPS UART
    (ZED-F9P, uart2) are now populated in hal.platform.rk3576_pins; I2C/USB
    topology data is still unported, so those two stay empty here (graceful
    degradation, same as RK3588Hardware without hal at all).
    """

    HAL_PREFIX = "rk3576"

    try:
        from hal.platform import rk3576_pins
        GPIO = rk3576_pins.GPIO
        CELLULAR = rk3576_pins.CELLULAR
        UART = rk3576_pins.UART
        WIFI_CHIP = rk3576_pins.WIFI_CHIP
        WIFI_INTERFACE = rk3576_pins.WIFI_INTERFACE
        WIFI_TYPE = rk3576_pins.WIFI_TYPE
        BT_CHIP = rk3576_pins.BT_CHIP
        BT_TYPE = rk3576_pins.BT_TYPE
        BT_HCI = rk3576_pins.BT_HCI
    except ImportError:
        GPIO = {}
        CELLULAR = {}
        UART = {}
        WIFI_CHIP = WIFI_INTERFACE = WIFI_TYPE = BT_CHIP = BT_TYPE = BT_HCI = ""
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
            # WiFi (AP6256/SDIO, being revised to AP6275S), BT (same chip,
            # UART, unconfirmed transport —
            # see hal.platform.rk3576_pins), GPS (ZED-F9P) and cellular
            # (EC25) are all present on this board. RTK is deliberately not
            # claimed here: no RTCM correction path exists yet (no NTRIP
            # client), so the module has RTK-capable silicon but nothing
            # feeds it corrections — see coordinationd/fusion.py's is_rtk
            # noise branch, which would start trusting an uncorrected fix as
            # centimeter-accurate if this capability were claimed early.
            HardwareCapability.WIFI,
            HardwareCapability.BLUETOOTH,
            HardwareCapability.GPS,
            HardwareCapability.CELLULAR,
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

    # has_side_cameras()/has_rear_camera() come from RockchipHardware
    # unchanged. 02M uses the same RTS5411S hub and the same device paths --
    # confirmed by exopilot/scripts/install/setup_rk3576.sh's USB topology
    # comment and its DT overlay (`exopilot02m-usbhub-rts5411.dtbo`), where
    # side_left/side_right are hub ports 1 and 2. `boards.py`'s BOARD_DATA
    # does not name the hub chip; that install script does.
    def get_max_reliable_depth_m(self) -> float:
        """RK3576's wider 160mm stereo baseline (vs. 01M's 80mm) roughly
        doubles reliable depth range — not yet measured on real hardware,
        this is a first-principles estimate (depth accuracy scales with
        baseline), not a validated figure."""
        return 160.0

    def get_camera_config(self, name: str) -> camera_config.CameraConfig | None:
        """Return camera configuration by name."""
        return camera_config.get_camera(name)

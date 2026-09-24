#!/usr/bin/env python3
"""RK3588 Hardware Implementation (ExoPilot 01M).

Board-specific only. Everything RK3588 shares with the other Rockchip board
-- reboot/shutdown, serial and dongle identity, the network and power stubs,
the USB camera probe, the RGA/MPP/RKNN handles -- lives in RockchipHardware,
which RK3576Hardware inherits as a sibling rather than through this class.
"""

from __future__ import annotations

import os

from openpilot.system.hardware.base import HardwareCapability
from openpilot.system.hardware.rk3588 import camera_config
from openpilot.system.hardware.rockchip_base import RockchipHardware


class RK3588Hardware(RockchipHardware):
    """RK3588 platform hardware (ExoPilot 01M).

    Board bring-up data (GPIO/UART/I2C/cellular pin assignments, USB topology)
    ships from the closed exopilot hal package (see
    exopilot/scripts/install/setup_rk3588.sh) rather than living in this
    public repo. Without it, these dicts are empty and hardware-specific
    methods (modem_power_on/off, etc.) fail closed.
    """

    HAL_PREFIX = "rk3588"

    try:
        from hal.platform import rk3588_pins
        WIFI_CHIP = rk3588_pins.WIFI_CHIP
        WIFI_INTERFACE = rk3588_pins.WIFI_INTERFACE
        WIFI_TYPE = rk3588_pins.WIFI_TYPE
        BT_CHIP = rk3588_pins.BT_CHIP
        BT_TYPE = rk3588_pins.BT_TYPE
        BT_HCI = rk3588_pins.BT_HCI
        GPIO = rk3588_pins.GPIO
        UART = rk3588_pins.UART
        I2C = rk3588_pins.I2C
        CELLULAR = rk3588_pins.CELLULAR
        USB = rk3588_pins.USB
    except ImportError:
        WIFI_CHIP = WIFI_INTERFACE = WIFI_TYPE = BT_CHIP = BT_TYPE = BT_HCI = ""
        GPIO = {}
        UART = {}
        I2C = {}
        CELLULAR = {}
        USB = {}

    try:
        from hal.platform import rk3588_camera_geometry as _cam_geo
    except ImportError:
        _cam_geo = None

    # Platform identity and camera-array shape. RockchipHardware's
    # get_camera_array_config()/get_stereo_baseline_mm() are written against
    # these attributes, so this class supplies data rather than behaviour.
    PLATFORM_NAME = "ExoPilot 01M"
    SOC_NAME = "RK3588"
    MIPI_CAMERA_NAMES = ("road", "wide_road", "stereo_left", "stereo_right")
    HAS_TELE_ROAD = False
    _usb_cameras = camera_config.USB_CAMERAS

    @staticmethod
    def modem_power_on() -> bool:
        """Enable EC25 on the Mini-PCIe slot (RK3588).

        Disables PCIe (HIGH) to enable USB signals, then pulses reset.
        Returns True if GPIO control was attempted.
        """
        import time
        try:
            gpio_dis = RK3588Hardware.GPIO["MINIPCIE_DIS"]["num"]
            gpio_rst = RK3588Hardware.GPIO["MINIPCIE_RST"]["num"]
            # Export GPIOs
            for gpio in (gpio_dis, gpio_rst):
                if not os.path.exists(f"/sys/class/gpio/gpio{gpio}"):
                    with open("/sys/class/gpio/export", "w") as f:
                        f.write(str(gpio))

            # Disable PCIe -> enable USB mode
            with open(f"/sys/class/gpio/gpio{gpio_dis}/direction", "w") as f:
                f.write("out")
            with open(f"/sys/class/gpio/gpio{gpio_dis}/value", "w") as f:
                f.write("1")
            time.sleep(0.5)

            # Pulse reset
            with open(f"/sys/class/gpio/gpio{gpio_rst}/direction", "w") as f:
                f.write("out")
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
        """Disable EC25 Mini-PCIe slot (RK3588)."""
        try:
            gpio_dis = RK3588Hardware.GPIO["MINIPCIE_DIS"]["num"]
            if os.path.exists(f"/sys/class/gpio/gpio{gpio_dis}"):
                with open(f"/sys/class/gpio/gpio{gpio_dis}/value", "w") as f:
                    f.write("0")
                return True
        except Exception:
            pass
        return False

    @staticmethod
    def detect() -> bool:
        """Detect RK3588 hardware."""
        try:
            with open('/proc/device-tree/compatible') as f:
                return 'rk3588' in f.read().lower()
        except OSError:
            return False

    def get_device_type(self) -> str:
        return "rk3588"

    def get_platform(self) -> str:
        return "ExoPilot 01M"

    def get_capabilities(self) -> set:
        return {
            HardwareCapability.GPIO,
            HardwareCapability.CAMERA_MIPI,
            HardwareCapability.CAMERA_USB,
            HardwareCapability.V4L2,
            HardwareCapability.NPU,
            HardwareCapability.RGA,
            HardwareCapability.PCIE,
            HardwareCapability.SPEAKER,
            HardwareCapability.VOICE_INPUT,
        }

    def has_speaker(self) -> bool:
        """ExoPilot 01M has speaker for alert tones and TTS output."""
        return True

    def has_voice_input(self) -> bool:
        """ExoPilot 01M has a 2-mic INMP441-class I2S pair on I2S0 SDI0,
        sharing the bus with the MAX98357A amp (exopilot
        kernel/dts/rk3588-lubancat-exp01.dts simple_sound;
        docs/02-HARDWARE/RK3588_PINMUX_01M.md section 4). The older "no
        on-board mic" note predated that audio design."""
        return True

    def get_max_reliable_depth_m(self) -> float:
        """RK3588 stereo baseline + ISP limits reliable depth to ~80m."""
        return 80.0

    def get_camera_config(self, name: str) -> camera_config.CameraConfig | None:
        """Return camera configuration by name."""
        return camera_config.get_camera(name)

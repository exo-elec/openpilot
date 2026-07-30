#!/usr/bin/env python3
"""RK3588 Hardware Implementation (ExoPilot 01M)."""

from __future__ import annotations

import os
import subprocess
from typing import Any

from openpilot.system.hardware.base import HardwareBase, HardwareCapability
from openpilot.system.hardware.rk_device_id import get_emmc_cid, get_rk_otp_chip_id
from openpilot.system.hardware.rk3588 import camera_config

# Rockchip hardware backends (RGA/MPP/RKNN ctypes bindings)
from openpilot.system.hardware.rockchip import RockchipBackendFactory


class RK3588Hardware(HardwareBase):
    """RK3588 platform hardware (ExoPilot 01M).

    Board bring-up data (GPIO/UART/I2C/cellular pin assignments, USB topology)
    ships from the closed exopilot hal package (see
    exopilot/scripts/install/setup_rk3588.sh) rather than living in this
    public repo. Without it, these dicts are empty and hardware-specific
    methods (modem_power_on/off, etc.) fail closed.
    """

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

    @staticmethod
    def get_cellular_interface() -> str:
        """Return active cellular modem interface for EC25.

        EC25 on RK3588 uses USB-over-Mini-PCIe / M.2 USB pins.
        Priority:
        1. USB ECM/RNDIS (usb0) — EC25
        2. USB QMI (wwan0) — EC25 QMI mode
        """
        import os
        if os.path.exists("/sys/class/net/usb0"):
            return "usb0"
        if os.path.exists("/sys/class/net/wwan0"):
            return "wwan0"
        return "usb0"  # Default for EC25

    @staticmethod
    def get_modem_type() -> str:
        """Auto-detect Quectel EC25 USB modem."""
        import subprocess
        try:
            result = subprocess.run(
                ["lsusb"], capture_output=True, text=True, timeout=5
            )
            output = result.stdout.lower()
            if "2c7c:" in output:  # Quectel vendor ID
                if any(pid in output for pid in ["0125", "0121"]):
                    return "quectel_ec25"
                return "quectel_usb"
        except Exception:
            pass
        return "unknown"

    @staticmethod
    def modem_power_on() -> bool:
        """Enable EC25 on the Mini-PCIe slot (RK3588).

        Disables PCIe (HIGH) to enable USB signals, then pulses reset.
        Returns True if GPIO control was attempted.
        """
        import os
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
        import os
        try:
            gpio_dis = RK3588Hardware.GPIO["MINIPCIE_DIS"]["num"]
            if os.path.exists(f"/sys/class/gpio/gpio{gpio_dis}"):
                with open(f"/sys/class/gpio/gpio{gpio_dis}/value", "w") as f:
                    f.write("0")
                return True
        except Exception:
            pass
        return False

    class Paths:
        """System paths for RK3588."""
        SHM_PATH = "/dev/shm"
        DATA_PATH = "/data/media/0"
        PARAMS_PATH = "/data/params"
    
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
    
    def reboot(self, reason=None):
        subprocess.run(["reboot"], check=False)
    
    def uninstall(self):
        pass
    
    def get_os_version(self):
        return "ubuntu"
    
    def get_imei(self, slot) -> str:
        return ""
    
    def get_serial(self):
        """Return Rockchip OTP chip ID (SoC-bound factory serial)."""
        rk_otp = get_rk_otp_chip_id()
        if rk_otp:
            return rk_otp
        # Fallback to device-tree serial (legacy, easily spoofed on clones)
        try:
            with open('/proc/device-tree/serial-number') as f:
                return f.read().strip('\x00')
        except OSError:
            return "unknown"

    def get_dongle_id(self):
        """Return eMMC CID as dongle ID (persistent across reflashes)."""
        emmc_cid = get_emmc_cid()
        if emmc_cid:
            return emmc_cid
        # Fallback to device-tree serial
        try:
            with open('/proc/device-tree/serial-number') as f:
                return f.read().strip('\x00')
        except OSError:
            return "unknown"
    
    def get_network_info(self):
        return {}
    
    def get_network_type(self):
        return "wifi"
    
    def get_sim_info(self):
        return {}
    
    def get_sim_lpa(self):
        raise NotImplementedError
    
    def get_network_strength(self, network_type):
        return 0
    
    def get_current_power_draw(self):
        return 0
    
    def get_som_power_draw(self):
        return 0
    
    def shutdown(self):
        subprocess.run(["poweroff"], check=False)
    
    def set_screen_brightness(self, percentage):
        pass
    
    def get_screen_brightness(self):
        return 100
    
    def set_power_save(self, powersave_enabled):
        pass
    
    def get_gpu_usage_percent(self):
        return 0
    
    def get_modem_temperatures(self):
        return []
    
    def initialize_hardware(self):
        pass
    
    def get_networks(self):
        return []
    
    def get_camera_array_config(self) -> dict:
        """ExoPilot 01M: 4 MIPI cameras + up to 3 USB cameras via hub.

        Mounting positions/lens data come from hal.platform.rk3588_camera_geometry
        (the same source selfdrive/gridd/camera_geometry.py uses) so this stays
        consistent with the actual calibration/perception geometry rather than
        carrying its own separate copy.

        USB hub cameras (side/rear) are enabled on hardware revisions with
        the USB 3.0 hub populated.
        """
        cam_geo = RK3588Hardware._cam_geo
        _mipi_names = ("road", "wide_road", "stereo_left", "stereo_right")
        if cam_geo is not None:
            mipi_cams = [
                {
                    "name": name,
                    "sensor": cam_geo.SENSOR_TYPE[name].upper(),
                    "lens_mm": cam_geo.LENS_MM[name],
                    "y_offset_mm": cam_geo.POSITIONS_M[name][1] * 1000.0,
                    "fov_deg": cam_geo.FOV_DEG[name],
                }
                for name in _mipi_names
            ]
            stereo_baseline_mm = cam_geo.STEREO_BASELINE_M * 1000.0
        else:
            mipi_cams = []
            stereo_baseline_mm = 0.0
        usb_cams = [
            {"name": c.name, "sensor": c.sensor.value, "lens_mm": 0.0,
             "y_offset_mm": c.y_offset_mm, "fov_deg": c.fov_deg}
            for c in camera_config.USB_CAMERAS
        ]
        return {
            "platform": "ExoPilot 01M",
            "soc": "RK3588",
            "num_cameras": len(mipi_cams) + len(usb_cams),
            "stereo_baseline_mm": stereo_baseline_mm,
            "has_tele_road": False,
            "cameras": mipi_cams + usb_cams,
        }
    
    def get_stereo_baseline_mm(self) -> float:
        cam_geo = RK3588Hardware._cam_geo
        return cam_geo.STEREO_BASELINE_M * 1000.0 if cam_geo is not None else 0.0
    
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
        }

    def has_speaker(self) -> bool:
        """ExoPilot 01M has speaker for alert tones and TTS output."""
        return True

    def has_voice_input(self) -> bool:
        """ExoPilot 01M has no on-board mic — voice input not supported."""
        return False
    
    @staticmethod
    def _detect_uvc_device(device_path: str) -> bool:
        """Check if a V4L2 UVC device is present and responds to queries."""
        import os
        import subprocess
        if not os.path.exists(device_path):
            return False
        try:
            result = subprocess.run(
                ["v4l2-ctl", "-d", device_path, "--all"],
                capture_output=True, text=True, timeout=5
            )
            return result.returncode == 0 and "error" not in result.stderr.lower()
        except Exception:
            return False

    @staticmethod
    def _detect_usb_hub() -> bool:
        """Detect RTS5411S USB 3.0 hub used for side cameras on ExoPilot 01M."""
        import subprocess
        try:
            result = subprocess.run(
                ["lsusb"], capture_output=True, text=True, timeout=5
            )
            output = result.stdout.lower()
            # RTS5411S hub vendor:product == 0bda:5411 (Realtek)
            return "0bda:5411" in output or "rts5411" in output
        except Exception:
            return False

    def has_side_cameras(self) -> bool:
        """Detect side cameras at runtime (UVC via RTS5411S USB hub).

        Returns:
            True if side_left or side_right UVC camera is detected.
        """
        left = self._detect_uvc_device("/dev/video-side-left")
        right = self._detect_uvc_device("/dev/video-side-right")
        hub = self._detect_usb_hub()
        return left or right or hub

    def has_rear_camera(self) -> bool:
        """Detect rear camera at runtime (UVC via shared HOST0 port).

        Original driver face camera is repurposed as rear UVC camera (170°).
        driverd runs in steering-torque-only mode (no face detection).

        Returns:
            True if rear UVC camera is detected.
        """
        return self._detect_uvc_device("/dev/video-rear")

    def get_max_reliable_depth_m(self) -> float:
        """RK3588 stereo baseline + ISP limits reliable depth to ~80m."""
        return 80.0

    def get_camera_config(self, name: str) -> camera_config.CameraConfig | None:
        """Return camera configuration by name."""
        return camera_config.get_camera(name)

    def get_rga(self):
        """Return RGA 2D accelerator instance, or None if librga.so unavailable."""
        return RockchipBackendFactory.create("rga")

    def get_mpp(self):
        """Return MPP decoder handle, or None if librockchip_mpp.so unavailable."""
        return RockchipBackendFactory.create("mpp")

    def get_rknn(self):
        """Return RKNN NPU runtime, or None if librknnrt.so unavailable."""
        return RockchipBackendFactory.create("rknn")

    def npu_available(self) -> bool:
        """Quick check if RK3588 NPU runtime is present."""
        return RockchipBackendFactory.create("rknn") is not None

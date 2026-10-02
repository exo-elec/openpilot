#!/usr/bin/env python3
"""Generic Rockchip platform hardware.

RK3588 (ExoPilot 01M) and RK3576 (ExoPilot 02M) are different boards but the
same Rockchip/Linux userspace: reboot, shutdown, serial and dongle identity,
the network and power stubs, the USB camera probe, and the RGA/MPP/RKNN
backend handles are all identical between them.

That shared half used to live in `RK3588Hardware`, with `RK3576Hardware`
subclassing it. This is that code, lifted into a base both boards inherit as
siblings instead.

The subclassing was wrong in a way that mattered for more than tidiness. It
made 01M's class a load-bearing dependency of 02M, so neither board's support
could be removed from a branch without breaking the other's -- and
`ROCKCHIP = isinstance(HARDWARE, RK3588Hardware)` meant "is this a Rockchip
board" was answered by asking "is this an 01M", which is only true by
accident of the class hierarchy.

A subclass supplies board identity, pin and camera-geometry data, its MIPI
camera array, its modem power-control circuit, and its capability set. It
does not need to reimplement anything here.
"""

from __future__ import annotations

import importlib
import os
import subprocess

from openpilot.system.hardware.base import HardwareBase
from openpilot.system.hardware.rk_device_id import get_emmc_cid, get_rk_otp_chip_id
from openpilot.system.hardware.rockchip import RockchipBackendFactory

# Realtek RTS5411S USB 3.0 hub, used for the side/rear UVC cameras on both
# boards. vendor:product as lsusb reports it.
USB_HUB_ID = "0bda:5411"


class RockchipHardware(HardwareBase):
    """What the ExoPilot boards have in common.

    Board bring-up data (GPIO/UART/I2C/cellular pin assignments, USB
    topology) ships from the closed exopilot hal package rather than living
    in this public repo. The empty defaults below are what a subclass falls
    back to when hal is absent, so hardware-specific methods fail closed
    instead of raising at import.
    """

    # ---- board data, supplied by the subclass ----------------------------

    WIFI_CHIP = WIFI_INTERFACE = WIFI_TYPE = BT_CHIP = BT_TYPE = BT_HCI = ""
    GPIO: dict = {}
    UART: dict = {}
    I2C: dict = {}
    CELLULAR: dict = {}
    USB: dict = {}

    # Platform identity and camera-array shape. get_camera_array_config() and
    # get_stereo_baseline_mm() are written against these, so a board only
    # overrides the attributes, never the methods.
    PLATFORM_NAME = "Rockchip"
    SOC_NAME = "ROCKCHIP"
    MIPI_CAMERA_NAMES: tuple[str, ...] = ()
    HAS_TELE_ROAD = False
    _cam_geo = None
    _usb_cameras: tuple = ()

    # The board's prefix inside the closed hal package: `rk3588` resolves
    # `hal.platform.rk3588_pins`, `hal.platform.rk3588_thermal`, and so on.
    # Subclasses set it; hal_module() below is what daemons call.
    HAL_PREFIX = ""

    class Paths:
        """System paths. Identical on both boards -- same image layout."""
        SHM_PATH = "/dev/shm"
        DATA_PATH = "/data/media/0"
        PARAMS_PATH = "/data/params"

    # ---- board data from the closed hal package --------------------------

    @classmethod
    def hal_module(cls, suffix: str, *, import_module=importlib.import_module):
        """Import `hal.platform.<HAL_PREFIX>_<suffix>` for the running board.

        See HardwareBase.hal_module for why daemons go through this instead
        of importing a board's module by name. Returns None when hal is not
        installed, when the board has no HAL_PREFIX, or when this board has
        no module of that kind -- all of which are ordinary states that the
        caller handles with its in-repo defaults.

        `import_module` is the importer to resolve through. It exists so a
        caller can supply a different one -- a test hands in a stub rather
        than editing sys.modules, which leaks into every test that runs
        after it.
        """
        if not cls.HAL_PREFIX:
            return None
        try:
            return import_module(f"hal.platform.{cls.HAL_PREFIX}_{suffix}")
        except ImportError:
            return None

    # ---- identity --------------------------------------------------------

    def get_os_version(self):
        return "ubuntu"

    def get_serial(self):
        """Rockchip OTP chip ID (SoC-bound factory serial)."""
        rk_otp = get_rk_otp_chip_id()
        if rk_otp:
            return rk_otp
        # Fallback to device-tree serial (legacy, easily spoofed on clones)
        return self._device_tree_serial()

    def get_dongle_id(self):
        """eMMC CID as dongle ID (persistent across reflashes)."""
        emmc_cid = get_emmc_cid()
        if emmc_cid:
            return emmc_cid
        return self._device_tree_serial()

    @staticmethod
    def _device_tree_serial() -> str:
        try:
            with open('/proc/device-tree/serial-number') as f:
                return f.read().strip('\x00')
        except OSError:
            return "unknown"

    def get_imei(self, slot) -> str:
        return ""

    # ---- power and lifecycle --------------------------------------------

    def reboot(self, reason=None):
        subprocess.run(["reboot"], check=False)

    def shutdown(self):
        subprocess.run(["poweroff"], check=False)

    def uninstall(self):
        pass

    def initialize_hardware(self):
        pass

    def get_current_power_draw(self):
        return 0

    def get_som_power_draw(self):
        return 0

    def set_power_save(self, powersave_enabled):
        pass

    def set_screen_brightness(self, percentage):
        pass

    def get_screen_brightness(self):
        return 100

    def get_gpu_usage_percent(self):
        return 0

    # ---- network ---------------------------------------------------------

    def get_network_info(self):
        return {}

    def get_network_type(self):
        return "wifi"

    def get_network_strength(self, network_type):
        return 0

    def get_networks(self):
        return []

    def get_sim_info(self):
        return {}

    def get_sim_lpa(self):
        raise NotImplementedError

    def get_modem_temperatures(self):
        return []

    @staticmethod
    def get_cellular_interface() -> str:
        """Active cellular interface for the EC25.

        Both boards carry the same EC25 in QMI mode, so the kernel exposes
        cdc-wdm + wwan0; the legacy ECM/RNDIS usb0 interface only appears
        when the modem is forced into that mode. Only the power-control
        circuit differs between boards, which does not affect this.
        """
        if os.path.exists("/sys/class/net/wwan0"):
            return "wwan0"
        if os.path.exists("/sys/class/net/usb0"):
            return "usb0"
        return "wwan0"

    @staticmethod
    def get_modem_type() -> str:
        """Auto-detect the Quectel EC25 USB modem."""
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

    # ---- cameras ---------------------------------------------------------

    def get_camera_array_config(self) -> dict:
        """MIPI cameras (per-board, `MIPI_CAMERA_NAMES`) plus up to three USB
        cameras through the hub.

        Mounting positions and lens data come from
        hal.platform.<soc>_camera_geometry -- the same source
        selfdrive/gridd/camera_geometry.py uses -- so this stays consistent
        with the actual calibration geometry rather than carrying a second
        copy of it.
        """
        cam_geo = self._cam_geo
        if cam_geo is not None:
            mipi_cams = [
                {
                    "name": name,
                    "sensor": cam_geo.SENSOR_TYPE[name].upper(),
                    "lens_mm": cam_geo.LENS_MM[name],
                    "y_offset_mm": cam_geo.POSITIONS_M[name][1] * 1000.0,
                    "fov_deg": cam_geo.FOV_DEG[name],
                }
                for name in self.MIPI_CAMERA_NAMES
            ]
            stereo_baseline_mm = cam_geo.STEREO_BASELINE_M * 1000.0
        else:
            mipi_cams = []
            stereo_baseline_mm = 0.0
        usb_cams = [
            {"name": c.name, "sensor": c.sensor.value, "lens_mm": 0.0,
             "y_offset_mm": c.y_offset_mm, "fov_deg": c.fov_deg}
            for c in self._usb_cameras
        ]
        return {
            "platform": self.PLATFORM_NAME,
            "soc": self.SOC_NAME,
            "num_cameras": len(mipi_cams) + len(usb_cams),
            "stereo_baseline_mm": stereo_baseline_mm,
            "has_tele_road": self.HAS_TELE_ROAD,
            "cameras": mipi_cams + usb_cams,
        }

    def get_stereo_baseline_mm(self) -> float:
        cam_geo = self._cam_geo
        return cam_geo.STEREO_BASELINE_M * 1000.0 if cam_geo is not None else 0.0

    @staticmethod
    def _detect_uvc_device(device_path: str) -> bool:
        """Whether a V4L2 UVC device is present and answers queries."""
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
        """Detect the RTS5411S USB 3.0 hub the side cameras hang off."""
        try:
            result = subprocess.run(
                ["lsusb"], capture_output=True, text=True, timeout=5
            )
            output = result.stdout.lower()
            return USB_HUB_ID in output or "rts5411" in output
        except Exception:
            return False

    def has_side_cameras(self) -> bool:
        """Detect side cameras at runtime (UVC via the RTS5411S hub)."""
        left = self._detect_uvc_device("/dev/video-side-left")
        right = self._detect_uvc_device("/dev/video-side-right")
        hub = self._detect_usb_hub()
        return left or right or hub

    def has_rear_camera(self) -> bool:
        """Detect the rear camera at runtime (UVC via the shared HOST0 port).

        The original driver-facing camera is repurposed as a 170-degree rear
        UVC camera; driverd runs in steering-torque-only mode.
        """
        return self._detect_uvc_device("/dev/video-rear")

    # ---- accelerators ----------------------------------------------------

    def get_rga(self):
        """RGA 2D accelerator, or None if librga.so is unavailable."""
        return RockchipBackendFactory.create("rga")

    def get_mpp(self):
        """MPP decoder handle, or None if librockchip_mpp.so is unavailable."""
        return RockchipBackendFactory.create("mpp")

    def get_rknn(self):
        """RKNN NPU runtime, or None if librknnrt.so is unavailable."""
        return RockchipBackendFactory.create("rknn")

    def npu_available(self) -> bool:
        return RockchipBackendFactory.create("rknn") is not None

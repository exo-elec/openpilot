#!/usr/bin/env python3
"""Hardware Base Classes for Rockchip platforms."""

from abc import abstractmethod, ABC
import os
from dataclasses import dataclass, fields
from enum import Enum, auto


class HardwareCapability(Enum):
    """Hardware capabilities."""
    NPU = auto()
    GPU = auto()
    DSP = auto()
    GPIO = auto()
    CAN = auto()
    SPI = auto()
    I2C = auto()
    UART = auto()
    CAMERA_MIPI = auto()
    CAMERA_USB = auto()
    V4L2 = auto()
    RGA = auto()
    ISP = auto()
    GPS = auto()
    RTK = auto()
    EMMC = auto()
    NVME = auto()
    SD_CARD = auto()
    STORAGE = auto()
    ETHERNET = auto()
    WIFI = auto()
    BLUETOOTH = auto()
    CELLULAR = auto()
    PMIC = auto()
    BATTERY = auto()
    TEMP_SENSORS = auto()
    FAN_CONTROL = auto()
    TEE = auto()
    HSM = auto()
    SECURE_BOOT = auto()
    PCIE = auto()
    MICROPHONE = auto()
    SPEAKER = auto()
    VOICE_INPUT = auto()  # Microphone + voice-tier accelerator for Whisper STT


# ---- restored upstream support classes (used by preserved pc/ and tici/ HALs) ----
class LPAError(RuntimeError):
  pass

class LPAProfileNotFoundError(LPAError):
  pass

@dataclass
class Profile:
  iccid: str
  nickname: str
  enabled: bool
  provider: str

@dataclass
class ThermalZone:
  # a zone from /sys/class/thermal/thermal_zone*
  name: str             # a.k.a type
  scale: float = 1000.  # scale to get degrees in C
  zone_number = -1

  def read(self) -> float:
    if self.zone_number < 0:
      for n in os.listdir("/sys/devices/virtual/thermal"):
        if not n.startswith("thermal_zone"):
          continue
        with open(os.path.join("/sys/devices/virtual/thermal", n, "type")) as f:
          if f.read().strip() == self.name:
            self.zone_number = int(n.removeprefix("thermal_zone"))
            break

    try:
      with open(f"/sys/devices/virtual/thermal/thermal_zone{self.zone_number}/temp") as f:
        return int(f.read()) / self.scale
    except FileNotFoundError:
      return 0

@dataclass
class ThermalConfig:
  cpu: list[ThermalZone] | None = None
  gpu: list[ThermalZone] | None = None
  dsp: ThermalZone | None = None
  pmic: list[ThermalZone] | None = None
  memory: ThermalZone | None = None
  intake: ThermalZone | None = None
  exhaust: ThermalZone | None = None
  case: ThermalZone | None = None

  def get_msg(self):
    ret = {}
    for f in fields(ThermalConfig):
      v = getattr(self, f.name)
      if v is not None:
        if isinstance(v, list):
          ret[f.name + "TempC"] = [x.read() for x in v]
        else:
          ret[f.name + "TempC"] = v.read()
    return ret

class LPABase(ABC):
  @abstractmethod
  def list_profiles(self) -> list[Profile]:
    pass

  @abstractmethod
  def get_active_profile(self) -> Profile | None:
    pass

  @abstractmethod
  def delete_profile(self, iccid: str) -> None:
    pass

  @abstractmethod
  def download_profile(self, qr: str, nickname: str | None = None) -> None:
    pass

  @abstractmethod
  def nickname_profile(self, iccid: str, nickname: str) -> None:
    pass

  @abstractmethod
  def switch_profile(self, iccid: str) -> None:
    pass


class HardwareBase(ABC):
    """Base class for hardware platforms."""

    @staticmethod
    @abstractmethod
    def detect() -> bool:
        """Detect if this hardware is present."""

    @abstractmethod
    def get_device_type(self) -> str:
        """Get device type string."""

    @abstractmethod
    def reboot(self, reason=None):
        """Reboot the system."""

    @abstractmethod
    def uninstall(self):
        """Uninstall software."""

    @abstractmethod
    def get_os_version(self):
        """Get OS version."""

    @abstractmethod
    def get_imei(self, slot) -> str:
        """Get IMEI."""

    @abstractmethod
    def get_serial(self):
        """Get hardware serial number."""

    def get_dongle_id(self):
        """Get dongle ID (device identity). Defaults to serial for backward compatibility."""
        return self.get_serial()

    @abstractmethod
    def get_network_info(self):
        """Get network info."""

    @abstractmethod
    def get_network_type(self):
        """Get network type."""

    @abstractmethod
    def get_sim_info(self):
        """Get SIM info."""

    @abstractmethod
    def get_sim_lpa(self):
        """Get LPA."""

    @abstractmethod
    def get_network_strength(self, network_type):
        """Get network strength."""

    @abstractmethod
    def get_current_power_draw(self):
        """Get current power draw."""

    @abstractmethod
    def get_som_power_draw(self):
        """Get SoM power draw."""

    @abstractmethod
    def shutdown(self):
        """Shutdown the system."""

    @abstractmethod
    def set_screen_brightness(self, percentage):
        """Set screen brightness."""

    @abstractmethod
    def get_screen_brightness(self):
        """Get screen brightness."""

    @abstractmethod
    def set_power_save(self, powersave_enabled):
        """Set power save mode."""

    @abstractmethod
    def get_gpu_usage_percent(self):
        """Get GPU usage percentage."""

    @abstractmethod
    def get_modem_temperatures(self):
        """Get modem temperatures."""

    @abstractmethod
    def initialize_hardware(self):
        """Initialize hardware."""

    @abstractmethod
    def get_networks(self):
        """Get available networks."""

    def modem_power_on(self) -> bool:
        """Power on cellular modem.

        Platform-specific implementation (e.g., sysfs GPIO for RK3588 Mini-PCIe).
        Returns True if control attempted.
        """
        return False

    def modem_power_off(self) -> bool:
        """Power off cellular modem."""
        return False

    def get_cellular_interface(self) -> str:
        """Return active cellular network interface (e.g. 'usb0', 'wwan0')."""
        return "usb0"

    def get_modem_type(self) -> str:
        """Return detected cellular modem identifier (e.g. 'quectel_ec25')."""
        return "unknown"

    def get_camera_array_config(self) -> dict:
        """Get camera array configuration."""
        return {
            "platform": "Unknown",
            "num_cameras": 0,
            "stereo_baseline_mm": 0.0,
            "cameras": []
        }

    def get_stereo_baseline_mm(self) -> float:
        """Get stereo baseline in mm."""
        return 0.0

    def get_capabilities(self) -> set:
        """Get hardware capabilities."""
        return set()

    def has_speaker(self) -> bool:
        """Check if platform has speaker for audio output."""
        return False

    def has_voice_input(self) -> bool:
        """Check if platform has voice input hardware (microphone + Hailo NPU)."""
        return False

    def has_side_cameras(self) -> bool:
        """Check if platform has side cameras (UVC via USB 3.0 hub RTS5411S)."""
        return False

    def has_rear_camera(self) -> bool:
        """Check if platform supports a rear-facing USB camera."""
        return False

    def get_max_reliable_depth_m(self) -> float:
        """Get maximum reliable stereo depth distance in meters."""
        return 80.0

    def get_can_bitrate(self) -> int:
        """Return default CAN bitrate in bits per second."""
        return 500000

    def get_camera_hal(self):
        """Return camera HAL for V4L2 driver selection."""
        from openpilot.system.v4l2d.camera_hal import CameraHAL
        return CameraHAL()

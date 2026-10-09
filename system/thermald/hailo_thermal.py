"""Hailo-8 NPU Thermal Monitoring

Monitors Hailo-8 NPU temperature via sysfs or HailoRT API.
The Hailo-8 exposes thermal zones that can be monitored similar
to SoC thermal zones.
"""
import os
from dataclasses import dataclass
from enum import IntEnum

from openpilot.common.swaglog import cloudlog


class HailoThermalStatus(IntEnum):
    """Hailo NPU thermal status."""
    NORMAL = 0      # < 75°C - Normal operation
    ELEVATED = 1    # 75-85°C - Performance may be reduced
    HIGH = 2        # 85-95°C - Throttling active
    CRITICAL = 3    # > 95°C - Risk of shutdown


@dataclass
class HailoThermalZone:
    """Hailo thermal zone reading."""
    name: str
    temperature: float  # Celsius
    status: HailoThermalStatus
    threshold_warning: float = 75.0
    threshold_high: float = 85.0
    threshold_critical: float = 95.0


class HailoThermalMonitor:
    """Monitor Hailo-8 NPU thermal status.

    The Hailo-8 exposes thermal information through:
    1. Sysfs: /sys/class/hailo/hailo0/temp (if driver supports it)
    2. HailoRT API: Device temperature readings
    3. PCIe thermal sensors (if available)

    Temperature thresholds based on Hailo-8 datasheet:
    - Normal: < 75°C
    - Elevated: 75-85°C (may reduce performance)
    - High: 85-95°C (throttling active)
    - Critical: > 95°C (risk of thermal shutdown)

    Args:
        device_id: Hailo device ID (default: 0)
    """

    # Thermal thresholds (Celsius)
    THRESHOLD_WARNING = 75.0
    THRESHOLD_HIGH = 85.0
    THRESHOLD_CRITICAL = 95.0

    # Sysfs paths to check for Hailo thermal
    SYSFS_PATHS = [
        "/sys/class/hailo/hailo{}/temp",
        "/sys/bus/pci/devices/{}/thermal/temp",
        "/sys/class/thermal/hailo_thermal/temp",
    ]

    def __init__(self, device_id: int = 0):
        self.device_id = device_id
        self._sysfs_path: str | None = None
        self._hailort_available = False
        self._last_temp = 0.0

        # Try to find thermal interface
        self._init_thermal_interface()

    def _init_thermal_interface(self):
        """Initialize thermal monitoring interface."""
        # Check sysfs paths
        for path_template in self.SYSFS_PATHS:
            path = path_template.format(self.device_id)
            if os.path.exists(path):
                self._sysfs_path = path
                cloudlog.info(f"HailoThermal: Found sysfs interface at {path}")
                return

        # Try HailoRT API
        try:
            from hailo_platform import HailoRT  # noqa: F401
            self._hailort_available = True
            cloudlog.info("HailoThermal: Using HailoRT API")
        except ImportError:
            cloudlog.warning("HailoThermal: No thermal interface available")

    def _read_sysfs(self) -> float | None:
        """Read temperature from sysfs.

        Returns:
            Temperature in Celsius, or None if unavailable
        """
        if not self._sysfs_path:
            return None

        try:
            with open(self._sysfs_path) as f:
                # Temperature is usually in millidegrees
                temp_raw = f.read().strip()
                temp_millidegrees = int(temp_raw)
                return temp_millidegrees / 1000.0
        except (OSError, ValueError) as e:
            cloudlog.debug(f"HailoThermal: Sysfs read failed: {e}")
            return None

    def _read_hailort(self) -> float | None:
        """Read temperature via HailoRT API.

        Returns:
            Temperature in Celsius, or None if unavailable
        """
        if not self._hailort_available:
            return None

        try:
            from hailo_platform import HailoRT

            # Get device
            device = HailoRT.get_device(self.device_id)
            if device is None:
                return None

            # Read temperature
            # Note: Actual API may differ based on HailoRT version
            temp = device.get_temperature()
            return float(temp)
        except Exception as e:
            cloudlog.debug(f"HailoThermal: HailoRT read failed: {e}")
            return None

    def read(self) -> float | None:
        """Read Hailo NPU temperature.

        Tries sysfs first, then HailoRT API.

        Returns:
            Temperature in Celsius, or None if unavailable
        """
        # Try sysfs first
        temp = self._read_sysfs()
        if temp is not None:
            self._last_temp = temp
            return temp

        # Fallback to HailoRT
        temp = self._read_hailort()
        if temp is not None:
            self._last_temp = temp
            return temp

        return None

    def get_thermal_status(self, temp: float | None = None) -> HailoThermalStatus:
        """Get thermal status for given temperature.

        Args:
            temp: Temperature in Celsius (uses last reading if None)

        Returns:
            Thermal status enum
        """
        if temp is None:
            temp = self._last_temp

        if temp >= self.THRESHOLD_CRITICAL:
            return HailoThermalStatus.CRITICAL
        elif temp >= self.THRESHOLD_HIGH:
            return HailoThermalStatus.HIGH
        elif temp >= self.THRESHOLD_WARNING:
            return HailoThermalStatus.ELEVATED
        else:
            return HailoThermalStatus.NORMAL

    def get_thermal_zone(self) -> HailoThermalZone | None:
        """Get complete thermal zone information.

        Returns:
            HailoThermalZone with current readings, or None if unavailable
        """
        temp = self.read()
        if temp is None:
            return None

        status = self.get_thermal_status(temp)

        return HailoThermalZone(
            name=f"hailo_{self.device_id}",
            temperature=temp,
            status=status,
            threshold_warning=self.THRESHOLD_WARNING,
            threshold_high=self.THRESHOLD_HIGH,
            threshold_critical=self.THRESHOLD_CRITICAL
        )

    def should_throttle(self, temp: float | None = None) -> bool:
        """Check if NPU should throttle due to temperature.

        Args:
            temp: Temperature in Celsius (uses last reading if None)

        Returns:
            True if throttling recommended
        """
        status = self.get_thermal_status(temp)
        return status >= HailoThermalStatus.HIGH

    def is_critical(self, temp: float | None = None) -> bool:
        """Check if temperature is critical.

        Args:
            temp: Temperature in Celsius (uses last reading if None)

        Returns:
            True if temperature is critical
        """
        status = self.get_thermal_status(temp)
        return status == HailoThermalStatus.CRITICAL

    def available(self) -> bool:
        """Check if thermal monitoring is available."""
        return self._sysfs_path is not None or self._hailort_available


def discover_hailo_devices() -> dict[int, str]:
    """Discover available Hailo devices.

    Returns:
        Dictionary mapping device IDs to device names
    """
    devices = {}

    # Check sysfs for hailo devices
    hailo_class = "/sys/class/hailo"
    if os.path.exists(hailo_class):
        try:
            for entry in os.listdir(hailo_class):
                if entry.startswith("hailo"):
                    try:
                        device_id = int(entry.replace("hailo", ""))
                        devices[device_id] = entry
                    except ValueError:
                        continue
        except OSError:
            pass

    # Check PCIe devices
    pci_bus = "/sys/bus/pci/devices"
    if os.path.exists(pci_bus):
        try:
            for entry in os.listdir(pci_bus):
                vendor_path = os.path.join(pci_bus, entry, "vendor")
                if os.path.exists(vendor_path):
                    try:
                        with open(vendor_path) as f:
                            vendor = f.read().strip()
                        # Hailo vendor ID is 0x1e7c
                        if vendor == "0x1e7c":
                            device_id = len(devices)  # Assign sequential ID
                            devices[device_id] = f"hailo_pcie_{entry}"
                    except OSError:
                        continue
        except OSError:
            pass

    return devices


# Convenience function
def get_hailo_thermal(device_id: int = 0) -> HailoThermalZone | None:
    """Get Hailo thermal zone for device.

    Args:
        device_id: Hailo device ID

    Returns:
        HailoThermalZone or None if unavailable
    """
    monitor = HailoThermalMonitor(device_id)
    return monitor.get_thermal_zone()

"""Thermal zone auto-discovery for RK3588 and Hailo-8.

Provides automatic discovery of thermal zones by type name,
eliminating hardcoded paths. Includes Hailo-8 NPU thermal monitoring
where a Hailo-8 accelerator is present.
"""
import os
from dataclasses import dataclass

from openpilot.system.thermald.hailo_thermal import HailoThermalMonitor, discover_hailo_devices

try:
  from hal.platform.rk3588_thermal import THERMAL_ZONE_TYPE_MAP as _TYPE_MAP
except ImportError:
  _TYPE_MAP = {}  # type: ignore[assignment]


@dataclass
class ThermalZone:
    """Single thermal zone interface.

    Wraps access to Linux thermal zone sysfs entries.
    Auto-discovers zone number from thermal zone type name.

    Args:
        name: Human-readable name for this zone
        zone_num: Thermal zone number (e.g., 0 for thermal_zone0)
        critical_temp: Shutdown threshold in Celsius
        warning_temp: Throttle threshold in Celsius
    """
    name: str
    zone_num: int
    critical_temp: float = 85.0
    warning_temp: float = 75.0

    def read(self) -> float | None:
        """Read current temperature in Celsius.

        Returns:
            Temperature in Celsius, or None if read fails.
        """
        try:
            path = f"/sys/class/thermal/thermal_zone{self.zone_num}/temp"
            with open(path) as f:
                temp_millidegrees = int(f.read().strip())
                return temp_millidegrees / 1000.0
        except Exception:
            return None

    def available(self) -> bool:
        """Check if zone is available for reading."""
        path = f"/sys/class/thermal/thermal_zone{self.zone_num}/temp"
        return os.path.exists(path)


def discover_zones() -> dict[str, ThermalZone]:
    """Auto-discover thermal zones by type name.

    Scans /sys/class/thermal/ for thermal zones and maps them
    to standard names based on their type. Also discovers Hailo-8
    NPU thermal zones where present.

    Returns:
        Dictionary mapping zone names to ThermalZone objects.
        Common names: CPU, NPU, GPU, PMIC, HAILO
    """
    zones = {}
    base = "/sys/class/thermal"

    # Map of thermal zone types to standard names and thresholds
    type_map = _TYPE_MAP or {
        "cpu_thermal": ("CPU", 85.0, 75.0),
        "npu_thermal": ("NPU", 85.0, 75.0),
        "gpu_thermal": ("GPU", 85.0, 75.0),
        "pmic_thermal": ("PMIC", 90.0, 80.0),
        "soc_thermal": ("SoC", 85.0, 75.0),
    }

    try:
        for entry in os.listdir(base):
            if not entry.startswith("thermal_zone"):
                continue
            try:
                zone_num = int(entry.replace("thermal_zone", ""))
                type_path = os.path.join(base, entry, "type")

                with open(type_path) as f:
                    zone_type = f.read().strip()

                # Map to standard name if known
                if zone_type in type_map:
                    name, critical, warning = type_map[zone_type]
                    zones[name] = ThermalZone(name, zone_num, critical, warning)
                else:
                    # Unknown type - use type as name
                    zones[zone_type] = ThermalZone(zone_type, zone_num, 85.0, 75.0)

            except (OSError, PermissionError, ValueError):
                continue
    except FileNotFoundError:
        pass

    return zones


def discover_hailo_zones() -> list[HailoThermalMonitor]:
    """Discover Hailo-8 NPU thermal monitors.

    Returns:
        List of HailoThermalMonitor instances for each Hailo device
    """
    hailo_devices = discover_hailo_devices()
    monitors = []

    for device_id in hailo_devices:
        monitor = HailoThermalMonitor(device_id)
        if monitor.available():
            monitors.append(monitor)

    return monitors


try:
  from hal.platform.rk3588_thermal import DEFAULT_THERMAL_ZONES as _DEFAULT_ZONES
except ImportError:
  _DEFAULT_ZONES = {
    "CPU": (0, 85.0, 75.0),
    "GPU": (1, 85.0, 75.0),
    "NPU": (2, 85.0, 75.0),
    "PMIC": (3, 90.0, 80.0),
  }


def get_default_zones() -> dict[str, ThermalZone]:
    """Get default thermal zones for RK3588.

    Returns zones from the closed HAL, falling back to hardcoded values when
    auto-discovery fails or the HAL is not installed.
    """
    return {
        name: ThermalZone(name, num, critical, warning)
        for name, (num, critical, warning) in _DEFAULT_ZONES.items()
    }

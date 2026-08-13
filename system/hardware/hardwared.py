#!/usr/bin/env python3
"""
hardwared - Hardware Management Daemon for Rockchip RK3588

Manages:
- PMIC power monitoring (RK806S voltage rails)
- Hardware initialization and configuration
- Core affinity management
- Car power/ignition detection
- Integration with thermal management

Publishes:
  - hardwareState: Power state, hardware status, and thermal info

This consolidates functionality from the separate power_monitor daemon
into the standard OpenPilot hardwared pattern.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from enum import IntEnum

from cereal import messaging, log
from openpilot.common.realtime import Ratekeeper
from openpilot.common.swaglog import cloudlog
from openpilot.common.core_config import set_daemon_affinity


class RailStatus(IntEnum):
    """Power rail status."""
    OK = 0
    WARNING = 1
    CRITICAL = 2
    UNKNOWN = 3


@dataclass
class PowerRail:
    """Power rail information."""
    name: str
    nominal_voltage: float
    voltage: float = 0.0
    enabled: bool = False
    status: RailStatus = RailStatus.UNKNOWN

    def check_status(self) -> RailStatus:
        """Update and return rail status based on voltage."""
        if self.voltage <= 0:
            self.status = RailStatus.UNKNOWN
        elif self.voltage < self.nominal_voltage * 0.9:
            self.status = RailStatus.CRITICAL
        elif self.voltage < self.nominal_voltage * 0.95:
            self.status = RailStatus.WARNING
        else:
            self.status = RailStatus.OK
        return self.status


class HardwareD:
    """Hardware management daemon for RK3588 platforms."""

    # RK806S regulator definitions for RK3588
    REGULATORS: list[tuple[str, float]] = [
        ("vdd_logic", 0.8),   # SoC logic voltage
        ("vdd_arm", 0.9),     # CPU voltage
        ("vdd_gpu", 0.8),     # GPU voltage
        ("vdd_npu", 0.8),     # NPU voltage
        ("vdd_ddr", 1.1),     # DDR memory voltage
        ("vcc_3v3", 3.3),     # 3.3V IO
        ("vcc_1v8", 1.8),     # 1.8V IO
        ("vcc_sdio", 3.3),    # SDIO voltage
    ]

    def __init__(self):
        set_daemon_affinity("hardwared")

        self.pm = messaging.PubMaster(['powerState'])

        # Initialize power rail tracking
        self.rails: dict[str, PowerRail] = {}
        for name, nominal in self.REGULATORS:
            self.rails[name] = PowerRail(name, nominal)

        self.car_power_connected = False
        self.last_under_voltage_report: list[str] | None = None

        self.initialized = False

        cloudlog.info("hardwared: Initialized")

    def _init_hardware(self) -> bool:
        """Initialize hardware configuration."""
        try:
            self._set_governor("/sys/class/devfreq/ffa30000.npu/governor", "performance")
            self._set_governor("/sys/class/devfreq/dmc/governor", "performance")

            # Ensure camera nodes are accessible
            self._fix_camera_permissions()

            return True
        except Exception as e:
            cloudlog.error(f"hardwared: Hardware init failed: {e}")
            return False

    def _set_governor(self, path: str, governor: str) -> bool:
        """Set CPU/GPU/NPU governor."""
        try:
            if os.path.exists(path):
                with open(path, 'w') as f:
                    f.write(governor)
                return True
        except (OSError, PermissionError) as e:
            cloudlog.debug(f"hardwared: Could not set governor {path}: {e}")
        return False

    def _fix_camera_permissions(self):
        """Fix camera device permissions."""
        try:
            for pattern in ["/dev/video*", "/dev/media*", "/dev/v4l-subdev*"]:
                import glob
                for device in glob.glob(pattern):
                    os.chmod(device, 0o666)
        except Exception as e:
            cloudlog.debug(f"hardwared: Camera permission fix failed: {e}")

    def _read_rail_voltage(self, name: str) -> float | None:
        """Read voltage from regulator sysfs."""
        try:
            path = f"/sys/class/regulator/{name}/microvolts"
            if os.path.exists(path):
                with open(path) as f:
                    microvolts = int(f.read().strip())
                    return microvolts / 1_000_000.0
        except (OSError, ValueError, PermissionError):
            pass
        return None

    def _read_rail_status(self, name: str) -> bool:
        """Check if regulator is enabled."""
        try:
            path = f"/sys/class/regulator/{name}/status"
            if os.path.exists(path):
                with open(path) as f:
                    return f.read().strip() == "enabled"
        except (OSError, PermissionError):
            pass
        return False

    def _read_car_power(self) -> bool:
        """Read car power/ignition connection status."""
        # Try RK806 charger first
        try:
            path = "/sys/class/power_supply/rk806-charger/online"
            if os.path.exists(path):
                with open(path) as f:
                    return f.read().strip() == "1"
        except (OSError, PermissionError):
            pass

        # Fallback to USB power
        try:
            path = "/sys/class/power_supply/usb/online"
            if os.path.exists(path):
                with open(path) as f:
                    return f.read().strip() == "1"
        except (OSError, PermissionError):
            pass

        # Fallback to DC power
        try:
            path = "/sys/class/power_supply/dc/online"
            if os.path.exists(path):
                with open(path) as f:
                    return f.read().strip() == "1"
        except (OSError, PermissionError):
            pass

        return False

    def _update_rails(self):
        """Update all rail voltages and statuses."""
        for name, rail in self.rails.items():
            voltage = self._read_rail_voltage(name)
            if voltage is not None:
                rail.voltage = voltage
            rail.enabled = self._read_rail_status(name)
            rail.check_status()

    def _check_under_voltage(self) -> list[str]:
        """Check for under-voltage conditions."""
        under_voltage = []
        for name, rail in self.rails.items():
            if rail.status == RailStatus.CRITICAL:
                under_voltage.append(f"{name}({rail.voltage:.2f}V)")
        return under_voltage

    def _log_under_voltage(self, under_voltage: list[str]):
        """Log under-voltage conditions with rate limiting."""
        if under_voltage != self.last_under_voltage_report:
            if under_voltage:
                cloudlog.error(f"hardwared: Under-voltage: {', '.join(under_voltage)}")
            else:
                cloudlog.info("hardwared: All rails normal")
            self.last_under_voltage_report = under_voltage.copy()

    def update(self):
        """Main update loop."""
        # Initialize hardware on first run
        if not self.initialized:
            self.initialized = self._init_hardware()

        # Read car power status
        self.car_power_connected = self._read_car_power()

        # Update rail readings
        self._update_rails()

        # Check for under-voltage
        under_voltage = self._check_under_voltage()
        self._log_under_voltage(under_voltage)

        # Publish power state
        msg = messaging.new_message('powerState', valid=True)
        ps = msg.powerState

        ps.carPowerConnected = self.car_power_connected

        # Add power rail statuses
        for name, rail in self.rails.items():
            entry = log.PowerState.RailStatus.new_message()
            entry.name = name
            entry.voltage = rail.voltage
            entry.status = rail.status
            entry.enabled = rail.enabled
            ps.rails.append(entry)

        self.pm.send('powerState', msg)

    def run(self):
        """Main daemon loop."""
        rk = Ratekeeper(1)  # 1Hz
        cloudlog.info("hardwared: Running")

        while True:
            self.update()
            rk.keep_time()


def main():
    daemon = HardwareD()
    try:
        daemon.run()
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()

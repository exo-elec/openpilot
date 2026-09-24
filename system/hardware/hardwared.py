#!/usr/bin/env python3
"""
hardwared - Hardware Management Daemon for ExoPilot 02M (RK3576)

Manages:
- PMIC power monitoring: every kernel regulator with a voltage, found by its
  sysfs name, checked against its own device-tree constraints
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

import glob
import os
from dataclasses import dataclass
from enum import IntEnum

from cereal import messaging
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


REGULATOR_SYSFS = "/sys/class/regulator"
DEVFREQ_SYSFS = "/sys/class/devfreq"


def _read(path: str) -> str | None:
    try:
        with open(path) as f:
            return f.read().strip()
    except (OSError, PermissionError):
        return None


def discover_regulators(root: str | None = None) -> dict[str, str]:
    """Rail name -> its sysfs dir, for every regulator that reports a voltage.

    The kernel names these dirs regulator.N and keeps the rail name (from the
    device tree's regulator-name) in `name`, so rails are found by name
    rather than by a hardcoded per-board list. Duplicate names keep the first.
    """
    rails: dict[str, str] = {}
    for d in sorted(glob.glob(os.path.join(root or REGULATOR_SYSFS, "regulator.*"))):
        name = _read(os.path.join(d, "name"))
        if name and name not in rails and _read(os.path.join(d, "microvolts")) is not None:
            rails[name] = d
    return rails


def discover_devfreq_governor(kind: str, root: str | None = None) -> str | None:
    """Governor node of a devfreq device by kind ('npu', 'dmc'): <addr>.npu
    differs per SoC (ffa30000.npu on RK3588), so it is found, not hardcoded."""
    for pattern in (f"*.{kind}/governor", f"{kind}/governor"):
        hits = sorted(glob.glob(os.path.join(root or DEVFREQ_SYSFS, pattern)))
        if hits:
            return hits[0]
    return None


class HardwareD:
    """Hardware management daemon for ExoPilot 02M (RK3576).

    Rails: 02M's PMIC rail names are not in this repo's board DTS (the PMIC
    node comes from the vendor base tree), so no list is written here.
    Every kernel regulator that reports a voltage is monitored, and its
    nominal is its own device-tree lower constraint (min_microvolts), or the
    first voltage read when the DT sets none. Note that regulator
    `microvolts` is the programmed setpoint, not a measured value: this
    catches a rail programmed or dropped below its constraint, not sag under
    load (that needs an ADC).
    """

    def __init__(self):
        set_daemon_affinity("hardwared")

        self.pm = messaging.PubMaster(['powerState'])

        # Power rails, discovered from sysfs (see class docstring)
        self.rail_dirs = discover_regulators()
        self.rails: dict[str, PowerRail] = {}
        for name, d in self.rail_dirs.items():
            min_uv = _read(os.path.join(d, "min_microvolts"))
            nominal = int(min_uv) / 1e6 if min_uv and min_uv.isdigit() and int(min_uv) > 0 else 0.0
            self.rails[name] = PowerRail(name, nominal)
        cloudlog.info(f"hardwared: monitoring {len(self.rails)} regulators")

        self.car_power_connected = False
        self.last_under_voltage_report: list[str] | None = None

        self.initialized = False

        cloudlog.info("hardwared: Initialized")

    def _init_hardware(self) -> bool:
        """Initialize hardware configuration."""
        try:
            for kind in ("npu", "dmc"):
                path = discover_devfreq_governor(kind)
                if path:
                    self._set_governor(path, "performance")

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
                for device in glob.glob(pattern):
                    os.chmod(device, 0o666)
        except Exception as e:
            cloudlog.debug(f"hardwared: Camera permission fix failed: {e}")

    def _read_rail_voltage(self, name: str) -> float | None:
        """Programmed voltage of a rail (regulator.N/microvolts)."""
        v = _read(os.path.join(self.rail_dirs[name], "microvolts"))
        return int(v) / 1_000_000.0 if v and v.lstrip("-").isdigit() else None

    def _read_rail_status(self, name: str) -> bool:
        """Rail enabled? The sysfs attribute is `state` (enabled/disabled)."""
        return _read(os.path.join(self.rail_dirs[name], "state")) == "enabled"

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
                if rail.nominal_voltage <= 0 and voltage > 0:
                    rail.nominal_voltage = voltage   # no DT constraint: first reading
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

        # Rail statuses (capnp lists are sized up front; they have no append)
        rails = ps.init('rails', len(self.rails))
        for entry, (name, rail) in zip(rails, self.rails.items(), strict=True):
            entry.name = name
            entry.voltage = rail.voltage
            entry.status = int(rail.status)
            entry.enabled = rail.enabled

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

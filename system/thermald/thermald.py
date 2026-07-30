#!/usr/bin/env python3
"""
thermald - Thermal Management Daemon for Rockchip RK3588 and Hailo-8

Manages:
- CPU/GPU/NPU thermal monitoring (with auto-discovery)
- Hailo-8 NPU thermal monitoring (where present)
- Fan speed control (PWM) with hysteresis bands
- Active thermal protection (frequency throttling)
- Emergency shutdown on critical temperature

Features:
- Passive cooling: Fan control with hysteresis
- Active cooling: NPU/GPU frequency throttling at 85°C
- Emergency protection: Shutdown at 105°C

Publishes:
  - thermalStatus: Temperature readings, fan speed, and thermal status
"""

from __future__ import annotations

import os
import subprocess
import time
from enum import Enum
from dataclasses import dataclass

from cereal import messaging, log
from openpilot.common.realtime import Ratekeeper
from openpilot.common.swaglog import cloudlog
from openpilot.common.core_config import set_daemon_affinity

from openpilot.system.thermald.thermal_zones import (
    discover_zones, get_default_zones, ThermalZone, 
    discover_hailo_zones
)
from openpilot.system.thermald.fan_control import FanController, ThermalStatus as FanThermalStatus
from openpilot.system.thermald.hailo_thermal import HailoThermalMonitor, HailoThermalStatus


class ProtectionStatus(Enum):
    """Thermal protection status levels."""
    NORMAL = "normal"
    WARNING = "warning"
    THROTTLING = "throttling"
    CRITICAL = "critical"


@dataclass
class ThermalProtectionConfig:
    """Thermal protection configuration."""
    warning_temp: float = 75.0
    throttle_temp: float = 85.0
    critical_temp: float = 105.0
    hysteresis: float = 5.0
    enable_throttling: bool = True
    enable_shutdown: bool = True


def _discover_devfreq_governor(candidates: list[str]) -> str | None:
    """Return the first existing devfreq governor sysfs path from candidates."""
    for path in candidates:
        if os.path.exists(path):
            return path
    return None


class ThermalProtection:
    """Active thermal protection with frequency management."""

    _NPU_GOV_CANDIDATES = [
        "/sys/class/devfreq/ffa30000.npu/governor",  # RK3588
    ]
    _GPU_GOV_CANDIDATES = [
        "/sys/class/devfreq/fb000000.gpu/governor",  # RK3588
        "/sys/class/devfreq/ff9a0000.gpu/governor",  # RK3588 alt
    ]

    def __init__(self, config: ThermalProtectionConfig | None = None):
        self.config = config or ThermalProtectionConfig()
        self.status = ProtectionStatus.NORMAL

        self._throttling_active = False
        self._original_npu_gov: str | None = None
        self._original_gpu_gov: str | None = None

        self.NPU_GOV_PATH = _discover_devfreq_governor(self._NPU_GOV_CANDIDATES)
        self.GPU_GOV_PATH = _discover_devfreq_governor(self._GPU_GOV_CANDIDATES)

        self._npu_available = self.NPU_GOV_PATH is not None
        self._gpu_available = self.GPU_GOV_PATH is not None

        if not self._npu_available:
            cloudlog.debug("thermald: NPU governor not available")
        if not self._gpu_available:
            cloudlog.debug("thermald: GPU governor not available")
    
    def _read_governor(self, path: str) -> str | None:
        try:
            with open(path, 'r') as f:
                return f.read().strip()
        except (IOError, PermissionError):
            return None
    
    def _write_governor(self, path: str, governor: str) -> bool:
        try:
            with open(path, 'w') as f:
                f.write(governor)
            return True
        except (IOError, PermissionError) as e:
            cloudlog.debug(f"thermald: Failed to set governor: {e}")
            return False
    
    def _save_original_governors(self):
        if self._original_npu_gov is None and self._npu_available:
            self._original_npu_gov = self._read_governor(self.NPU_GOV_PATH)
        if self._original_gpu_gov is None and self._gpu_available:
            self._original_gpu_gov = self._read_governor(self.GPU_GOV_PATH)
    
    def _reduce_frequencies(self):
        if not self.config.enable_throttling:
            return
        
        self._save_original_governors()
        
        if self._npu_available:
            if self._write_governor(self.NPU_GOV_PATH, "powersave"):
                cloudlog.info("thermald: NPU throttled to powersave")
        
        if self._gpu_available:
            if self._write_governor(self.GPU_GOV_PATH, "powersave"):
                cloudlog.info("thermald: GPU throttled to powersave")
        
        self._throttling_active = True
    
    def _restore_frequencies(self):
        if not self.config.enable_throttling:
            return
        
        if self._npu_available and self._original_npu_gov:
            if self._write_governor(self.NPU_GOV_PATH, self._original_npu_gov):
                cloudlog.info(f"thermald: NPU restored to {self._original_npu_gov}")
        
        if self._gpu_available and self._original_gpu_gov:
            if self._write_governor(self.GPU_GOV_PATH, self._original_gpu_gov):
                cloudlog.info(f"thermald: GPU restored to {self._original_gpu_gov}")
        
        self._throttling_active = False
    
    def _emergency_shutdown(self):
        if not self.config.enable_shutdown:
            cloudlog.error("thermald: CRITICAL TEMP - shutdown disabled!")
            return
        
        cloudlog.fatal("thermald: EMERGENCY SHUTDOWN due to critical temperature")
        
        try:
            subprocess.run(["sync"], check=False)
            time.sleep(0.5)
            subprocess.run(["poweroff", "-f"], check=False)
        except Exception as e:
            cloudlog.fatal(f"thermald: Shutdown failed: {e}")
            try:
                with open("/proc/sysrq-trigger", 'w') as f:
                    f.write('o')
            except Exception:
                pass
    
    def check_temperature(self, temp: float) -> ProtectionStatus:
        new_status = self._determine_status(temp)
        
        if new_status != self.status:
            self._handle_status_change(new_status, temp)
        
        self._apply_throttling(temp)
        self.status = new_status
        return new_status
    
    def _determine_status(self, temp: float) -> ProtectionStatus:
        if temp >= self.config.critical_temp:
            return ProtectionStatus.CRITICAL
        elif temp >= self.config.throttle_temp:
            return ProtectionStatus.THROTTLING
        elif temp >= self.config.warning_temp:
            return ProtectionStatus.WARNING
        
        # Hysteresis for moving to lower status
        if self.status == ProtectionStatus.CRITICAL:
            if temp < self.config.critical_temp - self.config.hysteresis:
                return ProtectionStatus.THROTTLING
            return ProtectionStatus.CRITICAL
        elif self.status == ProtectionStatus.THROTTLING:
            if temp < self.config.throttle_temp - self.config.hysteresis:
                return ProtectionStatus.WARNING
            return ProtectionStatus.THROTTLING
        elif self.status == ProtectionStatus.WARNING:
            if temp < self.config.warning_temp - self.config.hysteresis:
                return ProtectionStatus.NORMAL
            return ProtectionStatus.WARNING
        
        return ProtectionStatus.NORMAL
    
    def _handle_status_change(self, new_status: ProtectionStatus, temp: float):
        old_status = self.status
        
        if new_status == ProtectionStatus.CRITICAL:
            cloudlog.fatal(f"thermald: CRITICAL {temp:.1f}°C - Emergency shutdown")
            self._emergency_shutdown()
        elif new_status == ProtectionStatus.THROTTLING:
            if old_status != ProtectionStatus.THROTTLING:
                cloudlog.error(f"thermald: HIGH {temp:.1f}°C - Throttling activated")
        elif new_status == ProtectionStatus.WARNING:
            if old_status == ProtectionStatus.NORMAL:
                cloudlog.warning(f"thermald: Elevated {temp:.1f}°C")
        elif new_status == ProtectionStatus.NORMAL:
            if old_status != ProtectionStatus.NORMAL:
                cloudlog.info(f"thermald: Normal {temp:.1f}°C - Full performance restored")
    
    def _apply_throttling(self, temp: float):
        should_throttle = temp >= self.config.throttle_temp
        
        if should_throttle and not self._throttling_active:
            self._reduce_frequencies()
        elif not should_throttle and self._throttling_active:
            if temp < self.config.throttle_temp - self.config.hysteresis:
                self._restore_frequencies()
    
    def cleanup(self):
        if self._throttling_active:
            self._restore_frequencies()


class ThermalD:
    """Thermal management daemon with passive and active cooling."""
    
    def __init__(self):
        set_daemon_affinity("thermald")
        
        self.pm = messaging.PubMaster(['thermalStatus'])
        
        # Thermal zones
        self.zones = discover_zones()
        if not self.zones:
            cloudlog.warning("thermald: Auto-discovery failed, using defaults")
            self.zones = get_default_zones()
        cloudlog.info(f"thermald: Discovered zones: {list(self.zones.keys())}")
        
        # Hailo monitors
        self.hailo_monitors: list[HailoThermalMonitor] = discover_hailo_zones()
        if self.hailo_monitors:
            cloudlog.info(f"thermald: Discovered {len(self.hailo_monitors)} Hailo device(s)")
        
        # Fan control (passive cooling)
        self.fan_controller = FanController()
        self.fan_speed = 0
        
        # Thermal protection (active cooling)
        self.protection = ThermalProtection()
        
        # Temperature readings
        self.temps: dict[str, float] = {}
        self.hailo_temps: dict[str, float] = {}
        self.max_temp = 0.0
        
        self.throttling = False
        self.hailo_throttling = False
        self.shutdown_requested = False
        self._last_update = time.monotonic()
        self._last_fan_status: str = ""
        self._last_prot_status: str = ""
        
        cloudlog.info("thermald: Initialized")
    
    def _read_zone(self, zone: ThermalZone) -> float | None:
        temp = zone.read()
        if temp is None:
            cloudlog.debug(f"thermald: Failed to read {zone.name}")
        return temp
    
    def _read_hailo_temps(self) -> dict[str, float]:
        temps = {}
        any_hot = False
        for monitor in self.hailo_monitors:
            temp = monitor.read()
            if temp is not None:
                temps[f"HAILO_{monitor.device_id}"] = temp
                if monitor.should_throttle(temp):
                    any_hot = True

        if any_hot:
            if not self.hailo_throttling:
                cloudlog.warning("thermald: Hailo throttling active")
                self.hailo_throttling = True
        elif self.hailo_throttling:
            # Only clear throttling when every monitor read successfully and all are cool.
            # A failed read (not in temps) is treated as still hot.
            all_read = len(temps) == len(self.hailo_monitors)
            if all_read:
                cloudlog.info("thermald: Hailo throttling disabled")
                self.hailo_throttling = False
        return temps
    
    def _set_fan_speed(self, speed: int):
        speed = max(0, min(100, speed))
        if speed == self.fan_speed:
            return
        self.fan_speed = speed
        cloudlog.debug(f"thermald: Fan speed {speed}%")
    
    def update(self):
        now = time.monotonic()
        dt = now - self._last_update
        self._last_update = now
        
        # Read all temperatures
        self.temps = {}
        for name, zone in self.zones.items():
            temp = self._read_zone(zone)
            if temp is not None:
                self.temps[name] = temp
        
        self.hailo_temps = self._read_hailo_temps()
        all_temps = {**self.temps, **self.hailo_temps}
        
        if not all_temps:
            cloudlog.warning("thermald: No temperature readings")
            return
        
        self.max_temp = max(all_temps.values())
        
        # Passive cooling: Fan control
        target_speed = self.fan_controller.update(self.max_temp, dt)
        self._set_fan_speed(target_speed)
        
        # Active cooling: Frequency throttling
        protection_status = self.protection.check_temperature(self.max_temp)
        
        # Check for emergency shutdown from fan controller
        if self.fan_controller.should_shutdown(critical_temp=100.0):
            if not self.shutdown_requested:
                cloudlog.error(f"thermald: CRITICAL TEMP {self.max_temp}C - Requesting shutdown")
                self.shutdown_requested = True
        
        # Publish status
        msg = messaging.new_message('thermalStatus', valid=True)
        ts = msg.thermalStatus
        
        ts.cpu = self.temps.get('CPU', 0.0)
        ts.gpu = self.temps.get('GPU', 0.0)
        ts.mem = self.temps.get('NPU', self.temps.get('SoC', 0.0))
        ts.fanSpeed = self.fan_speed
        ts.thermalThrottling = (protection_status == ProtectionStatus.THROTTLING or 
                               self.hailo_throttling)
        
        # Add thermal zones (capnp requires init() with count, then index access)
        all_zones = list(self.temps.items()) + list(self.hailo_temps.items())
        zones = ts.init('thermalZones', len(all_zones))
        for i, (name, temp) in enumerate(all_zones):
            zones[i].name = name
            zones[i].temp = temp
        
        self.pm.send('thermalStatus', msg)
        
        # Log status changes
        fan_status = self.fan_controller.get_status_name()
        prot_status = protection_status.value
        
        if self._last_fan_status != fan_status or self._last_prot_status != prot_status:
            hailo_info = ""
            if self.hailo_temps:
                hailo_str = ", ".join(f"{k}={v:.1f}C" for k, v in self.hailo_temps.items())
                hailo_info = f", Hailo=[{hailo_str}]"
            
            cloudlog.info(f"thermald: Fan={fan_status}, Protection={prot_status}, "
                         f"max={self.max_temp:.1f}C, fan={self.fan_speed}%{hailo_info}")
        
        self._last_fan_status = fan_status
        self._last_prot_status = prot_status
    
    def run(self):
        rk = Ratekeeper(1)
        cloudlog.info("thermald: Running")
        
        while True:
            self.update()
            rk.keep_time()
    
    def stop(self):
        self._set_fan_speed(0)
        self.protection.cleanup()
        cloudlog.info("thermald: Stopped")


def main() -> int:
    try:
        daemon = ThermalD()
        daemon.run()
        return 0
    except KeyboardInterrupt:
        cloudlog.info("ThermalD stopped")
        return 0
    except Exception as e:
        cloudlog.exception(f"ThermalD fatal error: {e}")
        return 1


if __name__ == "__main__":
    exit(main())

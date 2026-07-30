#!/usr/bin/env python3
"""
stated - Vehicle State Daemon for ExoPilot (EOP)

Manages vehicle operation states:
- OFF: System off, minimal power
- ACCESSORY: Accessory power (radio, etc.)
- ON: Ignition on, engine not running
- RUNNING: Engine running, normal operation
- PARKING: Parking mode (ignition off, monitoring active)

Hardware inputs:
- Ignition GPIO (from vehicle)
- CAN messages (vehicle state)
- Power state (battery voltage)

Publishes:
  - vehicleState: Current vehicle state
  - ignitionState: Ignition on/off
"""

from __future__ import annotations

import time
from enum import Enum
from dataclasses import dataclass

from cereal import messaging, log
from openpilot.common.realtime import Ratekeeper
from openpilot.common.swaglog import cloudlog
from openpilot.common.core_config import set_daemon_affinity
from openpilot.common.params import Params


class VehicleState(Enum):
    """Vehicle operation states."""
    OFF = "off"               # Everything off
    ACCESSORY = "accessory"   # ACC power only
    ON = "on"                 # Ignition on, engine off
    RUNNING = "running"       # Engine running
    PARKING = "parking"       # Parking mode (monitoring)


class SystemState(Enum):
    """System operational states (VisionPilot-style integration)."""
    STARTUP = "startup"           # Booting, initializing
    CALIBRATING = "calibrating"   # Camera calibration in progress
    READY = "ready"               # Calibrated, ready for engagement
    ENGAGED = "engaged"           # ADAS active
    FAULT = "fault"               # System fault/error


@dataclass
class StateConfig:
    """State detection configuration."""
    # Voltage thresholds (for battery-based detection)
    voltage_running: float = 13.5  # V - alternator charging
    voltage_on: float = 12.5       # V - ignition on
    voltage_accessory: float = 12.0  # V - accessory only
    voltage_off: float = 11.5      # V - off/sleep
    
    # Timing
    debounce_time: float = 1.0     # seconds
    parking_timeout: float = 30.0  # seconds to enter parking mode


class StateD:
    """Vehicle state daemon."""
    
    def __init__(self):
        set_daemon_affinity("stated")
        
        self.params = Params()
        self.config = StateConfig()
        
        # Messaging
        self.pm = messaging.PubMaster(['vehicleState', 'systemState'])
        self.sm = messaging.SubMaster(['pandaStates', 'carState', 'calibrationState'])
        
        # System state (VisionPilot-style)
        self.system_state = SystemState.STARTUP
        self.calibration_complete = False
        self.calibration_quality = 0
        
        # State
        self.state = VehicleState.OFF
        self.ignition_on = False
        self.engine_running = False
        self.parking_mode = False
        
        # Timing
        self.last_state_change = time.monotonic()
        self.ignition_off_time = None
        
        # GPIO ignition (EOP platforms)
        self.eop_ignition_gpio = False
        
        cloudlog.info("stated: Initialized")
    
    def _read_eop_ignition(self) -> bool:
        """Read ignition state from EOP GPIO."""
        # TODO: Read actual GPIO from BSP
        # For now, use param set by external ignition monitor
        return self.params.get_bool("EOPIgnitionOn")
    
    def _read_can_ignition(self) -> bool:
        """Read ignition from CAN (if Panda available)."""
        if not self.sm.updated['pandaStates']:
            return False
        
        for ps in self.sm['pandaStates']:
            if ps.pandaType != log.PandaState.PandaType.unknown:
                return ps.ignitionLine or ps.ignitionCan
        return False
    
    def _read_engine_running(self) -> bool:
        """Detect if engine is running (from CAN)."""
        if not self.sm.updated['carState']:
            return False
        
        cs = self.sm['carState']
        # Engine running if RPM > 0 or vehicle moving
        return cs.engineRPM > 0 or cs.vEgo > 0.1
    
    def _update_calibration_state(self):
        """Update calibration state from calibrationState message."""
        if not self.sm.updated['calibrationState']:
            return
        
        cs = self.sm['calibrationState']
        self.calibration_quality = cs.quality
        
        # Check if calibration is complete (converging=2 or calibrated=3)
        prev_complete = self.calibration_complete
        self.calibration_complete = cs.status in (2, 3)
        
        if self.calibration_complete != prev_complete:
            cloudlog.info(f"stated: Calibration {'complete' if self.calibration_complete else 'incomplete'} "
                         f"(quality={cs.quality:.2f}, cameras={len(cs.cameraCalibrations)})")
    
    def _update_system_state(self):
        """Update system state (VisionPilot-style)."""
        # State machine based on vehicle state and calibration
        prev_system_state = self.system_state
        
        if self.state == VehicleState.OFF:
            self.system_state = SystemState.STARTUP
        elif not self.calibration_complete:
            self.system_state = SystemState.CALIBRATING
        elif self.state in (VehicleState.ON, VehicleState.RUNNING):
            self.system_state = SystemState.READY
        else:
            self.system_state = SystemState.STARTUP
        
        if self.system_state != prev_system_state:
            cloudlog.info(f"stated: System state {prev_system_state.value} -> {self.system_state.value}")
    
    def _update_state(self):
        """Update vehicle state based on inputs."""
        # Read ignition from multiple sources
        can_ignition = self._read_can_ignition()
        eop_ignition = self._read_eop_ignition()
        
        # Combine ignition sources (OR logic)
        ignition = can_ignition or eop_ignition
        
        # Detect state changes
        if ignition != self.ignition_on:
            self.ignition_on = ignition
            if ignition:
                self.ignition_off_time = None
                self.parking_mode = False
                cloudlog.info("stated: Ignition ON")
            else:
                self.ignition_off_time = time.monotonic()
                cloudlog.info("stated: Ignition OFF")
        
        # Read engine state
        self.engine_running = self._read_engine_running()

        # Determine vehicle state
        new_state = self.state

        if self.ignition_on:
            # Ignition is on
            if self.engine_running:
                new_state = VehicleState.RUNNING
            else:
                new_state = VehicleState.ON
        else:
            # Ignition is off
            if self.ignition_off_time:
                time_since_off = time.monotonic() - self.ignition_off_time
                if time_since_off > self.config.parking_timeout:
                    new_state = VehicleState.PARKING
                    self.parking_mode = True
                else:
                    new_state = VehicleState.OFF
            else:
                new_state = VehicleState.OFF
        
        # Update state if changed
        if new_state != self.state:
            cloudlog.info(f"stated: State {self.state.value} -> {new_state.value}")
            self.state = new_state
            self.last_state_change = time.monotonic()
        
        # Update system state (VisionPilot-style)
        self._update_calibration_state()
        self._update_system_state()
    
    def _publish_state(self):
        """Publish vehicle state."""
        msg = messaging.new_message('vehicleState', valid=True)
        
        vs = msg.vehicleState
        vs.state = self.state.value
        vs.ignition = self.ignition_on
        vs.engineRunning = self.engine_running
        vs.parkingMode = self.parking_mode
        vs.standstill = not self.engine_running
        
        self.pm.send('vehicleState', msg)
        
        # Publish system state (VisionPilot-style)
        msg2 = messaging.new_message('systemState', valid=True)
        ss = msg2.systemState
        ss.state = self.system_state.value
        ss.calibrationQuality = self.calibration_quality
        ss.calibrationComplete = self.calibration_complete
        self.pm.send('systemState', msg2)
    
    def update(self):
        """Main update loop."""
        self.sm.update(0)
        self._update_state()
        self._publish_state()
    
    def run(self):
        """Main daemon loop."""
        rk = Ratekeeper(10)  # 10Hz
        cloudlog.info("stated: Running")
        
        while True:
            self.update()
            rk.keep_time()
    
    def stop(self):
        """Stop daemon."""
        cloudlog.info("stated: Stopped")


def main():
    daemon = StateD()
    try:
        daemon.run()
    except KeyboardInterrupt:
        daemon.stop()


if __name__ == "__main__":
    main()

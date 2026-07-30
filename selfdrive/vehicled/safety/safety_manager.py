#!/usr/bin/env python3
"""
Safety Manager for SocketD
Integrates VehicleSafetyLayer into the CAN bridge
"""

from __future__ import annotations
import threading
import time
from collections.abc import Callable

from openpilot.selfdrive.vehicled.safety.safety import VehicleSafetyLayer, SafetyLimits, SafetyViolation


class SafetyManager:
    """
    Manages safety checks for SocketD CAN bridge.
    
    This is the 1st layer safety check that runs in openpilot/visionpilot.
    TC275 provides the 2nd layer with tighter limits.
    """
    
    # Required RX message IDs for safety_tick() validation
    REQUIRED_RX_MESSAGES = [0x370, 0x257]  # EPS status, Vehicle speed
    
    def __init__(self, limits: SafetyLimits | None = None, use_params: bool = True):
        # Load limits from params if not provided
        if limits is None and use_params:
            try:
                limits = SafetyLimits.from_params()
            except Exception as e:
                print(f"Warning: Could not load safety limits from params: {e}")
                limits = None
        
        # Check if file logging is enabled
        enable_file_logging = True
        self._log_file_path = "/data/safety_violations.log"
        if use_params:
            try:
                from openpilot.common.params import Params
                params = Params()
                enable_file_logging = params.get_bool("EOPSafetyEventLogEnabled")
                log_path = params.get("EOPSafetyEventLogPath")
                if log_path:
                    self._log_file_path = log_path.decode() if isinstance(log_path, bytes) else log_path
            except Exception:
                pass
        
        self.safety = VehicleSafetyLayer(limits, enable_file_logging=enable_file_logging)
        self._enabled = True
        self._lock = threading.Lock()
        
        # Statistics
        self._tx_checked = 0
        self._tx_blocked = 0
        self._rx_processed = 0
        
        # Violation handler
        self._on_violation: Callable[[SafetyViolation], None] | None = None
        self.safety.register_violation_callback(self._handle_violation)
        
        # safety_tick() state
        self._last_tick_time = 0.0
        self._tick_interval = 1.0  # Run at 1Hz
    
    def set_violation_handler(self, handler: Callable[[SafetyViolation], None]):
        """Set callback for safety violations"""
        self._on_violation = handler
    
    def _handle_violation(self, violation: SafetyViolation):
        """Internal violation handler"""
        if self._on_violation:
            try:
                self._on_violation(violation)
            except Exception as e:
                print(f"Safety violation handler error: {e}")
    
    def enable(self):
        """Enable safety checks"""
        with self._lock:
            self._enabled = True
    
    def disable(self):
        """Disable safety checks (for debugging only!)"""
        with self._lock:
            self._enabled = False
    
    def is_enabled(self) -> bool:
        """Check if safety is enabled"""
        with self._lock:
            return self._enabled
    
    def update_heartbeat(self, engaged: bool = True):
        """Update safety heartbeat"""
        self.safety.update_heartbeat(engaged)
    
    def process_rx_message(self, address: int, data: bytes, bus: int):
        """Process incoming CAN message for safety state"""
        if not self._enabled:
            return
        
        self.safety.process_rx_message(address, data, bus)
        self._rx_processed += 1
    
    def check_tx_message(self, address: int, data: bytes, bus: int) -> bool:
        """
        Check if TX message is safe.
        Returns True if message can be sent.
        """
        if not self._enabled:
            return True
        
        self._tx_checked += 1
        allowed, violation = self.safety.check_tx_message(address, data, bus)
        
        if not allowed:
            self._tx_blocked += 1
            print(f"🔒 SAFETY BLOCKED: {violation.violation_type} - {violation}")
        
        return allowed
    
    def get_status(self) -> dict:
        """Get safety manager status"""
        return {
            'enabled': self._enabled,
            'controls_allowed': self.safety.state.controls_allowed,
            'tx_checked': self._tx_checked,
            'tx_blocked': self._tx_blocked,
            'rx_processed': self._rx_processed,
            'block_rate': self._tx_blocked / max(1, self._tx_checked),
            'stats': self.safety.get_stats(),
        }
    
    def get_state(self) -> dict:
        """Get current safety state"""
        state = self.safety.state
        return {
            'controls_allowed': state.controls_allowed,
            'vehicle_speed': state.vehicle_speed,
            'steering_angle': state.steering_angle / 10.0,  # Convert to degrees
            'driver_torque': state.driver_torque,
            'hands_on_level': state.hands_on_level,
            'brake_pressed': state.brake_pressed,
            'gas_pressed': state.gas_pressed,
            'autopark_active': state.autopark_active,
            'stock_lkas_active': state.stock_lkas_active,
            'stock_aeb_active': state.stock_aeb_active,
            'rx_checks_invalid': state.rx_checks_invalid,
            'rx_check_errors': state.rx_check_errors,
        }
    
    def safety_tick(self) -> bool:
        """
        Periodic safety check that validates message freshness.
        Runs at 1Hz to verify required RX messages are being received.
        
        Returns:
            True if safety checks pass, False if messages are lagging
        """
        now = time.monotonic()
        
        # Rate limit to 1Hz
        if now - self._last_tick_time < self._tick_interval:
            return True
        
        self._last_tick_time = now
        
        if not self._enabled:
            return True
        
        # Check required messages are recent
        timeout = self.safety.limits.MESSAGE_TIMEOUT_MS / 1000.0
        
        for addr in self.REQUIRED_RX_MESSAGES:
            last_time = self.safety.state.message_timestamps.get(addr, 0)
            if now - last_time > timeout:
                # Message is stale
                self.safety.state.rx_checks_invalid = True
                self.safety.state.rx_check_errors += 1
                
                if self.safety.state.controls_allowed:
                    self.safety._log_event('safety_tick_timeout', {
                        'addr': hex(addr),
                        'elapsed': now - last_time,
                        'timeout': timeout,
                    })
                    self.safety.state.controls_allowed = False
                
                return False
        
        # All required messages are fresh
        self.safety.state.rx_checks_invalid = False
        return True

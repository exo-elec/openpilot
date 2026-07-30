#!/usr/bin/env python3
"""
Tesla Safety Implementation - 1st Layer
Compatible with both openpilot and visionpilot

Based on opendbc/safety/modes/tesla.h but with looser limits than TC275.
TC275 will have tighter limits as the 2nd layer safety.

This is the CANONICAL layer-1 safety module.  The former duplicate at
selfdrive/vehicled/safety/safety.py (which added the TC275 cross-core 0x712
verdict checks) was merged here; that file is now a shim re-exporting this
module.  Do not fork another copy — extend this one.
"""

from __future__ import annotations
import time
from dataclasses import dataclass, field
from collections.abc import Callable
from collections import deque


@dataclass
class SafetyLimits:
    """
    Safety limits for Tesla protocol - LAYER 1 (TIGHTER).

    Layer 1 (this): TIGHTER limits for normal operation (80% of Panda)
    Layer 2 (TC275): LOOSER limits (100% of Panda) - Safety net

    These limits are 80% of the original Panda limits from openpilot 0.10.0.
    TC275 will have the original Panda limits as the safety net.

    Reference (Panda/openpilot 0.10.0 original):
    - MAX_STEERING_ANGLE: 3600 (360°)
    - MAX_ACCEL: 425 (2.0 m/s²)
    - MIN_ACCEL: 288 (-3.48 m/s²)

    Note: These defaults are used when params are not available (e.g., testing).
    In production, limits are loaded from params in SafetyManager.
    """
    # Steering Limits - ~80% of Panda (TIGHTER, rounded)
    MAX_STEERING_ANGLE: int = 2700  # 270 degrees (0.1 deg units) - ~75% of 3600
    MAX_STEERING_RATE: int = 200    # 20 deg/s (0.1 deg/s units) - 80% of 250
    MAX_ANGLE_ERROR: int = 240      # 24 degrees max error - 80% of 30°

    # Longitudinal Limits - ~80% of Panda (TIGHTER)
    # Tesla DAS_control accel is offset-encoded: INACTIVE_ACCEL (375) = 0 m/s²,
    # values above = accelerate, below = brake.  The 80% factor must be applied
    # to the offset from 375, NOT to the raw Panda values (naive 0.8*425=340 is
    # below INACTIVE and would make every command a "both negative" block).
    #   MAX: 375 + 0.8*(425-375) = 415  (~+1.6 m/s²)
    #   MIN: 375 - 0.8*(375-288) = 305  (~-2.8 m/s²)
    MAX_ACCEL: int = 415           # ~+1.6 m/s² (Tesla units, offset from 375)
    MIN_ACCEL: int = 305           # ~-2.8 m/s² (Tesla units, offset from 375)
    INACTIVE_ACCEL: int = 375      # 0 m/s²

    # Rate Limits for longitudinal - 80% of Panda (TIGHTER)
    MAX_ACCEL_RATE: int = 16       # Max change per frame - 80% of 20

    # Timing - 80% of Panda (TIGHTER/faster response)
    HEARTBEAT_TIMEOUT_MS: float = 200.0  # 200ms timeout - 80% of 250ms
    MESSAGE_TIMEOUT_MS: float = 1200.0   # 1.2s message timeout - 80% of 1.5s

    # Driver override - 80% of Panda (TIGHTER)
    DRIVER_TORQUE_THRESHOLD: int = 200   # 2.0 Nm (0.01 Nm units) - 80% of 2.5

    # Speed
    MIN_SPEED_FOR_CONTROL: float = 0.0   # Allow at standstill

    @classmethod
    def from_params(cls, params=None) -> SafetyLimits:
        """
        Load safety limits from params.
        Falls back to defaults if params not available.
        """
        try:
            from openpilot.common.params import Params
            params = params or Params()

            return cls(
                MAX_STEERING_ANGLE=int(params.get("EOPSafetyMaxSteeringAngle") or 2700),
                MAX_STEERING_RATE=int(params.get("EOPSafetyMaxSteeringRate") or 200),
                MAX_ANGLE_ERROR=int(params.get("EOPSafetyMaxAngleError") or 240),
                MAX_ACCEL=int(params.get("EOPSafetyMaxAccel") or 415),
                MIN_ACCEL=int(params.get("EOPSafetyMinAccel") or 305),
                HEARTBEAT_TIMEOUT_MS=int(params.get("EOPSafetyHeartbeatTimeout") or 200),
                MESSAGE_TIMEOUT_MS=int(params.get("EOPSafetyMessageTimeout") or 1200),
                DRIVER_TORQUE_THRESHOLD=int(params.get("EOPSafetyDriverTorqueThreshold") or 200),
            )
        except Exception as e:
            # If params not available (e.g., testing), use defaults
            print(f"Warning: Could not load safety limits from params: {e}")
            return cls()


@dataclass
class SafetyState:
    """Current safety state tracking"""
    controls_allowed: bool = False
    last_heartbeat_time: float = field(default_factory=time.monotonic)
    last_steering_angle: int = 0
    last_accel_max: int = 375  # INACTIVE_ACCEL
    last_accel_min: int = 375  # INACTIVE_ACCEL

    # Message counters for validation
    message_counters: dict[int, int] = field(default_factory=dict)
    message_timestamps: dict[int, float] = field(default_factory=dict)
    message_valid: dict[int, bool] = field(default_factory=dict)

    # RX check status
    rx_checks_invalid: bool = False
    rx_check_errors: int = 0

    # Driver state
    driver_torque: int = 0
    hands_on_level: int = 0

    # Vehicle state
    vehicle_speed: float = 0.0
    steering_angle: int = 0
    brake_pressed: bool = False
    gas_pressed: bool = False

    # Stock system states
    stock_lkas_active: bool = False
    stock_aeb_active: bool = False
    autopark_active: bool = False

    # TC275 CPU1/CPU2 Kalman safety verdicts from 0x712 (CPU Safety Status)
    # Default True so openpilot can send before first 0x712 arrives (grace period)
    tc275_lateral_ok: bool = True
    tc275_longitudinal_ok: bool = True
    tc275_cpu1_alive: bool = True
    tc275_cpu2_alive: bool = True
    tc275_lat_violation: int = 0
    tc275_long_violation: int = 0
    tc275_last_rx: float = field(default_factory=lambda: 0.0)  # monotonic time of last 0x712


class SafetyViolation(Exception):
    """Raised when a safety violation is detected"""
    def __init__(self, message: str, violation_type: str, details: dict = None):
        super().__init__(message)
        self.violation_type = violation_type
        self.details = details or {}
        self.timestamp = time.monotonic()


class TeslaSafety:
    """
    Tesla Safety Layer - 1st Layer

    Validates CAN messages before they are sent to the vehicle.
    This is the software safety layer that catches bugs and enforces
    smooth control limits.

    TC275 (2nd layer) will have tighter limits and hardware enforcement.
    """

    # Tesla CAN IDs
    CAN_ID_STEERING_CONTROL = 0x488  # DAS_steeringControl
    CAN_ID_LONGITUDINAL_CONTROL = 0x2B9  # DAS_control
    CAN_ID_EAC_MONITOR = 0x27D  # APS_eacMonitor

    # TC275 CPU Safety Status frame (comma.c COMMA_EncodeMessage712, sent on CAN0)
    # Byte 0: bit7=lateralOk, bit6=longOk, bit5=controlsAllowed, bit4=epsCruise,
    #         bit3=cruiseEngaged, bit2=counterTrusted, bit1=cpu1Alive, bit0=cpu2Alive
    # Byte 1: latViolationReason   (CROSSCORE_LAT_VIOLATION_* codes)
    # Byte 2: longViolationReason  (CROSSCORE_LONG_VIOLATION_* codes)
    # Byte 3: (gatewayState<<6) | (commaAlive<<5)
    CAN_ID_TC275_CPU_SAFETY = 0x712

    # Timeout: if no 0x712 for >500 ms, treat as TC275 stall (fail-safe: allow)
    TC275_SAFETY_TIMEOUT_S = 0.5

    _TC275_LAT_VIOLATIONS: dict[int, str] = {
        1: "angle_limit", 2: "rate_limit", 3: "driver_override",
        4: "no_cruise",   5: "no_controls", 6: "counter_sync",
        7: "imu_diverge", 8: "predictive",
    }
    _TC275_LONG_VIOLATIONS: dict[int, str] = {
        10: "accel_limit", 11: "jerk_limit",   12: "no_cruise",
        13: "no_controls", 14: "counter_sync", 15: "imu_diverge", 16: "predictive",
    }

    # RX IDs for state monitoring
    CAN_ID_EPS_STATUS = 0x370  # EPAS3S_sysStatus
    CAN_ID_SPEED = 0x257  # DI_speed
    CAN_ID_GAS = 0x118  # DI_systemStatus
    CAN_ID_BRAKE = 0x39D  # IBST_status
    CAN_ID_CRUISE = 0x286  # DI_state
    CAN_ID_BRAKE_ESP = 0x145  # ESP_status (redundant brake source)
    CAN_ID_UI_WARNING = 0x311  # UI_warning
    CAN_ID_DAS_SETTINGS = 0x293  # DAS_settings

    # RX message configuration for validation (checksum byte, counter byte, max_counter)
    RX_MESSAGE_CONFIG = {
        0x370: {'checksum_byte': 7, 'counter_byte': 6, 'max_counter': 15, 'ignore_checksum': False},  # EPAS3S_sysStatus
        0x257: {'checksum_byte': 0, 'counter_byte': 1, 'max_counter': 15, 'ignore_checksum': False},  # DI_speed
        0x118: {'checksum_byte': 0, 'counter_byte': 1, 'max_counter': 15, 'ignore_checksum': False},  # DI_systemStatus
        0x39D: {'checksum_byte': 0, 'counter_byte': 1, 'max_counter': 15, 'ignore_checksum': False},  # IBST_status
        0x286: {'checksum_byte': 0, 'counter_byte': 1, 'max_counter': 15, 'ignore_checksum': False},  # DI_state
        0x145: {'checksum_byte': 0, 'counter_byte': 1, 'max_counter': 15, 'ignore_checksum': False},  # ESP_status
        0x311: {'checksum_byte': 0, 'counter_byte': 1, 'max_counter': 15, 'ignore_checksum': False},  # UI_warning
        0x293: {'checksum_byte': 7, 'counter_byte': 1, 'max_counter': 15, 'ignore_checksum': False},  # DAS_settings
        0x2B9: {'checksum_byte': 7, 'counter_byte': 6, 'max_counter': 7, 'ignore_checksum': True},   # DAS_control (TX echo)
        0x488: {'checksum_byte': 3, 'counter_byte': 2, 'max_counter': 15, 'ignore_checksum': True},   # DAS_steeringControl (TX echo)
    }

    def __init__(self, limits: SafetyLimits | None = None, enable_file_logging: bool = True):
        self.limits = limits or SafetyLimits()
        self.state = SafetyState()
        self._violation_callbacks: list[Callable[[SafetyViolation], None]] = []

        # Safety event log (last 100 events in memory)
        self._event_log: deque[dict] = deque(maxlen=100)

        # File logging
        self._file_logging_enabled = enable_file_logging
        self._log_file_path = "/data/safety_violations.log"
        self._log_max_events = 1000
        if self._file_logging_enabled:
            self._init_log_file()

        # Statistics
        self._stats = {
            'messages_checked': 0,
            'violations_blocked': 0,
            'heartbeats_received': 0,
            'heartbeats_missed': 0,
        }

    def _init_log_file(self):
        """Initialize log file if it doesn't exist"""
        try:
            import os
            if not os.path.exists(self._log_file_path):
                with open(self._log_file_path, 'w') as f:
                    f.write("# EOP Safety Violation Log\n")
                    f.write("# Format: timestamp, event_type, details_json\n")
        except Exception as e:
            print(f"Warning: Could not initialize safety log file: {e}")
            self._file_logging_enabled = False

    def _log_to_file(self, event: dict):
        """Write event to persistent log file"""
        if not self._file_logging_enabled:
            return

        try:
            import json
            from datetime import datetime

            # Format: ISO timestamp, event_type, details_json
            timestamp = datetime.fromtimestamp(event['timestamp']).isoformat()
            event_type = event['type']
            details = json.dumps(event.get('details', {}), default=str)
            controls_allowed = event.get('controls_allowed', False)

            log_line = f"{timestamp},{event_type},{controls_allowed},{details}\n"

            # Append to file
            with open(self._log_file_path, 'a') as f:
                f.write(log_line)

            # Rotate log if too large
            self._rotate_log_if_needed()

        except Exception as e:
            print(f"Warning: Could not write to safety log file: {e}")

    def _rotate_log_if_needed(self):
        """Rotate log file if it exceeds max events"""
        try:

            # Count lines in file
            with open(self._log_file_path) as f:
                lines = f.readlines()

            # Keep header + max_events
            if len(lines) > self._log_max_events + 2:  # +2 for header lines
                # Keep header and last max_events
                new_lines = lines[:2] + lines[-(self._log_max_events):]
                with open(self._log_file_path, 'w') as f:
                    f.writelines(new_lines)

        except Exception as e:
            print(f"Warning: Could not rotate safety log file: {e}")

    def register_violation_callback(self, callback: Callable[[SafetyViolation], None]):
        """Register a callback for safety violations"""
        self._violation_callbacks.append(callback)

    def _log_event(self, event_type: str, details: dict):
        """Log a safety event"""
        event = {
            'timestamp': time.monotonic(),
            'type': event_type,
            'details': details,
            'controls_allowed': self.state.controls_allowed,
        }
        self._event_log.append(event)
        self._log_to_file(event)

    def _notify_violation(self, violation: SafetyViolation):
        """Notify all registered callbacks of a violation"""
        self._stats['violations_blocked'] += 1
        self._log_event('violation', {
            'type': violation.violation_type,
            'message': str(violation),
            'details': violation.details,
        })
        for callback in self._violation_callbacks:
            try:
                callback(violation)
            except Exception:
                pass

    def check_heartbeat(self) -> bool:
        """
        Check if heartbeat is still valid.
        Returns True if controls should be allowed.
        """
        elapsed_ms = (time.monotonic() - self.state.last_heartbeat_time) * 1000
        if elapsed_ms > self.limits.HEARTBEAT_TIMEOUT_MS:
            if self.state.controls_allowed:
                self._log_event('heartbeat_lost', {'elapsed_ms': elapsed_ms})
                self._stats['heartbeats_missed'] += 1
            self.state.controls_allowed = False
            return False
        return self.state.controls_allowed

    def update_heartbeat(self, engaged: bool = True):
        """Update heartbeat timestamp"""
        self.state.last_heartbeat_time = time.monotonic()
        self._stats['heartbeats_received'] += 1

        # Controls allowed on rising edge or if already engaged
        if engaged and not self.state.controls_allowed:
            self._log_event('controls_allowed', {})
        self.state.controls_allowed = engaged

    def _process_tc275_cpu_safety(self, data: bytes) -> None:
        """Parse TC275 0x712 CPU Safety Status frame (COMMA_EncodeMessage712).

        Byte 0 flags: bit7=lateralOk, bit6=longOk, bit5=controlsAllowed,
                      bit4=epsCruise, bit3=cruiseEngaged, bit2=counterTrusted,
                      bit1=cpu1Alive, bit0=cpu2Alive
        Byte 1: latViolationReason  (CROSSCORE_LAT_VIOLATION_* 1-8)
        Byte 2: longViolationReason (CROSSCORE_LONG_VIOLATION_* 10-16)
        """
        if len(data) < 3:
            return

        flags = int(data[0])
        lat_ok = bool(flags & 0x80)
        long_ok = bool(flags & 0x40)
        cpu1_alive = bool(flags & 0x02)
        cpu2_alive = bool(flags & 0x01)
        lat_violation = int(data[1])
        long_violation = int(data[2])

        prev_lat_ok = self.state.tc275_lateral_ok
        prev_long_ok = self.state.tc275_longitudinal_ok

        self.state.tc275_lateral_ok = lat_ok
        self.state.tc275_longitudinal_ok = long_ok
        self.state.tc275_cpu1_alive = cpu1_alive
        self.state.tc275_cpu2_alive = cpu2_alive
        self.state.tc275_lat_violation = lat_violation
        self.state.tc275_long_violation = long_violation
        self.state.tc275_last_rx = time.monotonic()

        if not lat_ok and prev_lat_ok:
            reason = self._TC275_LAT_VIOLATIONS.get(lat_violation, f"code_{lat_violation}")
            self._log_event('tc275_lateral_blocked', {'reason': reason, 'code': lat_violation})

        if not long_ok and prev_long_ok:
            reason = self._TC275_LONG_VIOLATIONS.get(long_violation, f"code_{long_violation}")
            self._log_event('tc275_longitudinal_blocked', {'reason': reason, 'code': long_violation})

    def _tc275_safety_timed_out(self) -> bool:
        """True if no 0x712 received recently — fail-open (allow) to avoid false positives."""
        if self.state.tc275_last_rx == 0.0:
            return True  # Never received — no TC275 connected, don't block
        return (time.monotonic() - self.state.tc275_last_rx) > self.TC275_SAFETY_TIMEOUT_S

    def process_rx_message(self, address: int, data: bytes, bus: int):
        """
        Process incoming CAN message to update safety state.
        This tracks vehicle state for safety checks.
        Also validates checksum and counter for safety-critical messages.
        """
        # TC275 CPU safety status (CAN0 only, sent by comma.c COMMA_EncodeMessage712)
        if address == self.CAN_ID_TC275_CPU_SAFETY and bus == 0:
            self._process_tc275_cpu_safety(data)
            return

        if bus != 0:
            return

        # Validate message checksum and counter
        if not self._validate_rx_message(address, data):
            self.state.rx_check_errors += 1
            if self.state.rx_check_errors > 10:
                self.state.rx_checks_invalid = True
                if self.state.controls_allowed:
                    self._log_event('rx_checks_invalid', {'errors': self.state.rx_check_errors})
                    self.state.controls_allowed = False
            return

        self.state.message_valid[address] = True
        self.state.message_timestamps[address] = time.monotonic()

        try:
            if address == self.CAN_ID_EPS_STATUS:
                self._process_eps_status(data)
            elif address == self.CAN_ID_SPEED:
                self._process_speed(data)
            elif address == self.CAN_ID_GAS:
                self._process_gas(data)
            elif address == self.CAN_ID_BRAKE:
                self._process_brake(data)
            elif address == self.CAN_ID_CRUISE:
                self._process_cruise(data)
        except Exception as e:
            self._log_event('rx_processing_error', {'error': str(e), 'addr': hex(address)})

    def _validate_rx_message(self, address: int, data: bytes) -> bool:
        """
        Validate RX message checksum and counter.
        Returns True if message is valid.
        """
        config = self.RX_MESSAGE_CONFIG.get(address)
        if not config:
            # Unknown message, allow it
            return True

        if len(data) < 8:
            # Message too short for checksum/counter
            return True

        # Check checksum if not ignored
        if not config.get('ignore_checksum', False):
            if not self._validate_checksum(address, data, config['checksum_byte']):
                return False

        # Check counter
        if not self._validate_counter(address, data, config['counter_byte'], config['max_counter']):
            return False

        return True

    def _validate_checksum(self, address: int, data: bytes, checksum_byte: int) -> bool:
        """
        Validate Tesla checksum.
        Tesla checksum: sum of (address low byte + address high byte + all data bytes except checksum)
        """
        if checksum_byte >= len(data):
            return True

        expected_checksum = (address & 0xFF) + ((address >> 8) & 0xFF)
        for i, byte in enumerate(data):
            if i != checksum_byte:
                expected_checksum += byte
        expected_checksum &= 0xFF

        actual_checksum = data[checksum_byte]
        return expected_checksum == actual_checksum

    def _validate_counter(self, address: int, data: bytes, counter_byte: int, max_counter: int) -> bool:
        """
        Validate message counter.
        Counter should increment by 1 (with wraparound at max_counter).
        """
        if counter_byte >= len(data):
            return True

        current_counter = data[counter_byte] & 0x0F  # Counter is usually in low nibble
        last_counter = self.state.message_counters.get(address, -1)

        # Store current counter
        self.state.message_counters[address] = current_counter

        if last_counter < 0:
            # First message, accept it
            return True

        # Check counter increment (with wraparound)
        expected_counter = (last_counter + 1) % (max_counter + 1)
        if current_counter != expected_counter:
            # Allow some tolerance for dropped messages (up to 2 missed)
            missed = (current_counter - last_counter) % (max_counter + 1)
            if missed > 3:
                # Too many missed messages or counter error
                self._log_event('counter_error', {
                    'addr': hex(address),
                    'expected': expected_counter,
                    'actual': current_counter,
                    'last': last_counter,
                })
                return False

        return True

    def _process_eps_status(self, data: bytes):
        """Process EPAS3S_sysStatus (0x370)"""
        if len(data) < 7:
            return

        # Steering angle: (0.1 * val) - 819.2 deg
        raw_angle = ((data[4] & 0x3F) << 8) | data[5]
        self.state.steering_angle = raw_angle - 8192

        # Hands on level (bits 6-7 of byte 4)
        self.state.hands_on_level = data[4] >> 6

        # EAC status (bits 5-7 of byte 6)
        eac_status = data[6] >> 5
        eac_error = (data[2] >> 4) & 0x0F

        # Disengage on high hands on level or angle rate fault
        if (self.state.hands_on_level >= 3 or
            (eac_status == 0 and eac_error == 9)):
            if self.state.controls_allowed:
                self._log_event('driver_override', {
                    'hands_on': self.state.hands_on_level,
                    'eac_status': eac_status,
                    'eac_error': eac_error,
                })
            self.state.controls_allowed = False

    def _process_speed(self, data: bytes):
        """Process DI_speed (0x257)"""
        if len(data) < 3:
            return
        raw_speed = (data[2] << 4) | (data[1] >> 4)
        # Speed: ((val * 0.08) - 40) kph
        self.state.vehicle_speed = max(0.0, ((raw_speed * 0.08) - 40.0) / 3.6)

    def _process_gas(self, data: bytes):
        """Process DI_systemStatus (0x118)"""
        if len(data) < 5:
            return
        self.state.gas_pressed = data[4] != 0
        if self.state.gas_pressed and self.state.controls_allowed:
            self._log_event('gas_pressed', {})
            self.state.controls_allowed = False

    def _process_brake(self, data: bytes):
        """Process IBST_status (0x39D)"""
        if len(data) < 3:
            return
        brake_status = data[2] & 0x03
        self.state.brake_pressed = brake_status == 2
        if self.state.brake_pressed and self.state.controls_allowed:
            self._log_event('brake_pressed', {})
            self.state.controls_allowed = False

    def _process_cruise(self, data: bytes):
        """Process DI_state (0x286)"""
        if len(data) < 4:
            return

        # Autopark state (bits 1-4 of byte 3)
        autopark_state = (data[3] >> 1) & 0x0F
        autopark_active = autopark_state in [3, 4, 9]  # ACTIVE, COMPLETE, SELFPARK_STARTED

        if autopark_active != self.state.autopark_active:
            self._log_event('autopark_change', {'active': autopark_active, 'state': autopark_state})
        self.state.autopark_active = autopark_active

        # Cruise state (bits 4-6 of byte 1)
        (data[1] >> 4) & 0x07

        # Don't allow controls if autopark is active
        if self.state.autopark_active:
            self.state.controls_allowed = False

    def check_tx_message(self, address: int, data: bytes, bus: int) -> tuple[bool, SafetyViolation | None]:
        """
        Check if a TX message is safe to send.

        Returns:
            (allowed, violation) - allowed is True if message can be sent,
            violation contains details if blocked
        """
        self._stats['messages_checked'] += 1

        # Always check heartbeat first
        if not self.check_heartbeat():
            return False, SafetyViolation(
                "Controls not allowed - heartbeat timeout or disengaged",
                "controls_not_allowed",
                {'addr': hex(address)}
            )

        try:
            if address == self.CAN_ID_STEERING_CONTROL:
                # TC275 CPU1 lateral verdict — only enforce while 0x712 is fresh
                if not self._tc275_safety_timed_out() and not self.state.tc275_lateral_ok:
                    reason = self._TC275_LAT_VIOLATIONS.get(
                        self.state.tc275_lat_violation, f"code_{self.state.tc275_lat_violation}"
                    )
                    raise SafetyViolation(
                        f"Steering blocked by TC275 CPU1 lateral safety ({reason})",
                        "tc275_lateral_blocked",
                        {'reason': reason, 'code': self.state.tc275_lat_violation},
                    )
                return self._check_steering_control(data)
            elif address == self.CAN_ID_LONGITUDINAL_CONTROL:
                # TC275 CPU2 longitudinal verdict — only enforce while 0x712 is fresh
                if not self._tc275_safety_timed_out() and not self.state.tc275_longitudinal_ok:
                    reason = self._TC275_LONG_VIOLATIONS.get(
                        self.state.tc275_long_violation, f"code_{self.state.tc275_long_violation}"
                    )
                    raise SafetyViolation(
                        f"Longitudinal blocked by TC275 CPU2 safety ({reason})",
                        "tc275_longitudinal_blocked",
                        {'reason': reason, 'code': self.state.tc275_long_violation},
                    )
                return self._check_longitudinal_control(data)
            elif address == self.CAN_ID_EAC_MONITOR:
                return self._check_eac_monitor(data)
            else:
                # Unknown message, allow (may be diagnostic)
                return True, None
        except SafetyViolation as v:
            self._notify_violation(v)
            return False, v

    def _check_steering_control(self, data: bytes) -> tuple[bool, SafetyViolation | None]:
        """Check DAS_steeringControl (0x488)"""
        if len(data) < 4:
            raise SafetyViolation(
                "Steering control message too short",
                "invalid_message_length",
                {'len': len(data)}
            )

        # Check autopark - never send when autopark is active
        if self.state.autopark_active:
            raise SafetyViolation(
                "Steering blocked - Autopark/Summon active",
                "autopark_active",
                {}
            )

        # Check stock LKAS conflict
        if self.state.stock_lkas_active:
            raise SafetyViolation(
                "Steering blocked - Stock LKAS active",
                "stock_lkas_conflict",
                {}
            )

        # Parse steering angle
        raw_angle = ((data[0] & 0x7F) << 8) | data[1]
        desired_angle = raw_angle - 16384  # 0.1 deg units

        # Check control type
        control_type = data[2] >> 6
        if control_type not in [0, 1]:  # NONE or ANGLE_CONTROL
            raise SafetyViolation(
                f"Invalid steering control type: {control_type}",
                "invalid_control_type",
                {'control_type': control_type}
            )

        steer_enabled = control_type == 1

        if steer_enabled:
            # Check angle limits
            if abs(desired_angle) > self.limits.MAX_STEERING_ANGLE:
                raise SafetyViolation(
                    f"Steering angle {desired_angle/10}° exceeds limit {self.limits.MAX_STEERING_ANGLE/10}°",
                    "steering_angle_limit",
                    {
                        'desired': desired_angle,
                        'limit': self.limits.MAX_STEERING_ANGLE,
                        'measured': self.state.steering_angle,
                    }
                )

            # Check rate limit
            angle_delta = desired_angle - self.state.last_steering_angle
            if abs(angle_delta) > self.limits.MAX_STEERING_RATE:
                raise SafetyViolation(
                    f"Steering rate {abs(angle_delta)/10}°/s exceeds limit {self.limits.MAX_STEERING_RATE/10}°/s",
                    "steering_rate_limit",
                    {
                        'delta': angle_delta,
                        'rate_limit': self.limits.MAX_STEERING_RATE,
                    }
                )

            # Check angle error vs measured
            angle_error = abs(desired_angle - self.state.steering_angle)
            if angle_error > self.limits.MAX_ANGLE_ERROR:
                raise SafetyViolation(
                    f"Steering angle error {angle_error/10}° exceeds limit {self.limits.MAX_ANGLE_ERROR/10}°",
                    "steering_angle_error",
                    {
                        'desired': desired_angle,
                        'measured': self.state.steering_angle,
                        'error': angle_error,
                    }
                )

        # Update last angle
        self.state.last_steering_angle = desired_angle
        return True, None

    def _check_longitudinal_control(self, data: bytes) -> tuple[bool, SafetyViolation | None]:
        """Check DAS_control (0x2B9)"""
        if len(data) < 7:
            raise SafetyViolation(
                "Longitudinal control message too short",
                "invalid_message_length",
                {'len': len(data)}
            )

        # Check AEB event - never allow AEB commands from openpilot
        aeb_event = data[2] & 0x03
        if aeb_event != 0:
            raise SafetyViolation(
                f"AEB event {aeb_event} not allowed from openpilot",
                "aeb_blocked",
                {'aeb_event': aeb_event}
            )

        # Check stock AEB conflict
        if self.state.stock_aeb_active:
            raise SafetyViolation(
                "Longitudinal blocked - Stock AEB active",
                "stock_aeb_conflict",
                {}
            )

        # Parse acceleration limits
        raw_accel_max = ((data[6] & 0x1F) << 4) | (data[5] >> 4)
        raw_accel_min = ((data[5] & 0x0F) << 5) | (data[4] >> 3)
        data[1] >> 4

        # Check accel limits
        if raw_accel_max > self.limits.MAX_ACCEL:
            raise SafetyViolation(
                f"Max accel {raw_accel_max} exceeds limit {self.limits.MAX_ACCEL}",
                "max_accel_limit",
                {
                    'accel_max': raw_accel_max,
                    'limit': self.limits.MAX_ACCEL,
                }
            )

        if raw_accel_min < self.limits.MIN_ACCEL:
            raise SafetyViolation(
                f"Min accel {raw_accel_min} below limit {self.limits.MIN_ACCEL}",
                "min_accel_limit",
                {
                    'accel_min': raw_accel_min,
                    'limit': self.limits.MIN_ACCEL,
                }
            )

        # Check rate limits
        max_delta = abs(raw_accel_max - self.state.last_accel_max)
        abs(raw_accel_min - self.state.last_accel_min)

        if max_delta > self.limits.MAX_ACCEL_RATE:
            raise SafetyViolation(
                f"Accel max rate {max_delta} exceeds limit {self.limits.MAX_ACCEL_RATE}",
                "accel_rate_limit",
                {
                    'delta': max_delta,
                    'rate_limit': self.limits.MAX_ACCEL_RATE,
                }
            )

        # Note: there is deliberately no "both accel limits negative" check here
        # (unlike an earlier revision).  Braking commands have BOTH accel_max and
        # accel_min below INACTIVE_ACCEL — that check blocked 100% of braking.
        # opendbc's tesla.h has no equivalent; min/max bounds above are the guard.

        # Update last values
        self.state.last_accel_max = raw_accel_max
        self.state.last_accel_min = raw_accel_min

        return True, None

    def _check_eac_monitor(self, data: bytes) -> tuple[bool, SafetyViolation | None]:
        """Check APS_eacMonitor (0x27D)"""
        # EAC monitor is generally safe, just check length
        if len(data) < 3:
            raise SafetyViolation(
                "EAC monitor message too short",
                "invalid_message_length",
                {'len': len(data)}
            )
        return True, None

    def get_stats(self) -> dict:
        """Get safety statistics"""
        return self._stats.copy()

    def get_event_log(self, count: int = 10) -> list:
        """Get recent safety events"""
        return list(self._event_log)[-count:]

    def reset_stats(self):
        """Reset statistics"""
        self._stats = {
            'messages_checked': 0,
            'violations_blocked': 0,
            'heartbeats_received': 0,
            'heartbeats_missed': 0,
        }


# Backwards-compatible alias: the former selfdrive/vehicled/safety/safety.py
# copy (now a shim re-exporting this module) named the class VehicleSafetyLayer.
VehicleSafetyLayer = TeslaSafety

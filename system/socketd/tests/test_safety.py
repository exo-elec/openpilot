#!/usr/bin/env python3
"""
Tests for SocketD Safety Layer

Tests critical safety features:
- Checksum validation
- Counter validation
- safety_tick() monitoring
"""

import time
import pytest
from openpilot.system.socketd.safety.tesla_safety import TeslaSafety, SafetyLimits, SafetyViolation
from openpilot.system.socketd.safety.safety_manager import SafetyManager


class TestChecksumValidation:
    """Test RX message checksum validation"""
    
    def test_checksum_calculation(self):
        """Test Tesla checksum calculation"""
        safety = TeslaSafety()
        
        # Create a valid EPS message (0x370)
        # Checksum at byte 7, counter at byte 6
        addr = 0x370
        data = bytearray(8)
        data[4] = 0x20  # Steering angle high
        data[5] = 0x00  # Steering angle low
        data[6] = 0x05  # Counter = 5
        
        # Calculate expected checksum
        expected = (addr & 0xFF) + ((addr >> 8) & 0xFF)
        for i, byte in enumerate(data):
            if i != 7:  # Skip checksum byte
                expected += byte
        expected &= 0xFF
        data[7] = expected
        
        # Validate
        assert safety._validate_checksum(addr, bytes(data), 7)
    
    def test_checksum_invalid(self):
        """Test checksum detects corruption"""
        safety = TeslaSafety()
        
        addr = 0x370
        data = bytearray(8)
        data[4] = 0x20
        data[5] = 0x00
        data[6] = 0x05
        data[7] = 0xFF  # Wrong checksum
        
        assert not safety._validate_checksum(addr, bytes(data), 7)
    
    def test_unknown_message_allowed(self):
        """Unknown messages are allowed through"""
        safety = TeslaSafety()

        # Unknown address (not in RX_MESSAGE_CONFIG)
        assert safety._validate_rx_message(0x999, bytes(8))


class TestCounterValidation:
    """Test RX message counter validation"""
    
    def test_counter_increment(self):
        """Test counter increments correctly"""
        safety = TeslaSafety()
        
        addr = 0x370
        data = bytearray(8)
        
        # First message (counter = 5)
        data[6] = 0x05
        assert safety._validate_counter(addr, bytes(data), 6, 15)
        
        # Next message (counter = 6)
        data[6] = 0x06
        assert safety._validate_counter(addr, bytes(data), 6, 15)
    
    def test_counter_wraparound(self):
        """Test counter wraps at max"""
        safety = TeslaSafety()
        safety.state.message_counters[0x370] = 15
        
        addr = 0x370
        data = bytearray(8)
        data[6] = 0x00  # Should wrap to 0
        
        assert safety._validate_counter(addr, bytes(data), 6, 15)
    
    def test_counter_dropped_messages(self):
        """Test tolerance for dropped messages (up to 2 missed)"""
        safety = TeslaSafety()
        safety.state.message_counters[0x370] = 5

        addr = 0x370
        data = bytearray(8)
        data[6] = 0x08  # Jump from 5 to 8 (missed 6, 7 = 2 messages)

        # Should still be valid (within tolerance: delta <= 3)
        assert safety._validate_counter(addr, bytes(data), 6, 15)
    
    def test_counter_error(self):
        """Test counter error detection (too many dropped)"""
        safety = TeslaSafety()
        safety.state.message_counters[0x370] = 5
        
        addr = 0x370
        data = bytearray(8)
        data[6] = 0x0A  # Jump from 5 to 10 (missed 4 messages)
        
        # Should be invalid (exceeds tolerance)
        assert not safety._validate_counter(addr, bytes(data), 6, 15)


class TestSafetyTick:
    """Test safety_tick() message monitoring"""
    
    def test_safety_tick_valid(self):
        """Test safety_tick passes when messages are recent"""
        manager = SafetyManager()
        
        # Set recent timestamps
        now = time.monotonic()
        manager.safety.state.message_timestamps[0x370] = now
        manager.safety.state.message_timestamps[0x257] = now
        
        # Should pass
        assert manager.safety_tick()
        assert not manager.safety.state.rx_checks_invalid
    
    def test_safety_tick_lagging(self):
        """Test safety_tick detects lagging messages"""
        manager = SafetyManager()
        
        # Set old timestamps (2 seconds ago)
        manager.safety.state.message_timestamps[0x370] = time.monotonic() - 2.0
        manager.safety.state.controls_allowed = True
        
        # Should fail and disengage
        assert not manager.safety_tick()
        assert manager.safety.state.rx_checks_invalid
        assert not manager.safety.state.controls_allowed
    
    def test_safety_tick_rate_limit(self):
        """Test safety_tick only runs at 1Hz"""
        manager = SafetyManager()
        manager._last_tick_time = time.monotonic()
        
        # First call should return True (not run yet)
        assert manager.safety_tick()
        
        # Immediate second call should also return True (rate limited)
        assert manager.safety_tick()


class TestSteeringSafety:
    """Test steering control safety checks"""
    
    def test_steering_angle_limit(self):
        """Test steering angle limit enforcement"""
        safety = TeslaSafety()
        safety.state.controls_allowed = True

        def _msg(desired: int) -> bytes:
            angle = desired + 16384  # Tesla offset encoding (0.1 deg units)
            data = bytearray(4)
            data[0] = (angle >> 8) & 0x7F
            data[1] = angle & 0xFF
            data[2] = 0x40  # ANGLE_CONTROL
            return bytes(data)

        # Valid angle (200° = 2000): preset rate/error references at the
        # desired value so only the angle-limit check can trip.
        safety.state.last_steering_angle = 2000
        safety.state.steering_angle = 2000
        allowed, _ = safety._check_steering_control(_msg(2000))
        assert allowed

        # Invalid angle (300° = 3000 > 2700 limit): rate delta and angle
        # error are zero, so the angle limit is the check that fires.
        safety.state.last_steering_angle = 3000
        safety.state.steering_angle = 3000
        with pytest.raises(SafetyViolation) as exc_info:
            safety._check_steering_control(_msg(3000))
        assert exc_info.value.violation_type == "steering_angle_limit"

    def test_steering_rate_limit(self):
        """Test steering rate limit enforcement"""
        safety = TeslaSafety()
        safety.state.controls_allowed = True

        def _msg(desired: int) -> bytes:
            angle = desired + 16384
            data = bytearray(4)
            data[0] = (angle >> 8) & 0x7F
            data[1] = angle & 0xFF
            data[2] = 0x40  # ANGLE_CONTROL
            return bytes(data)

        # Valid rate: 10° step (100, limit is 200) with EPS feedback matched
        # so the angle-error check stays quiet.
        safety.state.last_steering_angle = 900
        safety.state.steering_angle = 1000
        allowed, _ = safety._check_steering_control(_msg(1000))
        assert allowed

        # Excessive rate: 40° step (400 > 200 limit), still inside the 2700
        # angle limit and with matched feedback, so the rate check fires.
        safety.state.steering_angle = 1400
        with pytest.raises(SafetyViolation) as exc_info:
            safety._check_steering_control(_msg(1400))
        assert exc_info.value.violation_type == "steering_rate_limit"


class TestLongitudinalSafety:
    """Test longitudinal control safety checks"""
    
    def test_accel_limit(self):
        """Test acceleration limit enforcement"""
        safety = TeslaSafety()
        safety.state.controls_allowed = True
        # Hold at inactive (375 = 0 m/s²) so the rate check does not trip
        # on the first frame.
        safety.state.last_accel_max = 375
        safety.state.last_accel_min = 375

        def _pack(accel_max: int, accel_min: int) -> bytes:
            # Matches tesla_safety parsing:
            #   raw_accel_max = ((data[6] & 0x1F) << 4) | (data[5] >> 4)
            #   raw_accel_min = ((data[5] & 0x0F) << 5) | (data[4] >> 3)
            data = bytearray(8)
            data[6] = (accel_max >> 4) & 0x1F
            data[5] = ((accel_max & 0x0F) << 4) | ((accel_min >> 5) & 0x0F)
            data[4] = (accel_min & 0x1F) << 3
            return bytes(data)

        # Valid: mild accel request (390 = +15 units over inactive) with
        # accel_min held at inactive; within MAX_ACCEL=415 / MIN_ACCEL=305
        # and within the 16-unit rate limit.
        allowed, _ = safety._check_longitudinal_control(_pack(390, 375))
        assert allowed

        # Invalid: accel_max beyond MAX_ACCEL (450 > 415).
        with pytest.raises(SafetyViolation) as exc_info:
            safety._check_longitudinal_control(_pack(450, 375))
        assert exc_info.value.violation_type == "max_accel_limit"

    def test_aeb_blocked(self):
        """Test AEB commands are blocked"""
        safety = TeslaSafety()
        safety.state.controls_allowed = True

        data = bytearray(8)
        data[2] = 0x01  # AEB event = 1 (ACTIVE)

        with pytest.raises(SafetyViolation) as exc_info:
            safety._check_longitudinal_control(bytes(data))
        assert exc_info.value.violation_type == "aeb_blocked"


class TestHeartbeat:
    """Test safety heartbeat"""
    
    def test_heartbeat_timeout(self):
        """Test controls disengage on heartbeat timeout"""
        safety = TeslaSafety()
        safety.state.controls_allowed = True
        safety.state.last_heartbeat_time = time.monotonic() - 1.0  # 1 second ago
        
        # Should timeout (200ms limit)
        assert not safety.check_heartbeat()
        assert not safety.state.controls_allowed
    
    def test_heartbeat_valid(self):
        """Test controls allowed with valid heartbeat"""
        safety = TeslaSafety()
        safety.state.controls_allowed = True
        safety.state.last_heartbeat_time = time.monotonic()
        
        assert safety.check_heartbeat()


if __name__ == "__main__":
    pytest.main([__file__, "-v"])

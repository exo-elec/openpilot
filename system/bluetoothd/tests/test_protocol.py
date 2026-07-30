#!/usr/bin/env python3
"""Tests for Bluetooth protocol."""
import pytest

from openpilot.system.bluetoothd.protocol import (
    Frame, MessageType, make_ack, make_error, get_device_info, PROTOCOL_VERSION
)


class TestFrame:
    """Test protocol frame."""
    
    def test_encode_decode(self):
        """Test frame encoding/decoding."""
        frame = Frame(MessageType.PING, b'')
        encoded = frame.encode()
        
        decoded, consumed = Frame.decode(encoded)
        assert decoded is not None
        assert consumed == len(encoded)
        assert decoded.msg_type == MessageType.PING
    
    def test_json_frame(self):
        """Test JSON payload."""
        data = {'speed': 25.5}
        frame = Frame.from_json(MessageType.TELEMETRY_VEHICLE, data)
        
        decoded = frame.to_json()
        assert decoded['speed'] == 25.5
    
    def test_partial_data(self):
        """Test partial frame handling."""
        decoded, consumed = Frame.decode(b'\x00\x00')
        assert decoded is None
        assert consumed == 0


class TestHelpers:
    """Test helper functions."""
    
    def test_make_ack(self):
        """Test ACK creation."""
        ack = make_ack(MessageType.CMD_NAVIGATE)
        assert ack.msg_type == MessageType.RESPONSE_ACK
    
    def test_make_error(self):
        """Test error creation."""
        err = make_error('test error')
        assert err.msg_type == MessageType.RESPONSE_ERROR
    
    def test_device_info(self):
        """Test device info."""
        info = get_device_info('phase_2')
        assert info['protocolVersion'] == PROTOCOL_VERSION
        assert info['hasTeleRoad'] is True


if __name__ == '__main__':
    pytest.main([__file__, '-v'])

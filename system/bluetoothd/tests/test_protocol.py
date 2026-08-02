#!/usr/bin/env python3
"""Tests for Bluetooth protocol."""
import json

import pytest

from openpilot.system.bluetoothd.protocol import (
    Frame, MessageType, make_ack, make_error, get_device_info, PROTOCOL_VERSION
)
from openpilot.system.bluetoothd.ncp_session import NCPSession


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

    def test_device_info_advertises_convoy(self):
        """Convoy is capability-gated on the phone via supportedServices."""
        info = get_device_info('phase_2')
        assert 'convoyFollow' in info['supportedServices']


class FakeParams:
    """In-memory stand-in for openpilot.common.params.Params."""

    def __init__(self):
        self._data: dict[str, str] = {}

    def get(self, key, block=False):
        return self._data.get(key)

    def put(self, key, val):
        self._data[key] = val

    def put_nonblocking(self, key, val):
        self._data[key] = val

    def remove(self, key):
        self._data.pop(key, None)


class TestConvoy:
    """Round-trip tests for the dedicated convoy NCP path (0x70/0x71)."""

    def _session(self):
        session = NCPSession(params=FakeParams())
        return session

    def test_convoy_lead_sets_nav_destination(self):
        session = self._session()
        frame = Frame.from_json(MessageType.CMD_CONVOY_LEAD, {
            'friendId': 'friend-123',
            'latitude': 13.7563,
            'longitude': 100.5018,
        })

        response = session.handle_frame(frame)

        assert response.msg_type == MessageType.RESPONSE_ACK
        dest = json.loads(session.params.get('NavDestination'))
        assert dest['latitude'] == pytest.approx(13.7563)
        assert dest['longitude'] == pytest.approx(100.5018)
        assert 'friend-123' in dest['place_name']

    def test_convoy_lead_missing_position_errors(self):
        session = self._session()
        frame = Frame.from_json(MessageType.CMD_CONVOY_LEAD, {'friendId': 'friend-123'})

        response = session.handle_frame(frame)

        assert response.msg_type == MessageType.RESPONSE_ERROR

    def test_convoy_cancel_clears_nav_destination(self):
        session = self._session()
        session.params.put('NavDestination', json.dumps({'latitude': 1.0, 'longitude': 2.0}))

        response = session.handle_frame(Frame(MessageType.CMD_CONVOY_CANCEL, b'{}'))

        assert response.msg_type == MessageType.RESPONSE_ACK
        assert session.params.get('NavDestination') is None


if __name__ == '__main__':
    pytest.main([__file__, '-v'])

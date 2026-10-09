#!/usr/bin/env python3
"""Tests for device classification."""
import pytest

from openpilot.system.bluetoothd.device import classify, Type, SPP


class TestClassify:
    """Test device classification."""

    def test_mobile(self):
        """Test mobile detection via SPP."""
        info = classify('00:11:22:33:44:66', 'iPhone', [SPP])
        assert info.is_mobile()

    def test_mobile_name_pattern(self):
        """Test phone name pattern."""
        info = classify('00:11:22:33:44:88', 'Samsung Galaxy', [])
        assert info.is_mobile()

    def test_navpilot_name(self):
        """Test NavPilot app detection."""
        info = classify('00:11:22:33:44:99', 'NavPilot', [SPP])
        assert info.is_mobile()

    def test_unknown_device(self):
        """Test unknown device classification."""
        info = classify('00:11:22:33:44:AA', 'Unknown Device', [])
        assert info.device_type == Type.UNKNOWN


if __name__ == '__main__':
    pytest.main([__file__, '-v'])  # noqa: TID251

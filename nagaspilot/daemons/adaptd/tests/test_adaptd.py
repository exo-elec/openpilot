#!/usr/bin/env python3
"""Tests for adaptd adaptive driving daemon."""
from __future__ import annotations

import sys
from unittest.mock import MagicMock  # noqa: TID251

import pytest

# Mock cereal.messaging BEFORE importing adaptd (avoids ARM .so on dev PC)
mock_messaging = MagicMock()
mock_messaging.PubMaster = lambda _: MagicMock()
mock_messaging.SubMaster = lambda _: MagicMock()
mock_messaging.new_message = lambda service, valid=True: MagicMock()
sys.modules['cereal.messaging'] = mock_messaging

from cereal import log
from cereal import custom
from openpilot.selfdrive.adaptd.adaptd import (
  AdaptiveDrivingComputer, AdaptD,
)

LongitudinalPersonality = log.LongitudinalPersonality
NcpVehicleData = custom.NcpVehicleData


class TestAdaptiveDrivingComputer:
  """Test AdaptiveDrivingComputer profile computation."""

  def test_normal_conditions_standard(self):
    comp = AdaptiveDrivingComputer()
    comp.set_enabled(True)
    vd = NcpVehicleData.new_message()
    vd.valid = True
    vd.batterySoc = 50.0
    vd.batteryTempMax = 25.0
    vd.batteryTempMin = 20.0
    vd.motorTemp = 50.0
    vd.inverterTemp = 40.0
    vd.coolantTemp = 85.0
    vd.rangeRemaining = 100.0
    profile = comp.update(vd)
    assert profile.enabled
    assert profile.personality == LongitudinalPersonality.standard
    assert profile.reason_code == "normal"
    assert profile.accel_max == pytest.approx(2.0)
    assert profile.accel_min == pytest.approx(-3.48)

  def test_low_soc_relaxed(self):
    comp = AdaptiveDrivingComputer()
    comp.set_enabled(True)
    vd = NcpVehicleData.new_message()
    vd.valid = True
    vd.batterySoc = 15.0
    vd.batteryTempMax = 25.0
    vd.motorTemp = 50.0
    vd.inverterTemp = 40.0
    vd.coolantTemp = 85.0
    vd.rangeRemaining = 100.0
    profile = comp.update(vd)
    assert profile.personality == LongitudinalPersonality.relaxed
    assert profile.accel_max == pytest.approx(1.4)
    assert "Low SOC" in profile.reason
    assert "low_soc" in profile.reason_code

  def test_critical_soc_traffic(self):
    comp = AdaptiveDrivingComputer()
    comp.set_enabled(True)
    vd = NcpVehicleData.new_message()
    vd.valid = True
    vd.batterySoc = 8.0
    vd.batteryTempMax = 25.0
    vd.motorTemp = 50.0
    vd.inverterTemp = 40.0
    vd.coolantTemp = 85.0
    vd.rangeRemaining = 100.0
    profile = comp.update(vd)
    assert profile.personality == LongitudinalPersonality.traffic
    assert profile.accel_max == pytest.approx(1.0)
    assert "Critical SOC" in profile.reason

  def test_high_soc_limited_regen(self):
    comp = AdaptiveDrivingComputer()
    comp.set_enabled(True)
    vd = NcpVehicleData.new_message()
    vd.valid = True
    vd.batterySoc = 95.0
    vd.batteryTempMax = 25.0
    vd.motorTemp = 50.0
    vd.inverterTemp = 40.0
    vd.coolantTemp = 85.0
    vd.rangeRemaining = 100.0
    profile = comp.update(vd)
    assert profile.regen_strength == pytest.approx(0.6)
    assert "high_soc" in profile.reason_code

  def test_hot_battery_derating(self):
    comp = AdaptiveDrivingComputer()
    comp.set_enabled(True)
    vd = NcpVehicleData.new_message()
    vd.valid = True
    vd.batterySoc = 50.0
    vd.batteryTempMax = 50.0
    vd.batteryTempMin = 20.0
    vd.motorTemp = 50.0
    vd.inverterTemp = 40.0
    vd.coolantTemp = 85.0
    vd.rangeRemaining = 100.0
    profile = comp.update(vd)
    assert profile.thermal_derating
    assert profile.accel_max == pytest.approx(1.2)
    assert "hot_batt_temp" in profile.reason_code

  def test_critical_battery_temp(self):
    comp = AdaptiveDrivingComputer()
    comp.set_enabled(True)
    vd = NcpVehicleData.new_message()
    vd.valid = True
    vd.batterySoc = 50.0
    vd.batteryTempMax = 60.0
    vd.motorTemp = 50.0
    vd.inverterTemp = 40.0
    vd.coolantTemp = 85.0
    vd.rangeRemaining = 100.0
    profile = comp.update(vd)
    assert profile.accel_max == pytest.approx(0.8)
    assert "critical_batt_temp" in profile.reason_code

  def test_cold_battery_limited_regen(self):
    comp = AdaptiveDrivingComputer()
    comp.set_enabled(True)
    vd = NcpVehicleData.new_message()
    vd.valid = True
    vd.batterySoc = 50.0
    vd.batteryTempMax = 25.0
    vd.batteryTempMin = -5.0
    vd.motorTemp = 50.0
    vd.inverterTemp = 40.0
    vd.coolantTemp = 85.0
    vd.rangeRemaining = 100.0
    profile = comp.update(vd)
    assert profile.regen_strength == pytest.approx(0.4)
    assert "cold_batt_temp" in profile.reason_code

  def test_hot_motor_derating(self):
    comp = AdaptiveDrivingComputer()
    comp.set_enabled(True)
    vd = NcpVehicleData.new_message()
    vd.valid = True
    vd.batterySoc = 50.0
    vd.batteryTempMax = 25.0
    vd.motorTemp = 85.0
    vd.inverterTemp = 40.0
    vd.coolantTemp = 85.0
    vd.rangeRemaining = 100.0
    profile = comp.update(vd)
    assert profile.thermal_derating
    assert profile.accel_max == pytest.approx(1.0)
    assert "hot_motor_temp" in profile.reason_code

  def test_critical_motor_temp(self):
    comp = AdaptiveDrivingComputer()
    comp.set_enabled(True)
    vd = NcpVehicleData.new_message()
    vd.valid = True
    vd.batterySoc = 50.0
    vd.batteryTempMax = 25.0
    vd.motorTemp = 110.0
    vd.inverterTemp = 40.0
    vd.coolantTemp = 85.0
    vd.rangeRemaining = 100.0
    profile = comp.update(vd)
    assert profile.accel_max == pytest.approx(0.6)
    assert "critical_motor_temp" in profile.reason_code

  def test_hot_inverter(self):
    comp = AdaptiveDrivingComputer()
    comp.set_enabled(True)
    vd = NcpVehicleData.new_message()
    vd.valid = True
    vd.batterySoc = 50.0
    vd.batteryTempMax = 25.0
    vd.motorTemp = 50.0
    vd.inverterTemp = 75.0
    vd.coolantTemp = 85.0
    vd.rangeRemaining = 100.0
    profile = comp.update(vd)
    assert profile.thermal_derating
    assert profile.accel_max == pytest.approx(1.2)
    assert "hot_inverter_temp" in profile.reason_code

  def test_low_range_relaxed(self):
    comp = AdaptiveDrivingComputer()
    comp.set_enabled(True)
    vd = NcpVehicleData.new_message()
    vd.valid = True
    vd.batterySoc = 50.0
    vd.batteryTempMax = 25.0
    vd.motorTemp = 50.0
    vd.inverterTemp = 40.0
    vd.coolantTemp = 85.0
    vd.rangeRemaining = 30.0
    profile = comp.update(vd)
    assert profile.personality == LongitudinalPersonality.relaxed
    assert profile.accel_max == pytest.approx(1.2)
    assert "low_range" in profile.reason_code

  def test_critical_range_traffic(self):
    comp = AdaptiveDrivingComputer()
    comp.set_enabled(True)
    vd = NcpVehicleData.new_message()
    vd.valid = True
    vd.batterySoc = 50.0
    vd.batteryTempMax = 25.0
    vd.motorTemp = 50.0
    vd.inverterTemp = 40.0
    vd.coolantTemp = 85.0
    vd.rangeRemaining = 15.0
    profile = comp.update(vd)
    assert profile.personality == LongitudinalPersonality.traffic
    assert profile.accel_max == pytest.approx(0.8)
    assert "critical_range" in profile.reason_code

  def test_high_coolant_temp(self):
    comp = AdaptiveDrivingComputer()
    comp.set_enabled(True)
    vd = NcpVehicleData.new_message()
    vd.valid = True
    vd.batterySoc = 50.0
    vd.batteryTempMax = 25.0
    vd.motorTemp = 50.0
    vd.inverterTemp = 40.0
    vd.coolantTemp = 105.0
    vd.rangeRemaining = 100.0
    profile = comp.update(vd)
    assert profile.thermal_derating
    assert profile.accel_max == pytest.approx(1.0)
    assert "high_coolant_temp" in profile.reason_code

  def test_multiple_reasons_stacked(self):
    comp = AdaptiveDrivingComputer()
    comp.set_enabled(True)
    vd = NcpVehicleData.new_message()
    vd.valid = True
    vd.batterySoc = 15.0
    vd.batteryTempMax = 50.0
    vd.motorTemp = 50.0
    vd.inverterTemp = 40.0
    vd.coolantTemp = 85.0
    vd.rangeRemaining = 100.0
    profile = comp.update(vd)
    assert "Low SOC" in profile.reason
    assert "Hot battery" in profile.reason
    assert profile.accel_max == pytest.approx(1.2)  # min of 1.4 (SOC) and 1.2 (temp)

  def test_invalid_data_returns_disabled_profile(self):
    comp = AdaptiveDrivingComputer()
    comp.set_enabled(True)
    vd = NcpVehicleData.new_message()
    vd.valid = False
    profile = comp.update(vd)
    assert profile.reason_code == "no_data"

  def test_hysteresis_prevents_oscillation(self):
    comp = AdaptiveDrivingComputer()
    comp.set_enabled(True)
    vd = NcpVehicleData.new_message()
    vd.valid = True
    vd.batterySoc = 50.0
    vd.batteryTempMax = 25.0
    vd.motorTemp = 50.0
    vd.inverterTemp = 40.0
    vd.coolantTemp = 85.0
    vd.rangeRemaining = 100.0

    # Start with normal
    profile = comp.update(vd)
    assert profile.personality == LongitudinalPersonality.standard

    # Jump to low SOC — should change immediately (first change)
    vd.batterySoc = 15.0
    profile = comp.update(vd)
    assert profile.personality == LongitudinalPersonality.relaxed

    # Jump back to normal immediately — should stay relaxed due to hysteresis
    vd.batterySoc = 50.0
    profile = comp.update(vd)
    assert profile.personality == LongitudinalPersonality.relaxed

    # After hysteresis expires, should change back
    comp._last_change_time = 0.0  # force expiry
    profile = comp.update(vd)
    assert profile.personality == LongitudinalPersonality.standard


class TestAdaptD:
  """Test AdaptD daemon wiring."""

  def test_daemon_init(self):
    d = AdaptD()
    assert d.computer is not None
    assert d._enabled is False

  def test_refresh_params_no_params(self):
    d = AdaptD()
    d._refresh_params()  # Should not crash when Params unavailable


if __name__ == '__main__':
  pytest.main([__file__, '-v'])  # noqa: TID251

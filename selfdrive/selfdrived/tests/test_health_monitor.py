"""Tests for EOP Health Monitor graduated degradation logic."""
import time
from unittest.mock import MagicMock  # noqa: TID251

from cereal import log

from openpilot.selfdrive.selfdrived.events import Events, EventName
from openpilot.selfdrive.selfdrived.selfdrived import SelfdriveD

ThermalStatus = log.DeviceState.ThermalStatus


def _make_selfdrived(thermal, cpu_usage, mem_usage):
  """Create a minimal SelfdriveD-like object with mocked dependencies."""
  sd = MagicMock()
  sd.events = Events()
  sd._health_monitor_enabled = True
  sd._health_cpu_history = []
  sd._health_thermal_history = []
  sd._health_warning_active = False
  sd._health_warn_start_time = 0.0

  ds = log.DeviceState.new_message()
  ds.thermalStatus = thermal
  ds.cpuUsagePercent = cpu_usage
  ds.memoryUsagePercent = mem_usage
  sd.sm = MagicMock()
  sd.sm.__getitem__ = lambda _self, key: ds if key == 'deviceState' else MagicMock()
  return sd


def _call_evaluate(sd):
  """Call _evaluate_health_monitor on a mocked SelfdriveD."""
  SelfdriveD._evaluate_health_monitor(sd)


class TestHealthMonitor:
  """Test Health Monitor state transitions."""

  def test_disabled_when_not_enabled(self):
    sd = _make_selfdrived(ThermalStatus.danger, [100], 100)
    sd._health_monitor_enabled = False
    _call_evaluate(sd)
    assert EventName.healthCriticalStop not in sd.events.events

  def test_critical_danger_thermal(self):
    sd = _make_selfdrived(ThermalStatus.danger, [50], 50)
    _call_evaluate(sd)
    assert EventName.healthCriticalStop in sd.events.events
    assert sd._health_warning_active is False

  def test_degraded_red_thermal(self):
    sd = _make_selfdrived(ThermalStatus.red, [50], 50)
    _call_evaluate(sd)
    assert EventName.healthDegradedStop in sd.events.events
    assert EventName.healthCriticalStop not in sd.events.events
    assert sd._health_warning_active is False

  def test_degraded_high_cpu(self):
    sd = _make_selfdrived(ThermalStatus.green, [96], 50)
    _call_evaluate(sd)
    assert EventName.healthDegradedStop in sd.events.events

  def test_degraded_high_memory(self):
    sd = _make_selfdrived(ThermalStatus.green, [50], 96)
    _call_evaluate(sd)
    assert EventName.healthDegradedStop in sd.events.events

  def test_warning_yellow_thermal_single_sign(self):
    # Single warning sign should not trigger warning immediately
    sd = _make_selfdrived(ThermalStatus.yellow, [50], 50)
    _call_evaluate(sd)
    assert EventName.healthWarning not in sd.events.events

  def test_warning_two_signs(self):
    # Two warning signs should trigger warning
    sd = _make_selfdrived(ThermalStatus.yellow, [85], 50)
    _call_evaluate(sd)
    assert EventName.healthWarning in sd.events.events
    assert sd._health_warning_active is True

  def test_warning_escalation_to_degraded(self):
    # Sustained warning should escalate to degraded after 10s
    sd = _make_selfdrived(ThermalStatus.yellow, [85], 50)
    sd._health_warn_start_time = time.monotonic() - 11.0
    sd._health_warning_active = True
    _call_evaluate(sd)
    assert EventName.healthWarning in sd.events.events
    assert EventName.healthDegradedStop in sd.events.events

  def test_warning_no_escalation_before_10s(self):
    sd = _make_selfdrived(ThermalStatus.yellow, [85], 50)
    sd._health_warn_start_time = time.monotonic() - 5.0
    sd._health_warning_active = True
    _call_evaluate(sd)
    assert EventName.healthWarning in sd.events.events
    assert EventName.healthDegradedStop not in sd.events.events

  def test_sustained_single_warning_escalates(self):
    # First call: single warning, no event
    sd = _make_selfdrived(ThermalStatus.yellow, [50], 50)
    _call_evaluate(sd)
    assert EventName.healthWarning not in sd.events.events
    # Second call with same condition: now warning because _health_warning_active is True
    _call_evaluate(sd)
    assert EventName.healthWarning in sd.events.events

  def test_clear_warning_when_healthy(self):
    sd = _make_selfdrived(ThermalStatus.green, [50], 50)
    sd._health_warning_active = True
    sd._health_warn_start_time = time.monotonic()
    _call_evaluate(sd)
    assert EventName.healthWarning not in sd.events.events
    assert sd._health_warning_active is False

  def test_critical_overrides_degraded(self):
    # If both danger and red conditions exist, critical wins
    sd = _make_selfdrived(ThermalStatus.danger, [96], 96)
    _call_evaluate(sd)
    assert EventName.healthCriticalStop in sd.events.events
    assert EventName.healthDegradedStop not in sd.events.events

  def test_degraded_overrides_warning(self):
    # If both degraded and warning conditions exist, degraded wins
    sd = _make_selfdrived(ThermalStatus.red, [85], 50)
    _call_evaluate(sd)
    assert EventName.healthDegradedStop in sd.events.events
    assert EventName.healthWarning not in sd.events.events

  def test_cpu_rising_trend_counts_as_warning(self):
    sd = _make_selfdrived(ThermalStatus.green, [55], 50)
    sd._health_cpu_history = [40.0] * 299 + [55.0]  # Rising by 15
    _call_evaluate(sd)
    # Single warning sign arms on first call, event on second
    assert sd._health_warning_active is True
    _call_evaluate(sd)
    assert EventName.healthWarning in sd.events.events

  def test_thermal_rising_trend_counts_as_warning(self):
    sd = _make_selfdrived(ThermalStatus.yellow, [50], 50)
    sd._health_thermal_history = [0] * 299 + [1]  # green -> yellow
    _call_evaluate(sd)
    assert EventName.healthWarning in sd.events.events

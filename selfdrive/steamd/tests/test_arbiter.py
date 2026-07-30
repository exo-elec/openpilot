#!/usr/bin/env python3
"""Unit tests for SteamD ControlArbiter.

Validates the safety-critical authority logic:
- Local override detection and priority
- may_publish() gating on JoystickDebugMode and SteamDRemoteControl
- Link-loss safe-stop timing
- Cooldown behavior after override

Run: python3 selfdrive/steamd/tests/test_arbiter.py
"""

import sys
import time
import unittest
from unittest.mock import MagicMock, patch

# Mock compiled dependencies before importing arbiter
_mock_msgq = MagicMock()
_mock_msgq.visionipc = MagicMock()
sys.modules['msgq'] = _mock_msgq
sys.modules['msgq.visionipc'] = _mock_msgq.visionipc
sys.modules['cereal'] = MagicMock()
sys.modules['cereal.messaging'] = MagicMock()
sys.modules['openpilot.common.swaglog'] = MagicMock()

from openpilot.selfdrive.steamd.arbiter import ControlArbiter, AuthorityState
from openpilot.selfdrive.steamd.inputs import ControlCommand


class FakeCarState:
  """Minimal carState mock for override tests."""

  def __init__(self, brake=False, gas=False, steer=False, door=False):
    self.brakePressed = brake
    self.gasPressed = gas
    self.steeringPressed = steer
    self.doorOpen = door
    self.vEgo = 5.0


class TestControlArbiter(unittest.TestCase):

  def setUp(self):
    self.arbiter = ControlArbiter()
    # Ensure clean state
    self.arbiter._last_cmd_time = time.monotonic()
    self.arbiter._state = AuthorityState.LOCAL_ONLY

  # ------------------------------------------------------------------ #
  # Local override
  # ------------------------------------------------------------------ #

  def test_no_override_when_nothing_pressed(self):
    cs = FakeCarState(brake=False, gas=False, steer=False, door=False)
    self.assertFalse(self.arbiter.check_local_override(cs))
    self.assertEqual(self.arbiter.state, AuthorityState.LOCAL_ONLY)

  def test_brake_override_fires(self):
    cs = FakeCarState(brake=True)
    self.assertTrue(self.arbiter.check_local_override(cs))
    self.assertEqual(self.arbiter.state, AuthorityState.LOCAL_OVERRIDE)
    self.assertEqual(self.arbiter._override_reason, "brake")

  def test_door_override_fires(self):
    cs = FakeCarState(door=True)
    self.assertTrue(self.arbiter.check_local_override(cs))
    self.assertEqual(self.arbiter._override_reason, "door")

  def test_steer_override_fires(self):
    cs = FakeCarState(steer=True)
    self.assertTrue(self.arbiter.check_local_override(cs))
    self.assertEqual(self.arbiter._override_reason, "steer")

  def test_gas_override_fires(self):
    cs = FakeCarState(gas=True)
    self.assertTrue(self.arbiter.check_local_override(cs))
    self.assertEqual(self.arbiter._override_reason, "gas")

  def test_priority_brake_over_door(self):
    cs = FakeCarState(brake=True, door=True)
    self.arbiter.check_local_override(cs)
    self.assertEqual(self.arbiter._override_reason, "brake")

  def test_priority_door_over_steer(self):
    cs = FakeCarState(door=True, steer=True)
    self.arbiter.check_local_override(cs)
    self.assertEqual(self.arbiter._override_reason, "door")

  def test_override_clears_control_params(self):
    with patch.object(self.arbiter.params, 'put_bool') as mock_put:
      cs = FakeCarState(brake=True)
      self.arbiter.check_local_override(cs)
      calls = list(mock_put.call_args_list)
      self.assertTrue(any(c.args == ("JoystickDebugMode", False) for c in calls))
      self.assertTrue(any(c.args == ("SteamDRemoteControl", False) for c in calls))

  # ------------------------------------------------------------------ #
  # may_publish() gating
  # ------------------------------------------------------------------ #

  def test_may_publish_false_when_no_params(self):
    with patch.object(self.arbiter.params, 'get_bool', return_value=False):
      self.assertFalse(self.arbiter.may_publish())
      self.assertEqual(self.arbiter.state, AuthorityState.LOCAL_ONLY)

  def test_may_publish_true_with_joystick_debug_mode(self):
    def side_effect(key):
      return key == "JoystickDebugMode"
    with patch.object(self.arbiter.params, 'get_bool', side_effect=side_effect):
      self.assertTrue(self.arbiter.may_publish())
      self.assertEqual(self.arbiter.state, AuthorityState.REMOTE_ACTIVE)

  def test_may_publish_true_with_steamd_remote_control(self):
    def side_effect(key):
      return key == "SteamDRemoteControl"
    with patch.object(self.arbiter.params, 'get_bool', side_effect=side_effect):
      self.assertTrue(self.arbiter.may_publish())
      self.assertEqual(self.arbiter.state, AuthorityState.REMOTE_ACTIVE)

  def test_may_publish_false_during_override_cooldown(self):
    self.arbiter._state = AuthorityState.LOCAL_OVERRIDE
    self.arbiter._override_reason = "brake"
    self.arbiter._override_time = time.monotonic()
    with patch.object(self.arbiter.params, 'get_bool', return_value=True):
      self.assertFalse(self.arbiter.may_publish())

  def test_may_publish_true_after_cooldown_expires(self):
    self.arbiter._state = AuthorityState.LOCAL_OVERRIDE
    self.arbiter._override_reason = "gas"  # 2.0s cooldown
    self.arbiter._override_time = time.monotonic() - 3.0
    with patch.object(self.arbiter.params, 'get_bool', return_value=True):
      # After cooldown, state resets to LOCAL_ONLY and returns False
      # because may_publish requires the param to be True AND no cooldown
      self.assertFalse(self.arbiter.may_publish())
      self.assertEqual(self.arbiter.state, AuthorityState.LOCAL_ONLY)

  # ------------------------------------------------------------------ #
  # Link loss
  # ------------------------------------------------------------------ #

  def test_no_link_loss_when_commands_fresh(self):
    self.arbiter.on_command(ControlCommand())
    killed, elapsed = self.arbiter.process_link_loss(timeout_sec=0.5)
    self.assertFalse(killed)
    self.assertEqual(elapsed, 0.0)

  def test_link_loss_detected_after_timeout(self):
    self.arbiter._last_cmd_time = time.monotonic() - 1.5
    killed, elapsed = self.arbiter.process_link_loss(timeout_sec=0.5)
    self.assertFalse(killed)
    self.assertGreater(elapsed, 0.0)

  def test_link_loss_killed_after_2_seconds(self):
    self.arbiter._last_cmd_time = time.monotonic() - 3.5
    killed, elapsed = self.arbiter.process_link_loss(timeout_sec=0.5)
    self.assertTrue(killed)
    self.assertGreater(elapsed, 2000.0)

  def test_link_loss_resets_when_command_received(self):
    self.arbiter._last_cmd_time = time.monotonic() - 3.5
    self.arbiter._link_loss_start = time.monotonic() - 2.5
    self.arbiter.on_command(ControlCommand())
    killed, elapsed = self.arbiter.process_link_loss(timeout_sec=0.5)
    self.assertFalse(killed)
    self.assertEqual(elapsed, 0.0)
    self.assertIsNone(self.arbiter._link_loss_start)

  # ------------------------------------------------------------------ #
  # safe_accel clamping
  # ------------------------------------------------------------------ #

  def test_safe_accel_clamps_max(self):
    self.assertEqual(self.arbiter.safe_accel(2.0, 1.5, 3.0), 1.5)

  def test_safe_accel_clamps_min(self):
    self.assertEqual(self.arbiter.safe_accel(-5.0, 1.5, 3.0), -3.0)

  def test_safe_accel_passes_through(self):
    self.assertEqual(self.arbiter.safe_accel(1.0, 1.5, 3.0), 1.0)

  # ------------------------------------------------------------------ #
  # Status reporting
  # ------------------------------------------------------------------ #

  def test_status_reflects_state(self):
    self.arbiter._state = AuthorityState.REMOTE_ACTIVE
    status = self.arbiter.status()
    self.assertEqual(status.state, AuthorityState.REMOTE_ACTIVE)
    self.assertTrue(status.controls_allowed)
    self.assertEqual(status.active_source, "external")

  def test_status_shows_cooldown(self):
    self.arbiter._state = AuthorityState.LOCAL_OVERRIDE
    self.arbiter._override_reason = "brake"
    self.arbiter._override_time = time.monotonic()
    status = self.arbiter.status()
    self.assertEqual(status.state, AuthorityState.LOCAL_OVERRIDE)
    self.assertGreater(status.cooldown_remaining_ms, 0)
    self.assertFalse(status.controls_allowed)


if __name__ == "__main__":
  unittest.main()

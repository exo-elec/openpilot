#!/usr/bin/env python3
"""
SteamD Control Arbiter

Single source of truth for external control authority.

Rules:
  1. SteamD is the only external control publisher (joystickd merged).
  2. Local driver input (brake, gas, steering torque) immediately overrides
     and kills the remote session.
  3. Reuses existing JoystickDebugMode param as the gate. This minimizes
     impact — controlsd already stops when this param is True.
  4. When local override fires, JoystickDebugMode is cleared and controlsd
     auto-restarts via process manager.
"""

from __future__ import annotations

import time
from dataclasses import dataclass
from enum import Enum

from openpilot.common.params import Params
from openpilot.common.swaglog import cloudlog

from openpilot.selfdrive.steamd.inputs import ControlCommand


class AuthorityState(Enum):
  LOCAL_ONLY = "local_only"
  REMOTE_ACTIVE = "remote_active"
  LOCAL_OVERRIDE = "local_override"
  LINK_LOSS = "link_loss"


@dataclass
class ArbiterStatus:
  state: AuthorityState = AuthorityState.LOCAL_ONLY
  override_reason: str | None = None
  cooldown_remaining_ms: int = 0
  controls_allowed: bool = False
  active_source: str = "none"


class ControlArbiter:
  """Decides whether external control commands may be published to the vehicle."""

  # Override triggers and their cooldowns
  COOLDOWNS = {
    "brake": 3.0,
    "gas": 2.0,
    "steer": 2.0,
    "door": 3.0,
    "link_loss": 2.0,
  }

  def __init__(self):
    self.params = Params()
    self._state = AuthorityState.LOCAL_ONLY
    self._override_reason: str | None = None
    self._override_time: float = 0.0
    self._last_cmd_time: float = time.monotonic()
    self._link_loss_start: float | None = None

  @property
  def state(self) -> AuthorityState:
    return self._state

  def status(self) -> ArbiterStatus:
    now = time.monotonic()
    cooldown = max(0.0, self._override_time + self.COOLDOWNS.get(self._override_reason or "", 0.0) - now)
    return ArbiterStatus(
      state=self._state,
      override_reason=self._override_reason,
      cooldown_remaining_ms=int(cooldown * 1000),
      controls_allowed=self._state == AuthorityState.REMOTE_ACTIVE,
      active_source="none" if self._state != AuthorityState.REMOTE_ACTIVE else "external",
    )

  def check_local_override(self, cs) -> bool:
    """Evaluate carState for local driver override. Returns True if override fired."""
    if cs is None:
      return False

    triggers = []
    if getattr(cs, "brakePressed", False):
      triggers.append("brake")
    if getattr(cs, "gasPressed", False):
      triggers.append("gas")
    if getattr(cs, "steeringPressed", False):
      triggers.append("steer")
    if getattr(cs, "doorOpen", False):
      triggers.append("door")

    if not triggers:
      return False

    # Take the highest-priority trigger
    priority = ["brake", "door", "steer", "gas"]
    reason = next((p for p in priority if p in triggers), triggers[0])

    self._trigger_override(reason)
    return True

  def force_kill(self, reason: str):
    """Public: immediately kill the remote session and clear control params."""
    self._trigger_override(reason)

  def _trigger_override(self, reason: str):
    """Internal: record override and clear external-control param."""
    cloudlog.warning(f"SteamD arbiter: local override ({reason})")
    self._state = AuthorityState.LOCAL_OVERRIDE
    self._override_reason = reason
    self._override_time = time.monotonic()
    self.params.put_bool("JoystickDebugMode", False)
    self.params.put_bool("SteamDRemoteControl", False)

  def may_publish(self) -> bool:
    """Return True if SteamD is allowed to publish carControl this tick."""
    # Reuse existing JoystickDebugMode param as the external-control gate.
    # This minimizes impact: controlsd already stops when this param is True.
    if not (self.params.get_bool("JoystickDebugMode") or self.params.get_bool("SteamDRemoteControl")):
      self._state = AuthorityState.LOCAL_ONLY
      return False

    # If in cooldown, block
    if self._state == AuthorityState.LOCAL_OVERRIDE:
      cooldown = self.COOLDOWNS.get(self._override_reason or "", 0.0)
      if time.monotonic() - self._override_time < cooldown:
        return False
      # Cooldown expired — transition back to standby
      self._state = AuthorityState.LOCAL_ONLY
      return False

    self._state = AuthorityState.REMOTE_ACTIVE
    return True

  def process_link_loss(self, timeout_sec: float) -> tuple[bool, float]:
    """Check for link loss.

    Returns:
      (killed, elapsed_ms)
      killed   — True if session exceeded 2 s without commands and was terminated.
      elapsed_ms — milliseconds since link loss began (0 if not in link loss).
    """
    now = time.monotonic()
    elapsed = now - self._last_cmd_time

    if elapsed < timeout_sec:
      self._link_loss_start = None
      return False, 0.0

    if self._link_loss_start is None:
      # Record when link loss began (last command time + timeout)
      # rather than 'now', so elapsed_ms is non-zero on first detection.
      self._link_loss_start = self._last_cmd_time + timeout_sec
      cloudlog.warning("SteamD arbiter: link loss detected")

    elapsed_ms = (now - self._link_loss_start) * 1000.0

    # Mark as killed after 2 s; the caller decides whether to keep braking
    # until standstill or release immediately based on vehicle speed.
    killed = now - self._link_loss_start > 2.0
    if killed:
      cloudlog.warning("SteamD arbiter: link loss timeout — entering emergency stop")

    return killed, elapsed_ms

  def on_command(self, cmd: ControlCommand | None):
    """Record that a command was received."""
    if cmd is not None:
      self._last_cmd_time = time.monotonic()

  def safe_accel(self, raw_accel: float, max_accel: float, max_decel: float) -> float:
    """Clamp accel to configured limits."""
    return float(max(-max_decel, min(max_accel, raw_accel)))

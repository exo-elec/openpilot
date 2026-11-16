"""Always-on Lane Centering Control (ALCC) state machine.

This helper owns ALCC state only. Runtime actuation and Panda authorization
remain explicit integration responsibilities.

ALCC gives up steering authority while the driver overrides. The brake mode
says what a brake press does: nothing (MAINTAIN), pause until released
(PAUSE), or release ALCC until the driver engages again with the LKAS / set /
resume button or by cycling the cruise main switch (DISENGAGE).
"""

from dataclasses import dataclass
from enum import IntEnum


class BrakeMode(IntEnum):
  MAINTAIN = 0
  PAUSE = 1
  DISENGAGE = 2

  @classmethod
  def from_param(cls, value) -> "BrakeMode":
    """A saved ALCCBrakeMode; anything unknown is MAINTAIN."""
    try:
      return cls(int(value))
    except (TypeError, ValueError):
      return cls.MAINTAIN


class ALCCState(IntEnum):
  DISABLED = 0
  PAUSED = 1
  ENABLED = 2
  SOFT_DISABLING = 3
  OVERRIDING = 4


@dataclass(frozen=True)
class ALCCInput:
  feature_enabled: bool
  engage_request: bool = False
  user_disable: bool = False
  immediate_disable: bool = False
  soft_disable: bool = False
  pause_condition: bool = False
  steering_override: bool = False
  calibrated: bool = True
  gear_ok: bool = True
  safety_ok: bool = True
  brake_pressed: bool = False
  engage_button: bool = False
  dt: float = 0.01


@dataclass(frozen=True)
class ALCCResult:
  state: ALCCState
  latched: bool
  active_suggestion: bool
  available: bool
  control_authority: bool
  reason: str


class ALCC:
  SOFT_DISABLE_TIME = 3.0

  def __init__(self, brake_mode: BrakeMode = BrakeMode.MAINTAIN):
    self.brake_mode = BrakeMode(brake_mode)
    self.state = ALCCState.DISABLED
    self._latched = False
    self._rearm_needed = False
    self._soft_disable_remaining = 0.0

  def update(self, sample: ALCCInput) -> ALCCResult:
    available = sample.feature_enabled and sample.calibrated and sample.gear_ok and sample.safety_ok
    reason = "ready"
    if self._rearm_needed and (sample.engage_button or not sample.engage_request):
      self._rearm_needed = False

    if not sample.feature_enabled:
      self.state = ALCCState.DISABLED
      self._latched = False
      reason = "feature_disabled"
    elif sample.user_disable or sample.immediate_disable or not sample.safety_ok:
      self.state = ALCCState.DISABLED
      self._latched = False
      reason = "safety_or_user_disable"
    else:
      if sample.engage_request and not self._rearm_needed:
        self._latched = True
      if self._latched and sample.brake_pressed and self.brake_mode is BrakeMode.DISENGAGE:
        self._latched = False
        self._rearm_needed = True
      if not self._latched:
        self.state = ALCCState.DISABLED
        reason = "not_engaged"
      elif not sample.calibrated or not sample.gear_ok or sample.pause_condition or \
           (sample.brake_pressed and self.brake_mode is BrakeMode.PAUSE):
        self.state = ALCCState.PAUSED
        reason = "paused_condition"
      elif sample.soft_disable:
        if self.state is not ALCCState.SOFT_DISABLING:
          self._soft_disable_remaining = self.SOFT_DISABLE_TIME
        self.state = ALCCState.SOFT_DISABLING
        self._soft_disable_remaining = max(0.0, self._soft_disable_remaining - max(0.0, sample.dt))
        if self._soft_disable_remaining == 0.0:
          self.state = ALCCState.DISABLED
          self._latched = False
          reason = "soft_disable_expired"
        else:
          reason = "soft_disabling"
      elif sample.steering_override:
        self.state = ALCCState.OVERRIDING
        reason = "driver_override"
      else:
        self.state = ALCCState.ENABLED

    # A steering override is a latched controller state for status/recovery,
    # but it must not request steering authority from controlsd.
    active = self.state in (ALCCState.ENABLED, ALCCState.SOFT_DISABLING)
    return ALCCResult(self.state, self._latched, active, available, False, reason)

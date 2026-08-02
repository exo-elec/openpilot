"""Comma 3-safe Always Lane Centering Control state proposal.

Unlike the EOP10 controller, this module does not mutate controls events or
claim Panda authority. It can be wired to logging before any control consumer.
"""

from dataclasses import dataclass
from enum import IntEnum


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
  dt: float = 0.01


@dataclass(frozen=True)
class ALCCResult:
  state: ALCCState
  latched: bool
  active_suggestion: bool
  available: bool
  control_authority: bool
  reason: str


class NGPALCC:
  SOFT_DISABLE_TIME = 3.0

  def __init__(self):
    self.state = ALCCState.DISABLED
    self._latched = False
    self._soft_disable_remaining = 0.0

  def update(self, sample: ALCCInput) -> ALCCResult:
    available = sample.feature_enabled and sample.calibrated and sample.gear_ok and sample.safety_ok
    reason = "ready"

    if not sample.feature_enabled:
      self.state = ALCCState.DISABLED
      self._latched = False
      reason = "feature_disabled"
    elif sample.user_disable or sample.immediate_disable or not sample.safety_ok:
      self.state = ALCCState.DISABLED
      self._latched = False
      reason = "safety_or_user_disable"
    else:
      if sample.engage_request:
        self._latched = True
      if not self._latched:
        self.state = ALCCState.DISABLED
        reason = "not_engaged"
      elif not sample.calibrated or not sample.gear_ok or sample.pause_condition:
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

    active = self.state in (ALCCState.ENABLED, ALCCState.SOFT_DISABLING, ALCCState.OVERRIDING)
    return ALCCResult(self.state, self._latched, active, available, False, reason)

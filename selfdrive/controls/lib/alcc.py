"""
alcc.py - Always Lane Centering Control (ALCC)

Enhanced with MADS state-machine logic:
  - Independent lateral engagement state machine
  - Event replacement (door open / seatbelt → paused, not disabled)
  - Panda lateral mismatch counter
  - Per-brand overrides (Hyundai LDA button, Tesla)
  - Steering mode on brake (remain active / pause / disengage)
  - Unified engagement mode
"""

import time
from enum import IntEnum
from dataclasses import dataclass

from cereal import car, log
from openpilot.common.params import Params
from openpilot.common.realtime import DT_CTRL
from openpilot.selfdrive.selfdrived.events import ET

EventName = log.OnroadEvent.EventName


class AlccState(IntEnum):
  disabled = 0
  paused = 1
  enabled = 2
  softDisabling = 3
  overriding = 4


class SteeringModeOnBrake(IntEnum):
  REMAIN_ACTIVE = 0
  PAUSE = 1
  DISENGAGE = 2


# Brands that don't require ACC main button for lateral
NO_ACC_MAIN_BUTTON_BRANDS = ("rivian", "tesla")

# Events that block ALCC but allow paused state instead of full disable
PAUSE_ALLOWED_EVENTS = [
  EventName.wrongGear, EventName.reverseGear, EventName.brakeHold,
  EventName.doorOpen, EventName.seatbeltNotLatched, EventName.parkBrake
]

SOFT_DISABLE_TIME = 3.0  # seconds


@dataclass
class AlccStatus:
  state: AlccState = AlccState.disabled
  enabled: bool = False
  active: bool = False
  available: bool = False
  hold_at_standstill: bool = False
  lateral_mismatch_counter: int = 0


class AlccController:
  """Enhanced ALCC with MADS-derived state machine and safety checks."""

  def __init__(self, CP: car.CarParams):
    self.CP = CP
    self.params = Params()
    self.enabled_toggle = self.params.get_bool("EOPLatALCC")
    self.always_allow = self.params.get_bool("EOPALCCAllowAlways")
    self.hold_at_standstill = self.params.get_bool("EOPALCCHoldAtStandstill")
    self.unified_engagement = self.params.get_bool("EOPALCCUnifiedEngagement")

    # Steering mode on brake
    try:
      self.steering_mode_on_brake = SteeringModeOnBrake(
        int(self.params.get("EOPALCCSteeringModeOnBrake") or 1)
      )
    except (ValueError, TypeError):
      self.steering_mode_on_brake = SteeringModeOnBrake.PAUSE

    # Per-brand overrides
    self.allow_always = False
    self.no_main_cruise = False
    if CP.brand == "tesla":
      self.allow_always = True
    if CP.brand in NO_ACC_MAIN_BUTTON_BRANDS:
      self.no_main_cruise = True

    # State machine
    self.state = AlccState.disabled
    self.enabled = False
    self.active = False
    self.available = False
    self.soft_disable_timer = 0.0
    self.lateral_mismatch_counter = 0
    self._enabled_prev = False
    self._last_param_t: float = 0.0

  PARAM_REFRESH_S = 1.0

  def read_params(self):
    now = time.monotonic()
    if now - self._last_param_t < self.PARAM_REFRESH_S:
      return
    self._last_param_t = now
    self.enabled_toggle = self.params.get_bool("EOPLatALCC")
    self.always_allow = self.params.get_bool("EOPALCCAllowAlways")
    self.hold_at_standstill = self.params.get_bool("EOPALCCHoldAtStandstill")
    self.unified_engagement = self.params.get_bool("EOPALCCUnifiedEngagement")
    try:
      self.steering_mode_on_brake = SteeringModeOnBrake(
        int(self.params.get("EOPALCCSteeringModeOnBrake") or 1)
      )
    except (ValueError, TypeError):
      self.steering_mode_on_brake = SteeringModeOnBrake.PAUSE

  def _has_event(self, events, event_name: int) -> bool:
    return event_name in events.names

  def _remove_event(self, events, event_name: int):
    if event_name in events.names:
      events.names.remove(event_name)

  def pedal_pressed_non_gas(self, CS: car.CarState, CS_prev: car.CarState, disengage_on_accelerator: bool) -> bool:
    if CS.brakePressed:
      return True
    if disengage_on_accelerator and CS.gasPressed and not CS_prev.gasPressed:
      return True
    return False

  def update_events(self, CS: car.CarState, CS_prev: car.CarState,
                    events, enabled: bool, enabled_prev: bool,
                    disengage_on_accelerator: bool):
    if not self.enabled_toggle:
      return

    # When stock is not engaged but ALCC is on, replace disabling events with pause
    if not enabled and self.enabled:
      if CS.standstill:
        for evt in [EventName.doorOpen, EventName.seatbeltNotLatched]:
          if self._has_event(events, evt):
            self._remove_event(events, evt)
            self.state = AlccState.paused
        for evt in [EventName.wrongGear, EventName.reverseGear, EventName.brakeHold, EventName.parkBrake]:
          if self._has_event(events, evt):
            self._remove_event(events, evt)
            self.state = AlccState.paused
      if self.steering_mode_on_brake == SteeringModeOnBrake.PAUSE:
        if self.pedal_pressed_non_gas(CS, CS_prev, disengage_on_accelerator):
          self.state = AlccState.paused
      # Remove pre-enable events that block ALCC engagement
      for evt in [EventName.preEnableStandstill, EventName.belowEngageSpeed, EventName.speedTooLow,
                  EventName.cruiseDisabled, EventName.manualRestart]:
        self._remove_event(events, evt)

    # Brake mode: disengage on non-gas pedal press
    if self.steering_mode_on_brake == SteeringModeOnBrake.DISENGAGE:
      if self.pedal_pressed_non_gas(CS, CS_prev, disengage_on_accelerator):
        self.state = AlccState.disabled

    # Resume from paused if conditions clear
    if self.state == AlccState.paused:
      if not self.pedal_pressed_non_gas(CS, CS_prev, disengage_on_accelerator):
        blocked = any(self._has_event(events, e) for e in PAUSE_ALLOWED_EVENTS)
        if not blocked:
          self.state = AlccState.enabled

    # Remove events that shouldn't affect lateral-only mode
    for evt in [EventName.pcmDisable, EventName.buttonCancel, EventName.pedalPressed, EventName.wrongCruiseMode]:
      self._remove_event(events, evt)

  def update_state_machine(self, events, steer_override: bool):
    """Update ALCC state machine."""
    if not self.enabled_toggle:
      self.state = AlccState.disabled
      return

    has_user_disable = events.contains(ET.USER_DISABLE)
    has_imm_disable = events.contains(ET.IMMEDIATE_DISABLE)
    has_soft_disable = events.contains(ET.SOFT_DISABLE)
    has_enable = EventName.lkasEnable in events.names or EventName.buttonEnable in events.names

    # Decrement soft-disable timer
    self.soft_disable_timer = max(0.0, self.soft_disable_timer - DT_CTRL)

    if self.state != AlccState.disabled:
      if has_user_disable or has_imm_disable:
        self.state = AlccState.disabled
      elif has_soft_disable:
        if self.state == AlccState.enabled:
          self.state = AlccState.softDisabling
          self.soft_disable_timer = SOFT_DISABLE_TIME
        elif self.state == AlccState.softDisabling:
          if self.soft_disable_timer <= 0:
            self.state = AlccState.disabled
      elif steer_override:
        if self.state == AlccState.enabled:
          self.state = AlccState.overriding
      else:
        if self.state == AlccState.overriding:
          self.state = AlccState.enabled
        elif self.state == AlccState.softDisabling and not has_soft_disable:
          self.state = AlccState.enabled
    else:
      if has_enable:
        self.state = AlccState.enabled

    self.enabled = self.state in (AlccState.paused, AlccState.enabled, AlccState.softDisabling, AlccState.overriding)
    self.active = self.state in (AlccState.enabled, AlccState.softDisabling, AlccState.overriding)

  def check_lateral_mismatch(self, panda_states, stock_active: bool):
    """Count frames where Panda disallows lateral but we want it active."""
    IGNORED_SAFETY = ('silent', 'noOutput')
    if not self.active or stock_active:
      self.lateral_mismatch_counter = 0
      return
    for ps in panda_states:
      if ps.safetyModel not in IGNORED_SAFETY and not getattr(ps, 'controlsAllowedLateral', True):
        self.lateral_mismatch_counter += 1
        return
    self.lateral_mismatch_counter = 0

  def update(self, CS: car.CarState, CS_prev: car.CarState,
             events, panda_states,
             stock_enabled: bool, stock_active: bool,
             calibrated: bool, gear_ok: bool, safety_ok: bool,
             disengage_on_accelerator: bool) -> AlccStatus:
    """Main ALCC update loop. Call every control frame."""
    self.read_params()

    # Availability
    self.available = self.enabled_toggle and calibrated and gear_ok and safety_ok

    # Update events
    self.update_events(CS, CS_prev, events, stock_enabled, self._enabled_prev, disengage_on_accelerator)

    # Check Panda lateral mismatch
    self.check_lateral_mismatch(panda_states, stock_active)
    if self.lateral_mismatch_counter >= 200:
      events.add(EventName.controlsMismatchLateral)

    # Determine if ALCC should be steering
    steer_override = abs(CS.steeringTorque) > 1.0

    # Update state machine
    self.update_state_machine(events, steer_override)

    self._enabled_prev = stock_enabled

    return AlccStatus(
      state=self.state,
      enabled=self.enabled,
      active=self.active,
      available=self.available,
      hold_at_standstill=self.hold_at_standstill,
      lateral_mismatch_counter=self.lateral_mismatch_counter,
    )

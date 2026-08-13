#!/usr/bin/env python3
"""Vehicle events for Tesla-format protocol.

Replaces car_specific.py with Tesla-format protocol event handling.
"""
from cereal import car, log
from openpilot.selfdrive.selfdrived.events import Events

EventName = log.OnroadEvent.EventName
ButtonType = car.CarState.ButtonEvent.Type
GearShifter = car.CarState.GearShifter

# Constants
DT_CTRL = 0.01  # 100Hz control loop
MAX_CTRL_SPEED = 135.0  # m/s (~486 km/h)


class VehicleEvents:
  """Vehicle event handler."""

  def __init__(self, CP: car.CarParams):
    self.CP = CP

    self.steering_unpressed = 0
    self.low_speed_alert = False
    self.no_steer_warning = False
    self.silent_steer_warning = True

  def update(self, CS: car.CarState, CS_prev: car.CarState, CC: car.CarControl):
    """Update and return vehicle events."""
    events = self._create_common_events(CS, CS_prev, CC)

    # Protocol-specific events
    if self.CP.openpilotLongitudinalControl:
      if CS.cruiseState.standstill and not CS.brakePressed:
        events.add(EventName.resumeRequired)
      if CS.vEgo < self.CP.minEnableSpeed:
        events.add(EventName.belowEngageSpeed)
        if CC.actuators.accel > 0.3:
          events.add(EventName.speedTooLow)
      if CS.vEgo < 0.001:
        events.add(EventName.manualRestart)

    return events

  def _create_common_events(
    self,
    CS: car.CarState,
    CS_prev: car.CarState,
    CC: car.CarControl,
    extra_gears=None,
    pcm_enable=True,
    allow_button_cancel=True
  ) -> Events:
    """Create common vehicle events."""
    events = Events()

    # Safety checks
    if CS.doorOpen:
      events.add(EventName.doorOpen)
    if CS.seatbeltUnlatched:
      events.add(EventName.seatbeltNotLatched)

    # Gear checks
    if CS.gearShifter != GearShifter.drive:
      if extra_gears is None or CS.gearShifter not in extra_gears:
        events.add(EventName.wrongGear)
    if CS.gearShifter == GearShifter.reverse:
      events.add(EventName.reverseGear)

    # Cruise state
    if not CS.cruiseState.available:
      events.add(EventName.wrongCarMode)

    # ESP/ABS
    if CS.espDisabled:
      events.add(EventName.espDisabled)

    # Stock systems
    if CS.stockFcw:
      events.add(EventName.stockFcw)
    if CS.stockAeb:
      events.add(EventName.stockAeb)

    # Speed
    if CS.vEgo > MAX_CTRL_SPEED:
      events.add(EventName.speedTooHigh)
    if CS.cruiseState.nonAdaptive:
      events.add(EventName.wrongCruiseMode)

    # Brakes
    if CS.brakeHoldActive and self.CP.openpilotLongitudinalControl:
      events.add(EventName.brakeHold)
    if CS.parkingBrake:
      events.add(EventName.parkBrake)

    # ACC faults
    if CS.accFaulted:
      events.add(EventName.accFaulted)

    # Steering
    if CS.steeringPressed:
      events.add(EventName.steerOverride)

    # Pre-enable
    if CS.brakePressed and CS.standstill:
      events.add(EventName.preEnableStandstill)
    if CS.gasPressed:
      events.add(EventName.gasPressedOverride)

    # Sensors
    if CS.invalidLkasSetting:
      events.add(EventName.invalidLkasSetting)
    if CS.vEgo < self.CP.minSteerSpeed:
      events.add(EventName.belowSteerSpeed)

    # Button events
    for b in CS.buttonEvents:
      if b.type == ButtonType.cancel and (allow_button_cancel or not self.CP.pcmCruise):
        events.add(EventName.buttonCancel)

    # Steering faults
    self.steering_unpressed = 0 if CS.steeringPressed else self.steering_unpressed + 1

    if CS.steerFaultTemporary:
      if CS.steeringPressed and (not CS_prev.steerFaultTemporary or self.no_steer_warning):
        self.no_steer_warning = True
      else:
        self.no_steer_warning = False

      if self.silent_steer_warning or CS.standstill or self.steering_unpressed < int(1.5 / DT_CTRL):
        self.silent_steer_warning = True
        events.add(EventName.steerTempUnavailableSilent)
      else:
        events.add(EventName.steerTempUnavailable)
    else:
      self.no_steer_warning = False
      self.silent_steer_warning = False

    if CS.steerFaultPermanent:
      events.add(EventName.steerUnavailable)

    # PCM enable/disable
    if pcm_enable:
      if CS.cruiseState.enabled and not CS_prev.cruiseState.enabled:
        events.add(EventName.pcmEnable)
      elif not CS.cruiseState.enabled:
        events.add(EventName.pcmDisable)

    return events

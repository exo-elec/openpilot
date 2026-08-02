import numpy as np

from opendbc.can import CANPacker
from opendbc.car import Bus, structs
from opendbc.car.lateral import apply_std_steer_angle_limits
from opendbc.car.interfaces import CarControllerBase
from opendbc.car.byd.bydcan import create_acc_cmd, create_lkas_hud, create_steering_control
from opendbc.car.byd.values import CarControllerParams

LongCtrlState = structs.CarControl.Actuators.LongControlState


class CarController(CarControllerBase):
  """0x1E2 steering + 0x316 HUD + (when openpilotLongitudinalControl) 0x32E accel.

  0x1E2 is byte-verified against the tc275_freertos/TC275_BrownPanda firmware's
  real WriteRaw sequence and against opendbc/safety/modes/byd.h's byd_tx_hook
  (opendbc/car/byd/tests/test_byd.py, opendbc/safety/tests/test_byd.py).

  0x316 and 0x32E are ported from shemps/byd-atto3-openpilot-port's later
  CarrotPilot-derived revision (see opendbc/car/byd/bydcan.py's module
  docstring) - real on-car behavior observed on that fork's own vehicle, not
  validated against this project's target car. 0x316 passes every stock field
  through from CS.lkas_hud untouched except the specific bits that fork's
  captures proved safe to override.

  Not wired to a live vehicle: opendbc/car/byd/interface.py still selects
  SafetyModel.noOutput and openpilotLongitudinalControl=False, so panda never
  installs byd_hooks, the 0x32E branch never runs, and nothing here reaches a
  CAN bus.

  Separately, opendbc/safety/modes/byd.h's BYD_TX_MSGS whitelist does not yet
  include 0x32E at all - only 0x1E2/0x316. Even if openpilotLongitudinalControl
  were set, a real panda would reject every create_acc_cmd frame at the
  generic TX_MSGS check, before byd_tx_hook's own logic ever runs (a safe,
  fail-closed gap, not a silent-transmit one). Adding 0x32E to BYD_TX_MSGS and
  writing its own byd_tx_hook validation (accel bounds, checksum, standstill/
  resume state checks) is required before this branch could ever go live -
  not just flipping openpilotLongitudinalControl. See MIGRATION_PLAN.md task 4.
  """

  def __init__(self, dbc_names, CP):
    super().__init__(dbc_names, CP)
    self.packer = CANPacker(dbc_names[Bus.pt])
    self.apply_angle_last = 0.
    self.accel_last = 0.

  def update(self, CC, CS, now_nanos):
    del now_nanos
    actuators = CC.actuators
    can_sends = []
    accel = 0.

    if (CC.enabled or CC.latActive) and (self.frame % CarControllerParams.STEER_STEP == 0):
      self.apply_angle_last = apply_std_steer_angle_limits(
        actuators.steeringAngleDeg, self.apply_angle_last, CS.out.vEgoRaw, CS.out.steeringAngleDeg,
        CC.latActive, CarControllerParams.ANGLE_LIMITS)

      cntr = (self.frame // CarControllerParams.STEER_STEP) % 16
      can_sends.append(create_steering_control(self.packer, self.apply_angle_last, CC.latActive, cntr))
      can_sends.append(create_lkas_hud(self.packer, CC.latActive, cntr, CS.lkas_hud))

    if self.CP.openpilotLongitudinalControl and CC.longActive and (self.frame % 3 == 0):
      target = float(np.clip(actuators.accel, CarControllerParams.ACCEL_MIN, CarControllerParams.ACCEL_MAX))
      lcs = actuators.longControlState
      stopping = (lcs == LongCtrlState.stopping) or (CS.out.standstill and target <= 0.)
      resume = (lcs == LongCtrlState.starting) or CC.cruiseControl.resume

      if resume and CS.out.standstill and self.accel_last < 0.:
        self.accel_last = 0.

      launch = CS.out.vEgo < 2.0 and target > 0.
      up = (CarControllerParams.JERK_UP_LAUNCH if launch else CarControllerParams.JERK_UP) * 0.03
      down = CarControllerParams.JERK_DOWN * 0.03
      accel = float(np.clip(target, self.accel_last - down, self.accel_last + up))

      cntr = (self.frame // 3) % 16
      can_sends.append(create_acc_cmd(self.packer, accel, True, cntr,
                                      standstill=stopping and CS.out.standstill, resume=resume))
      self.accel_last = accel
    elif self.frame % 3 == 0:
      self.accel_last = float(np.clip(CS.out.aEgo, CarControllerParams.ACCEL_MIN, CarControllerParams.ACCEL_MAX))

    new_actuators = actuators.as_builder()
    new_actuators.steeringAngleDeg = self.apply_angle_last
    new_actuators.accel = accel

    self.frame += 1
    return new_actuators, can_sends

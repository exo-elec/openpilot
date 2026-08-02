from opendbc.can import CANPacker
from opendbc.car import Bus
from opendbc.car.lateral import apply_std_steer_angle_limits
from opendbc.car.interfaces import CarControllerBase
from opendbc.car.byd.bydcan import create_steering_control
from opendbc.car.byd.values import CarControllerParams


class CarController(CarControllerBase):
  """0x1E2 steering-command generation only.

  Byte-verified against the tc275_freertos/TC275_BrownPanda firmware's real
  WriteRaw sequence (opendbc/car/byd/tests/test_byd.py) and against
  opendbc/safety/modes/byd.h's byd_tx_hook (opendbc/safety/tests/test_byd.py).
  Not wired to a live vehicle: opendbc/car/byd/interface.py still selects
  SafetyModel.noOutput, so panda never installs byd_hooks and nothing here
  reaches a CAN bus. HUD (0x316) generation is not implemented -
  opendbc/safety/modes/byd.h statically blocks camera->car forwarding of both
  0x1E2 and 0x316 once the byd safety model is active, so a controller must
  emit a substitute 0x316 too, and that requires a target-car capture to
  verify AUTO_LIGHT/HMA_ON_OFF/LDSW_TYPE and an unexplained bit-35 overlap
  with MPC_RightLaneState first (see nagaspilot/docs/MIGRATION_PLAN.md).
  """

  def __init__(self, dbc_names, CP):
    super().__init__(dbc_names, CP)
    self.packer = CANPacker(dbc_names[Bus.pt])
    self.apply_angle_last = 0
    self.counter = 0

  def update(self, CC, CS, now_nanos):
    del now_nanos
    actuators = CC.actuators
    can_sends = []

    if CC.enabled and (self.frame % CarControllerParams.STEER_STEP == 0):
      self.apply_angle_last = apply_std_steer_angle_limits(
        actuators.steeringAngleDeg, self.apply_angle_last, CS.out.vEgoRaw, CS.out.steeringAngleDeg,
        CC.latActive, CarControllerParams.ANGLE_LIMITS)

      can_sends.append(create_steering_control(self.packer, self.apply_angle_last, CC.latActive, self.counter))
      self.counter = (self.counter + 1) % 16

    new_actuators = actuators.as_builder()
    new_actuators.steeringAngleDeg = self.apply_angle_last

    self.frame += 1
    return new_actuators, can_sends

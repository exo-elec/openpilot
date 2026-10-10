import numpy as np
from cereal import car
from openpilot.system.socketd.vehicle.protocol.packer import CANPacker
from openpilot.system.socketd.vehicle.protocol import Bus
from openpilot.system.socketd.vehicle.protocol.lateral import apply_steer_angle_limits_vm, AngleSteeringLimitsVM
from openpilot.system.socketd.vehicle.protocol.teslacan import TeslaCAN
from openpilot.system.socketd.vehicle.tesla.values import CarControllerParams
from openpilot.system.socketd.vehicle.protocol.vehicle_model import VehicleModel
CarControllerParams.ANGLE_LIMITS = AngleSteeringLimitsVM(360, MAX_ANGLE_RATE=5)


def get_safety_CP():
  # We use the TESLA_MODEL_Y platform for lateral limiting to match safety
  # A Model 3 at 40 m/s using the Model Y limits sees a <0.3% difference in max angle (from curvature factor)
  CP = car.CarParams.new_message()
  for name, value in dict(mass=2208.0, wheelbase=2.890000104904175, steerRatio=12.0,
                          centerToFront=1.4450000524520874, steerRatioRear=0.0,
                          rotationalInertia=4325.73486328125, tireStiffnessFront=241830.375,
                          tireStiffnessRear=382284.53125).items():
    setattr(CP, name, value)
  return CP



class CarController:
  def __init__(self, CP):
    self.CP = CP
    self.frame = 0
    dbc_names = {Bus.party: "tesla_model3_party"}
    self.apply_angle_last = 0
    self.packer = CANPacker(dbc_names[Bus.party])
    self.tesla_can = TeslaCAN(CP, self.packer)

    # Vehicle model used for lateral limiting
    self.VM = VehicleModel(get_safety_CP())

  def update(self, CC, CS, now_nanos):
    actuators = CC.actuators
    can_sends = []

    # Tesla EPS enforces disabling steering on heavy lateral override force.
    # When enabling in a tight curve, we wait until user reduces steering force to start steering.
    # Canceling is done on rising edge and is handled generically with CC.cruiseControl.cancel
    lat_active = CC.latActive and CS.hands_on_level < 3

    if self.frame % 2 == 0:
      # Angular rate limit based on speed
      self.apply_angle_last = apply_steer_angle_limits_vm(actuators.steeringAngleDeg, self.apply_angle_last, CS.out.vEgoRaw, CS.out.steeringAngleDeg,
                                                          lat_active, CarControllerParams, self.VM)

      can_sends.append(self.tesla_can.create_steering_control(self.apply_angle_last, lat_active))

    if self.frame % 10 == 0:
      can_sends.append(self.tesla_can.create_steering_allowed())

    # Longitudinal control
    if self.CP.openpilotLongitudinalControl:
      if self.frame % 4 == 0:
        state = 13 if CC.cruiseControl.cancel else 4  # 4=ACC_ON, 13=ACC_CANCEL_GENERIC_SILENT
        accel = float(np.clip(actuators.accel, CarControllerParams.ACCEL_MIN, CarControllerParams.ACCEL_MAX))
        # controlsd clips all non-AEB control to ACCEL_MIN_COMFORT. A request
        # below that boundary is therefore the schema-free, safety-checked AEB
        # transition; the existing Tesla DAS_aebEvent bit carries it onward.
        aeb_active = CC.longActive and accel < CarControllerParams.ACCEL_MIN_COMFORT
        cntr = (self.frame // 4) % 8
        can_sends.append(self.tesla_can.create_longitudinal_command(state, accel, cntr, CS.out.vEgo, CC.longActive, aeb_active))

    else:
      # Increment counter so cancel is prioritized even without openpilot longitudinal
      if CC.cruiseControl.cancel:
        cntr = (CS.das_control["DAS_controlCounter"] + 1) % 8
        can_sends.append(self.tesla_can.create_longitudinal_command(13, 0, cntr, CS.out.vEgo, False, False))

    # TODO: HUD control
    new_actuators = actuators.as_builder()
    new_actuators.steeringAngleDeg = self.apply_angle_last

    self.frame += 1
    return new_actuators, can_sends

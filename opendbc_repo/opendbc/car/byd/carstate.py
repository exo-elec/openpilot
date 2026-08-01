"""BYD Atto 3 vehicle-state decoding.

Adapted from shemps/byd-atto3-openpilot-port commit
5b34194240bb831719629d2fd095fae5daaed1e0. Proof-of-concept engagement,
CarrotPilot cruise plumbing, longitudinal control, and UI repurposing are
intentionally omitted.
"""

import copy

from opendbc.can.parser import CANParser
from opendbc.car import Bus, structs
from opendbc.car.common.conversions import Conversions as CV
from opendbc.car.interfaces import CarStateBase
from opendbc.car.byd.values import CarControllerParams, DBC

GearShifter = structs.CarState.GearShifter

GEAR_MAP = {
  1: GearShifter.park,
  2: GearShifter.reverse,
  3: GearShifter.neutral,
  4: GearShifter.drive,
}


class CarState(CarStateBase):
  def __init__(self, CP):
    super().__init__(CP)
    self.lkas_hud = {}

  def update(self, can_parsers) -> structs.CarState:
    cp = can_parsers[Bus.pt]
    cp_cam = can_parsers[Bus.cam]
    ret = structs.CarState()

    speed_kph = cp.vl["WHEELSPEED_CLEAN"]["WHEELSPEED_CLEAN"]
    ret.vEgoRaw = speed_kph * CV.KPH_TO_MS
    ret.vEgo, ret.aEgo = self.update_speed_kf(ret.vEgoRaw)
    ret.vEgoCluster = ret.vEgo
    ret.standstill = speed_kph < 0.1

    ret.steeringAngleDeg = cp.vl["STEER_MODULE_2"]["STEER_ANGLE_2"]
    ret.steeringTorque = cp.vl["STEERING_TORQUE"]["MAIN_TORQUE"]
    ret.steeringTorqueEps = cp.vl["STEER_MODULE_2"]["DRIVER_EPS_TORQUE"]
    driver_override = abs(ret.steeringTorqueEps) > CarControllerParams.STEER_DRIVER_OVERRIDE
    ret.steeringPressed = self.update_steering_pressed(driver_override, 5)

    lks_prepared = bool(cp.vl["STEERING_TORQUE"]["LKS_PREPARED"])
    lks_activated = bool(cp.vl["STEERING_TORQUE"]["CRUISE_ACTIVATED"])
    eps_stream_dropout = lks_prepared and lks_activated
    camera_fault = int(cp_cam.vl["LKAS_HUD_ADAS"]["LKAS_STATE"]) == 4
    ret.steerFaultTemporary = camera_fault or eps_stream_dropout

    ret.gasPressed = cp.vl["PEDAL"]["GAS_PEDAL"] > 0.10
    ret.brake = cp.vl["PEDAL"]["BRAKE_PEDAL"]
    ret.brakePressed = bool(cp.vl["DRIVE_STATE"]["BRAKE_PRESSED"])
    ret.gearShifter = GEAR_MAP.get(int(cp.vl["DRIVE_STATE"]["GEAR"]), GearShifter.unknown)

    ret.leftBlinker = bool(cp.vl["STALKS"]["LEFT_BLINKER"])
    ret.rightBlinker = bool(cp.vl["STALKS"]["RIGHT_BLINKER"])
    ret.leftBlindspot = cp.vl["BSD_RADAR"]["LEFT_APPROACH"] != 0
    ret.rightBlindspot = cp.vl["BSD_RADAR"]["RIGHT_APPROACH"] != 0

    ret.doorOpen = any((
      cp.vl["METER_CLUSTER"]["FRONT_LEFT_DOOR"],
      cp.vl["METER_CLUSTER"]["FRONT_RIGHT_DOOR"],
      cp.vl["METER_CLUSTER"]["BACK_LEFT_DOOR"],
      cp.vl["METER_CLUSTER"]["BACK_RIGHT_DOOR"],
    ))
    ret.seatbeltUnlatched = not bool(cp.vl["METER_CLUSTER"]["SEATBELT_DRIVER"])

    acc_state = int(cp_cam.vl["ACC_HUD_ADAS"]["ACC_STATE"])
    ret.cruiseState.speed = cp_cam.vl["ACC_HUD_ADAS"]["SET_SPEED"] * CV.KPH_TO_MS
    ret.cruiseState.available = acc_state in (2, 3, 5)
    ret.cruiseState.enabled = acc_state in (3, 5)
    ret.cruiseState.standstill = bool(cp_cam.vl["ACC_CMD"]["STANDSTILL_STATE"])

    # DragonPilot ALKA uses this as the stock lateral-availability state.
    self.lkas_on = ret.cruiseState.available
    self.lkas_hud = copy.copy(cp_cam.vl["LKAS_HUD_ADAS"])

    return ret

  @staticmethod
  def get_can_parsers(CP):
    return {
      Bus.pt: CANParser(DBC[CP.carFingerprint][Bus.pt], [], 0),
      Bus.cam: CANParser(DBC[CP.carFingerprint][Bus.pt], [], 2),
    }

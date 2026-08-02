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

    speed_kph = cp.vl["B_0x1F0_VCU_ESP_VehSpeed_L8_20ms"]["ESP_VehicleSpeed"]
    ret.vEgoRaw = speed_kph * CV.KPH_TO_MS
    ret.vEgo, ret.aEgo = self.update_speed_kf(ret.vEgoRaw)
    ret.vEgoCluster = ret.vEgo
    ret.standstill = speed_kph < 0.1

    ret.steeringAngleDeg = cp.vl["B_0x11F_SAS_SensorState_L5_10ms"]["SAS_SteeringAngle"]
    ret.steeringTorque = cp.vl["B_0x1FC_EPS_MotorState_L8_20ms"]["EPS_MainTorque"]
    ret.steeringTorqueEps = cp.vl["B_0x11F_SAS_SensorState_L5_10ms"]["EPS_DriverTorque"]
    driver_override = abs(ret.steeringTorqueEps) > CarControllerParams.STEER_DRIVER_OVERRIDE
    ret.steeringPressed = self.update_steering_pressed(driver_override, 5)

    lks_prepared = bool(cp.vl["B_0x1FC_EPS_MotorState_L8_20ms"]["EPS_LkasPrepared"])
    lks_activated = bool(cp.vl["B_0x1FC_EPS_MotorState_L8_20ms"]["EPS_CruiseActivated"])
    eps_stream_dropout = lks_prepared and lks_activated
    camera_fault = int(cp_cam.vl["A_0x316_MPC_MpcState_L8_20ms"]["MPC_LkasState"]) == 4
    ret.steerFaultTemporary = camera_fault or eps_stream_dropout

    # VCU_AccelPedalRaw/VCU_BrakePedalPressureCount are raw counts (0-255), matching
    # tc275_freertos DBC/byd_atto3.c case 0x342; 10 raw is the tested gas-pressed
    # threshold (also used raw in opendbc/safety/modes/byd.h).
    ret.gasPressed = cp.vl["B_0x342_VCU_PedalState_L8_20ms"]["VCU_AccelPedalRaw"] > 10
    ret.brake = cp.vl["B_0x342_VCU_PedalState_L8_20ms"]["VCU_BrakePedalPressureCount"] * 0.01
    ret.brakePressed = bool(cp.vl["B_0x242_VCU_DriveState_L8_20ms"]["VCU_BrakePressed"])
    ret.gearShifter = GEAR_MAP.get(int(cp.vl["B_0x242_VCU_DriveState_L8_20ms"]["VCU_Gear"]), GearShifter.unknown)

    # BCM_TurnSignalStalk is a single 3-bit enum (tc275_freertos DBC/byd_atto3.c case
    # 0x133): only 1=LEFT and 2=RIGHT are firmware-validated. Reading one field rather
    # than two independent bit-flags means a reversed stalk push (e.g. right while left
    # is active) already cancels the opposite side for free, since the field can only
    # ever hold one value at a time. Values other than 0/1/2 (e.g. a light lane-change
    # tap that doesn't reach a full detent) are not yet captured against a real car;
    # treat them as OFF until verified.
    ret.leftBlinker = cp.vl["B_0x133_BCM_StalkState_L8_50ms"]["BCM_TurnSignalStalk"] == 1
    ret.rightBlinker = cp.vl["B_0x133_BCM_StalkState_L8_50ms"]["BCM_TurnSignalStalk"] == 2
    ret.leftBlindspot = cp.vl["B_0x418_VCU_BsdState_L8_50ms"]["VCU_BSDLeftApproach"] != 0
    ret.rightBlindspot = cp.vl["B_0x418_VCU_BsdState_L8_50ms"]["VCU_BSDRightApproach"] != 0

    ret.doorOpen = any((
      cp.vl["B_0x294_BCM_CabinState_L8_50ms"]["BCM_DoorFrontLeft"],
      cp.vl["B_0x294_BCM_CabinState_L8_50ms"]["BCM_DoorFrontRight"],
      cp.vl["B_0x294_BCM_CabinState_L8_50ms"]["BCM_DoorRearLeft"],
      cp.vl["B_0x294_BCM_CabinState_L8_50ms"]["BCM_DoorRearRight"],
    ))
    ret.seatbeltUnlatched = not bool(cp.vl["B_0x294_BCM_CabinState_L8_50ms"]["BCM_DriverSeatbelt"])

    acc_state = int(cp_cam.vl["B_0x32D_HUD_AdasState_L8_20ms"]["VCU_ACCState"])
    ret.cruiseState.speed = cp_cam.vl["B_0x32D_HUD_AdasState_L8_20ms"]["VCU_ACCSetSpeed"] * CV.KPH_TO_MS
    ret.cruiseState.available = acc_state in (2, 3, 5)
    ret.cruiseState.enabled = acc_state in (3, 5)
    ret.cruiseState.standstill = bool(cp_cam.vl["A_0x32E_MPC_Long_Cmd_L8_20ms"]["MPC_StandstillState"])

    # DragonPilot ALKA uses this as the stock lateral-availability state.
    self.lkas_on = ret.cruiseState.available
    self.lkas_hud = copy.copy(cp_cam.vl["A_0x316_MPC_MpcState_L8_20ms"])

    return ret

  @staticmethod
  def get_can_parsers(CP):
    return {
      Bus.pt: CANParser(DBC[CP.carFingerprint][Bus.pt], [], 0),
      Bus.cam: CANParser(DBC[CP.carFingerprint][Bus.pt], [], 2),
    }

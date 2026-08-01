"""Passive BYD Atto 3 interface for parser and recording validation."""

from opendbc.car import get_safety_config, structs
from opendbc.car.byd.carcontroller import CarController
from opendbc.car.byd.carstate import CarState
from opendbc.car.interfaces import CarInterfaceBase


class CarInterface(CarInterfaceBase):
  CarState = CarState
  CarController = CarController

  @staticmethod
  def _get_params(ret: structs.CarParams, candidate, fingerprint, car_fw,
                  alpha_long, is_release, dp_params, docs) -> structs.CarParams:
    del candidate, fingerprint, car_fw, alpha_long, is_release, dp_params, docs
    ret.brand = "byd"
    ret.safetyConfigs = [get_safety_config(structs.CarParams.SafetyModel.noOutput)]
    ret.dashcamOnly = True

    ret.steerControlType = structs.CarParams.SteerControlType.angle
    ret.steerActuatorDelay = 0.2
    ret.steerLimitTimer = 0.4
    ret.steerAtStandstill = True

    ret.radarUnavailable = True
    ret.alphaLongitudinalAvailable = False
    ret.openpilotLongitudinalControl = False
    ret.pcmCruise = True
    return ret

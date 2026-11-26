"""BYD Atto 3 interface: lateral control live, longitudinal still gated.

Flipped from dashcamOnly/SafetyModel.noOutput to SafetyModel.byd on explicit
request (2026-08-12) - opendbc/safety/modes/byd.h's steering enforcement
(steer_angle_cmd_checks_vm + zone LUT backstop) was already audited complete
for 0x1E2/0x316 (see docs/upstream-audit/NODE_02_byd_panda_safety.md and
NODE_06_edp10_net_new.md on dev/EOP10). Longitudinal (0x32E) stays off:
byd.h's BYD_TX_MSGS whitelist still only carries BYD_MPC_LATERAL_CMD and
BYD_MPC_STATE, so openpilotLongitudinalControl would fail closed at panda's
generic TX check rather than transmit - flip that separately, once 0x32E is
added to the whitelist and reviewed with the same rigor as the lateral path.
This has not been driven on a real Atto 3; treat the steering constants
(max_angle, zone LUTs, slip_factor) exactly as their own comments already
flag them - some are route-driven evidence, some are uncited placeholders.
"""

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
    ret.safetyConfigs = [get_safety_config(structs.CarParams.SafetyModel.byd)]
    ret.dashcamOnly = False

    ret.steerControlType = structs.CarParams.SteerControlType.angle
    ret.steerActuatorDelay = 0.2
    ret.steerLimitTimer = 0.4
    ret.steerAtStandstill = True

    ret.radarUnavailable = True
    ret.alphaLongitudinalAvailable = False
    ret.openpilotLongitudinalControl = False
    ret.pcmCruise = True
    return ret

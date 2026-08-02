from dataclasses import dataclass, field
from enum import StrEnum

from opendbc.car import Bus, CarSpecs, DbcDict, PlatformConfig, Platforms, structs
from opendbc.car.docs_definitions import CarDocs, CarHarness, CarParts
from opendbc.car.fw_query_definitions import FwQueryConfig, Request, StdQueries
from opendbc.car.lateral import AngleSteeringLimits

Ecu = structs.CarParams.Ecu


class CarControllerParams:
  STEER_DRIVER_OVERRIDE = 10
  STEER_STEP = 2  # 50 Hz, matches byd.h's AngleSteeringLimits.frequency = 50U

  # Mirrors opendbc/safety/modes/byd.h's BYD_STEERING_LIMITS exactly; any
  # divergence here is caught by test_byd.py's cross-check against byd_tx_hook.
  # Breakpoints are nagaspilot/docs/SPEED_ZONE_POLICY.md's CITY_SPEED_MPS (12)
  # and HIGHWAY_SPEED_MPS (24) - hardcoded rather than imported, since
  # opendbc_repo has no dependency on nagaspilot/ (and that doc's cited
  # canonical source, nagaspilot/speed_zones.py, does not currently exist in
  # this tree). Rates are a provisional design (higher/looser at low speed
  # for city maneuvering, tighter at highway speed), not target-car evidence.
  ANGLE_LIMITS: AngleSteeringLimits = AngleSteeringLimits(
    390,  # deg, matches BYD_STEERING_LIMITS.max_angle / angle_deg_to_can
    ([0., 12., 24.], [4., 2., .5]),
    ([0., 12., 24.], [4., 3., 1.5]),
  )


class WMI(StrEnum):
  BYD_AUTO = "LGX"


class ModelYear(StrEnum):
  N_2022 = "N"
  P_2023 = "P"
  R_2024 = "R"
  S_2025 = "S"


@dataclass
class BydCarDocs(CarDocs):
  package: str = "All"
  car_parts: CarParts = field(default_factory=CarParts.common([CarHarness.custom]))


@dataclass
class BydPlatformConfig(PlatformConfig):
  dbc_dict: DbcDict = field(default_factory=lambda: {Bus.pt: "byd_atto3"})
  wmis: set[WMI] = field(default_factory=set)
  years: set[ModelYear] = field(default_factory=set)


class CAR(Platforms):
  BYD_ATTO_3 = BydPlatformConfig(
    [BydCarDocs("BYD Atto 3 2024")],
    CarSpecs(mass=1750, wheelbase=2.72, steerRatio=19.8),
    wmis={WMI.BYD_AUTO},
    years={ModelYear.N_2022, ModelYear.P_2023, ModelYear.R_2024, ModelYear.S_2025},
  )


FW_QUERY_CONFIG = FwQueryConfig(
  requests=[
    Request(
      [StdQueries.SUPPLIER_SOFTWARE_VERSION_REQUEST],
      [StdQueries.SUPPLIER_SOFTWARE_VERSION_RESPONSE],
      bus=0,
    ),
  ],
  non_essential_ecus={Ecu.fwdCamera: [CAR.BYD_ATTO_3]},
)


DBC = CAR.create_dbc_map()

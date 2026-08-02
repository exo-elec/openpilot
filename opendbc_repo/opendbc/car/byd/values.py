from dataclasses import dataclass, field
from enum import StrEnum

from opendbc.car import Bus, CarSpecs, DbcDict, PlatformConfig, Platforms, structs
from opendbc.car.docs_definitions import CarDocs, CarHarness, CarParts
from opendbc.car.fw_query_definitions import FwQueryConfig, Request, StdQueries
from opendbc.car.lateral import AngleSteeringLimits, ISO_LATERAL_ACCEL

Ecu = structs.CarParams.Ecu


class CarControllerParams:
  STEER_DRIVER_OVERRIDE = 10
  STEER_STEP = 2  # 50 Hz, matches byd.h's AngleSteeringLimits.frequency = 50U
  AVERAGE_ROAD_ROLL = 0.06
  MAX_LATERAL_ACCEL = ISO_LATERAL_ACCEL + (9.81 * AVERAGE_ROAD_ROLL)
  MAX_LATERAL_JERK = 3.0 + (9.81 * AVERAGE_ROAD_ROLL)
  MAX_ANGLE_RATE = 4.0

  # Mirrors opendbc/safety/modes/byd.h's BYD_STEERING_LIMITS exactly; any
  # divergence here is caught by test_byd.py's cross-check against byd_tx_hook.
  # STEER_ANGLE_MAX and the MAX_LATERAL_*/MAX_ANGLE_RATE kwargs are what
  # apply_steer_angle_limits_vm() actually reads (continuous ISO 11270
  # accel/jerk limit, same formula as opendbc/safety/lateral.h and byd.h's
  # steer_angle_cmd_checks_vm). The two breakpoint-list positional args
  # below are NOT consumed by the vm path - they're kept only to document
  # NagasPilot's CRAWL(0)/WALK(2)/CITY(6)/URBAN(12)/HIGHWAY(24) m/s policy
  # ranges from nagaspilot/docs/SPEED_ZONE_POLICY.md and
  # nagaspilot/speed_zones.py (hardcoded rather than imported, since
  # opendbc_repo has no dependency on nagaspilot/). The slip factor in
  # byd.h's BYD_STEERING_PARAMS is a provisional design, not target-car
  # evidence.
  ANGLE_LIMITS: AngleSteeringLimits = AngleSteeringLimits(
    120,  # deg, matches the TC275/TC375 physical gateway limit
    ([0., 2., 6., 12., 24.], [4., 4., 3., 2., .5]),
    ([0., 2., 6., 12., 24.], [4., 4., 3.5, 3., 1.5]),
    MAX_LATERAL_ACCEL=MAX_LATERAL_ACCEL,
    MAX_LATERAL_JERK=MAX_LATERAL_JERK,
    MAX_ANGLE_RATE=MAX_ANGLE_RATE,
  )

  # Longitudinal (0x32E trial). Comfort envelope, well inside the safety cap
  # (byd.h enforces -3.5..+2.0 on 0x32E per BYD_ATTO3_COMMA3_PORT_PLAN.md).
  # Ported from shemps/byd-atto3-openpilot-port's CarrotPilot-derived revision
  # (see bydcan.py's module docstring); unvalidated against this project's
  # target car. Only reachable when openpilotLongitudinalControl is set, which
  # interface.py currently never does.
  ACCEL_MIN = -3.0  # m/s^2
  ACCEL_MAX = 1.5   # m/s^2
  JERK_UP = 2.5         # accel increasing (m/s^3)
  JERK_UP_LAUNCH = 4.0  # pull-away only (vEgo < 2, cmd > 0)
  JERK_DOWN = 5.0       # accel decreasing (m/s^3)


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

"""
Telemetry Module for obd2d

Vehicle telemetry collection dataclasses.
Uses camelCase field names to match OpenPilot cereal / capnp convention.
"""

import time
from dataclasses import dataclass, asdict


@dataclass
class VehicleTelemetry:
    """Complete vehicle telemetry data.

    Field names use camelCase to match OpenPilot cereal/capnp convention.
    """

    # Core vehicle state
    vEgo: float | None = None
    aEgo: float | None = None
    steeringAngleDeg: float | None = None
    steeringRateDeg: float | None = None
    steeringTorque: float | None = None
    gearShifter: str | None = None
    leftBlinker: bool | None = None
    rightBlinker: bool | None = None

    # Position (from GPS)
    lat: float | None = None
    lon: float | None = None
    heading: float | None = None
    gpsAccuracy: float | None = None

    # ICE specific
    engineRpm: float | None = None
    engineTemp: int | None = None
    throttlePos: float | None = None
    engineLoad: float | None = None
    mafRate: float | None = None
    fuelLevel: float | None = None
    fuelRate: float | None = None
    odometer: int | None = None

    # EV specific
    batterySoc: float | None = None
    batterySoh: float | None = None
    batteryVoltage: float | None = None
    batteryCurrent: float | None = None
    batteryTempMin: int | None = None
    batteryTempMax: int | None = None
    batteryPower: float | None = None
    chargingStatus: str | None = None
    chargingPower: float | None = None
    rangeRemaining: int | None = None
    rangeAt100: int | None = None
    energyConsumption: float | None = None

    # Motor/Inverter (EV)
    motorRpm: float | None = None
    motorTemp: int | None = None
    inverterTemp: int | None = None

    # 12V system
    auxBatteryVoltage: float | None = None

    # Metadata
    vehicleType: str = "unknown"
    vin: str | None = None
    timestamp: int = 0

    def __post_init__(self):
        if self.timestamp == 0:
            self.timestamp = int(time.monotonic() * 1000)

    def to_ncp_json(self) -> dict:
        """Convert to NCP telemetry payload.

        Uses camelCase keys matching OpenPilot cereal and VisionPilot dataclass fields.
        """
        data = {
            "ncpVersion": "4.0.0",
            "msgType": "VehicleTelemetry",
            "vehicleType": self.vehicleType,
            "timestamp": self.timestamp,
        }

        # Add all non-None fields
        for key, value in asdict(self).items():
            if value is not None and key not in ("timestamp",):
                data[key] = value

        return data


@dataclass
class VehicleInfo:
    """Vehicle identification info."""
    vin: str
    vehicleType: str
    make: str
    model: str | None = None
    year: int | None = None
    fuelType: str | None = None
    supportedPids: list[str | None] = None

    def to_ncp_json(self) -> dict:
        return {
            "ncpVersion": "4.0.0",
            "msgType": "VehicleInfo",
            "vin": self.vin,
            "vehicleType": self.vehicleType,
            "make": self.make,
            **({"model": self.model} if self.model else {}),
            **({"year": self.year} if self.year else {}),
            **({"fuelType": self.fuelType} if self.fuelType else {}),
            **({"supportedPids": self.supportedPids} if self.supportedPids else {}),
        }

"""
OBD Daemon for OpenPilot.

Provides OBD2/UDS diagnostics via SocketCAN with ELM327 protocol interface.
Communicates with bluetoothd via Cereal messaging.

Services:
- OBD2 Mode 01 (Current Data) - PIDs 0x00-0xA6
- OBD2 Mode 09 (Vehicle Info) - VIN, etc.
- OBD2 Mode 22 (Manufacturer Specific) - Chinese EVs
- ISO-TP (ISO 15765-2) multi-frame support
- UDS (ISO 14229) via python-udsoncan
"""

from openpilot.selfdrive.obd2d.obd2d import OBD2D, main
from openpilot.selfdrive.obd2d.vehicle_db import VehicleType, VEHICLE_PIDS
from openpilot.selfdrive.obd2d.telemetry import VehicleTelemetry, VehicleInfo

# UDS adapter (optional, requires submodules)
try:
    from openpilot.selfdrive.obd2d.adapters.udsoncan_adapter import (
        UDSVehicleAdapter, UDSConfig
    )
    __all__ = ['OBD2D', 'main', 'VehicleType', 'VEHICLE_PIDS',
               'VehicleTelemetry', 'VehicleInfo',
               'UDSVehicleAdapter', 'UDSConfig']
except ImportError:
    __all__ = ['OBD2D', 'main', 'VehicleType', 'VEHICLE_PIDS',
               'VehicleTelemetry', 'VehicleInfo']

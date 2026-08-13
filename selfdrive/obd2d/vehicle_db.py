#!/usr/bin/env python3
"""vehicle_db.py - Vehicle PID Database and Detection

Supports: Generic ICE, Generic EV, Chinese EVs (BYD, MG, GAC, CHANGAN, GWM, GEELY, CHERY)
Mode 22 (Manufacturer Specific) PIDs for battery telemetry

Naming follows OpenPilot cereal convention: camelCase field names
"""

from enum import Enum


class VehicleType(Enum):
    """Supported vehicle types."""
    # Generic
    GENERIC_ICE = "generic_ice"
    GENERIC_EV = "generic_ev"
    GENERIC_PHEV = "generic_phev"  # Plug-in Hybrid

    # Chinese EVs
    BYD = "byd"
    MG = "mg"
    GAC = "gac"
    CHANGAN = "changan"
    GWM = "gwm"
    GEELY = "geely"
    CHERY = "chery"


# Mode 22 PID Database for all vehicle types
# Format: PID_HEX -> (name, unit, scale, offset)
# Names use camelCase to match OpenPilot cereal / capnp field naming convention
VEHICLE_PIDS: dict[VehicleType, dict[str, tuple]] = {
    # =========================================================================
    # Generic ICE (Standard OBD2 PIDs - Mode 01)
    # =========================================================================
    VehicleType.GENERIC_ICE: {
        # Engine
        '010C': ('engineRpm', 'rpm', 0.25, 0),
        '010D': ('vehicleSpeed', 'km/h', 1, 0),
        '0105': ('coolantTemp', '°C', 1, -40),
        '0104': ('engineLoad', '%', 100 / 255, 0),
        '0111': ('throttlePos', '%', 100 / 255, 0),
        # Fuel
        '012F': ('fuelLevel', '%', 100 / 255, 0),
        '015E': ('fuelRate', 'L/h', 0.05, 0),
        # Intake
        '010B': ('map', 'kPa', 1, 0),
        '010F': ('intakeTemp', '°C', 1, -40),
        '0110': ('mafRate', 'g/s', 0.01, 0),
        # Timing
        '010E': ('timingAdvance', '°', 0.5, -64),
        # O2 Sensors
        '0114': ('o2Sensor1', 'V', 0.005, 0),
        # Distance
        '0121': ('distanceMil', 'km', 1, 0),
        '0131': ('distanceDtcClear', 'km', 1, 0),
        # Runtime
        '011F': ('runtime', 's', 1, 0),
        # Ambient
        '0146': ('ambTemp', '°C', 1, -40),
        # Oil
        '015C': ('oilTemp', '°C', 1, -40),
    },

    # =========================================================================
    # Generic EV (Standard + Common EV PIDs)
    # =========================================================================
    VehicleType.GENERIC_EV: {
        # Battery (Mode 01 standard)
        '015B': ('hybridBatteryLife', '%', 100 / 255, 0),
        '015C': ('hybridBatteryTemp', '°C', 1, -40),

        # Common Mode 22 PIDs for many EVs
        '220001': ('batterySoc', '%', 0.5, 0),
        '220002': ('batterySoh', '%', 0.01, 0),
        '220003': ('batteryVoltage', 'V', 0.1, 0),
        '220004': ('batteryCurrent', 'A', 0.1, 0),
        '220005': ('batteryTempMax', '°C', 1, -40),
        '220006': ('batteryTempMin', '°C', 1, -40),
        '220007': ('rangeRemaining', 'km', 1, 0),
        '220008': ('chargingStatus', '', 1, 0),
        '220009': ('motorRpm', 'rpm', 1, 0),
        '22000A': ('motorTemp', '°C', 1, -40),
        '22000B': ('inverterTemp', '°C', 1, -40),
        '22000C': ('auxBatteryVoltage', 'V', 0.1, 0),
    },

    # =========================================================================
    # Generic PHEV (Plug-in Hybrid) - Has both ICE and EV systems
    # =========================================================================
    VehicleType.GENERIC_PHEV: {
        # ICE PIDs (from GENERIC_ICE)
        '010C': ('engineRpm', 'rpm', 0.25, 0),
        '010D': ('vehicleSpeed', 'km/h', 1, 0),
        '0105': ('coolantTemp', '°C', 1, -40),
        '0104': ('engineLoad', '%', 100 / 255, 0),
        '0111': ('throttlePos', '%', 100 / 255, 0),
        '015C': ('engineTemp', '°C', 1, -40),
        '012F': ('fuelLevel', '%', 100 / 255, 0),
        '0151': ('fuelType', '', 1, 0),

        # EV PIDs (battery for hybrid system)
        '015B': ('hybridBatteryLife', '%', 100 / 255, 0),
        '0142': ('batteryVoltage', 'V', 0.001, 0),
        '0143': ('batteryCurrent', 'A', 0.001, 0),
        '0144': ('batteryTemp', '°C', 1, -40),

        # Common Mode 22 PIDs for PHEVs
        '220001': ('batterySoc', '%', 0.5, 0),
        '220002': ('batterySoh', '%', 0.01, 0),
        '220007': ('rangeRemaining', 'km', 1, 0),
        '220008': ('chargingStatus', '', 1, 0),
        '220009': ('motorRpm', 'rpm', 1, 0),
        '22000A': ('motorTemp', '°C', 1, -40),
        '22000D': ('evModeActive', '', 1, 0),  # 0=ICE, 1=EV, 2=Hybrid
    },

    # =========================================================================
    # Chinese EVs
    # =========================================================================
    VehicleType.BYD: {
        # Battery
        '221FFC': ('batterySoc', '%', 0.01, 0),
        '221FFD': ('batterySoh', '%', 0.01, 0),
        '221FFE': ('batteryVoltage', 'V', 0.1, 0),
        '221FFF': ('batteryCurrent', 'A', 0.1, 0),
        '222000': ('batteryTempMax', '°C', 1, -40),
        '222001': ('batteryTempMin', '°C', 1, -40),
        '222002': ('batteryPower', 'kW', 0.001, 0),
        # Cell data
        '222003': ('cellVoltageMax', 'mV', 1, 0),
        '222004': ('cellVoltageMin', 'mV', 1, 0),
        '222005': ('cellVoltageDelta', 'mV', 1, 0),
        # Charging
        '222006': ('chargingStatus', '', 1, 0),
        '222007': ('chargingPower', 'kW', 0.1, 0),
        '222008': ('chargingCurrent', 'A', 0.1, 0),
        '222009': ('chargingVoltage', 'V', 0.1, 0),
        # Range
        '22200B': ('rangeRemaining', 'km', 1, 0),
        '22200D': ('energyConsumption', 'kWh/100km', 0.1, 0),
        # Motor
        '22200E': ('motorRpm', 'rpm', 1, 0),
        '22200F': ('motorTemp', '°C', 1, -40),
        '222012': ('inverterTemp', '°C', 1, -40),
        # 12V
        '222013': ('auxBatteryVoltage', 'V', 0.1, 0),
    },

    VehicleType.MG: {
        '220501': ('batterySoc', '%', 0.5, 0),
        '220502': ('batterySoh', '%', 0.01, 0),
        '220503': ('batteryVoltage', 'V', 0.1, 0),
        '220504': ('batteryCurrent', 'A', 0.1, 0),
        '220505': ('batteryTempMax', '°C', 1, -40),
        '220506': ('batteryTempMin', '°C', 1, -40),
        '220507': ('batteryPower', 'kW', 0.01, 0),
        '220508': ('cellVoltageMax', 'mV', 1, 0),
        '220509': ('cellVoltageMin', 'mV', 1, 0),
        '22050A': ('chargingStatus', '', 1, 0),
        '22050B': ('chargingPower', 'kW', 0.1, 0),
        '22050D': ('rangeRemaining', 'km', 1, 0),
        '22050E': ('energyConsumption', 'Wh/km', 1, 0),
        '22050F': ('motorRpm', 'rpm', 1, 0),
        '220510': ('motorTemp', '°C', 1, -40),
        '220511': ('inverterTemp', '°C', 1, -40),
        '220512': ('auxBatteryVoltage', 'V', 0.1, 0),
        '220513': ('odometer', 'km', 1, 0),
    },

    VehicleType.GAC: {
        '22A001': ('batterySoc', '%', 0.01, 0),
        '22A002': ('batterySoh', '%', 0.01, 0),
        '22A003': ('batteryVoltage', 'V', 0.1, 0),
        '22A004': ('batteryCurrent', 'A', 0.1, 0),
        '22A005': ('batteryTempMax', '°C', 1, -40),
        '22A006': ('batteryTempMin', '°C', 1, -40),
        '22A007': ('chargingStatus', '', 1, 0),
        '22A008': ('chargingPower', 'kW', 0.1, 0),
        '22A009': ('rangeRemaining', 'km', 1, 0),
        '22A00A': ('energyConsumption', 'kWh/100km', 0.1, 0),
        '22A00B': ('motorRpm', 'rpm', 1, 0),
        '22A00C': ('motorTemp', '°C', 1, -40),
        '22A00D': ('inverterTemp', '°C', 1, -40),
        '22A00E': ('auxBatteryVoltage', 'V', 0.1, 0),
    },

    VehicleType.CHANGAN: {
        '22C101': ('batterySoc', '%', 0.01, 0),
        '22C102': ('batteryVoltage', 'V', 0.1, 0),
        '22C103': ('batteryCurrent', 'A', 0.1, 0),
        '22C104': ('batteryTemp', '°C', 1, -40),
        '22C105': ('chargingStatus', '', 1, 0),
        '22C106': ('rangeRemaining', 'km', 1, 0),
        '22C107': ('motorTemp', '°C', 1, -40),
        '22C108': ('inverterTemp', '°C', 1, -40),
    },

    VehicleType.GWM: {
        '22D801': ('batterySoc', '%', 0.01, 0),
        '22D802': ('batterySoh', '%', 0.01, 0),
        '22D803': ('batteryVoltage', 'V', 0.1, 0),
        '22D804': ('batteryCurrent', 'A', 0.1, 0),
        '22D805': ('batteryTempMax', '°C', 1, -40),
        '22D806': ('batteryTempMin', '°C', 1, -40),
        '22D807': ('chargingStatus', '', 1, 0),
        '22D808': ('chargingPower', 'kW', 0.1, 0),
        '22D809': ('rangeRemaining', 'km', 1, 0),
        '22D80A': ('hevMode', '', 1, 0),
        '22D80B': ('fuelLevel', '%', 0.4, 0),
        '22D80C': ('engineRpm', 'rpm', 1, 0),
        '22D80D': ('motorRpm', 'rpm', 1, 0),
        '22D80E': ('motorTemp', '°C', 1, -40),
        '22D80F': ('inverterTemp', '°C', 1, -40),
        '22D810': ('auxBatteryVoltage', 'V', 0.1, 0),
    },

    VehicleType.GEELY: {
        '22E001': ('batterySoc', '%', 0.01, 0),
        '22E002': ('batterySoh', '%', 0.01, 0),
        '22E003': ('batteryVoltage', 'V', 0.1, 0),
        '22E004': ('batteryCurrent', 'A', 0.1, 0),
        '22E005': ('batteryTempMax', '°C', 1, -40),
        '22E006': ('batteryTempMin', '°C', 1, -40),
        '22E007': ('chargingStatus', '', 1, 0),
        '22E008': ('chargingPower', 'kW', 0.1, 0),
        '22E009': ('rangeRemaining', 'km', 1, 0),
        '22E00A': ('energyConsumption', 'kWh/100km', 0.1, 0),
        '22E00B': ('motorRpm', 'rpm', 1, 0),
        '22E00C': ('motorTemp', '°C', 1, -40),
        '22E00D': ('inverterTemp', '°C', 1, -40),
        '22E00E': ('auxBatteryVoltage', 'V', 0.1, 0),
    },

    VehicleType.CHERY: {
        '22F001': ('batterySoc', '%', 0.01, 0),
        '22F002': ('batterySoh', '%', 0.01, 0),
        '22F003': ('batteryVoltage', 'V', 0.1, 0),
        '22F004': ('batteryCurrent', 'A', 0.1, 0),
        '22F005': ('batteryTemp', '°C', 1, -40),
        '22F006': ('chargingStatus', '', 1, 0),
        '22F007': ('chargingPower', 'kW', 0.1, 0),
        '22F008': ('rangeRemaining', 'km', 1, 0),
        '22F009': ('motorRpm', 'rpm', 1, 0),
        '22F00A': ('motorTemp', '°C', 1, -40),
        '22F00B': ('inverterTemp', '°C', 1, -40),
        '22F00C': ('auxBatteryVoltage', 'V', 0.1, 0),
        '22F00D': ('odometer', 'km', 1, 0),
    },
}


# VIN WMI to vehicle type mapping
VEHICLE_WMI_MAP = {
    'LGX': ('byd', 'BYD'),
    'SGS': ('mg', 'MG'),
    'LWV': ('gac', 'GAC'),
    'LS5': ('changan', 'CHANGAN'),
    'LGW': ('gwm', 'GWM'),
    'LB3': ('geely', 'GEELY'),
    'LVV': ('chery', 'CHERY'),
}


def detect_vehicle_type(vin: str, vehicle_info: dict | None = None) -> VehicleType:
    """Detect vehicle type from VIN or vehicle info.

    Returns:
        VehicleType enum value
    """
    vin_upper = vin.upper() if vin else ""
    make = vehicle_info.get('make', '').upper() if vehicle_info else ""

    # Check for fuel type hint
    fuel_type = vehicle_info.get('fuel_type', '').upper() if vehicle_info else ""
    is_phev = fuel_type == 'PHEV' or 'PLUGIN' in fuel_type or 'PLUG-IN' in fuel_type
    is_ev = fuel_type in ('ELECTRIC', 'BEV', 'EV') or (fuel_type == 'HYBRID' and not is_phev)
    is_ice = fuel_type in ('GASOLINE', 'DIESEL', 'PETROL', 'GAS')

    # Chinese EV Brands (check first for specificity)
    if vin_upper.startswith('LGX') or make == 'BYD':
        return VehicleType.BYD
    elif vin_upper.startswith('SGS') or make in ('MG', 'MORRIS GARAGES', 'SAIC'):
        return VehicleType.MG
    elif make in ('GAC', 'AION', 'HYPER'):
        return VehicleType.GAC
    elif make in ('CHANGAN', 'DEEPAL', 'NEVO'):
        return VehicleType.CHANGAN
    elif make in ('GWM', 'ORA', 'HAVAL', 'WEY', 'TANK', 'GREAT WALL'):
        return VehicleType.GWM
    elif make in ('GEELY', 'ZEEKR', 'LYNKCO', 'LYNK & CO', 'GALAXY', 'GEOMETRY'):
        return VehicleType.GEELY
    elif make in ('CHERY', 'EXEED', 'OMODA', 'JAECOO', 'JETOUR'):
        return VehicleType.CHERY

    # Known EV brands (non-Chinese)
    elif make in ('TESLA', 'NISSAN') and 'LEAF' in (vehicle_info or {}).get('model', '').upper():
        return VehicleType.GENERIC_EV
    elif ('ELECTRIC' in (vehicle_info or {}).get('model', '').upper() or
          'EV' in (vehicle_info or {}).get('model', '').upper() or
          'BEV' in (vehicle_info or {}).get('model', '').upper()):
        return VehicleType.GENERIC_EV

    # Generic by fuel type
    elif is_phev:
        return VehicleType.GENERIC_PHEV
    elif is_ev:
        return VehicleType.GENERIC_EV
    elif is_ice:
        return VehicleType.GENERIC_ICE

    # Default to generic ICE if VIN starts with common WMI codes for ICE vehicles
    elif vin_upper.startswith(('1', '4', '5')):
        return VehicleType.GENERIC_ICE
    elif vin_upper.startswith(('W', 'S', 'Z')):
        return VehicleType.GENERIC_ICE
    elif vin_upper.startswith('J'):
        return VehicleType.GENERIC_ICE

    # Default fallback
    return VehicleType.GENERIC_ICE


def is_chinese_ev(vehicle_type: str) -> bool:
    """Check if vehicle uses Mode 22 PIDs."""
    return vehicle_type in {
        VehicleType.BYD.value,
        VehicleType.MG.value,
        VehicleType.GAC.value,
        VehicleType.CHANGAN.value,
        VehicleType.GWM.value,
        VehicleType.GEELY.value,
        VehicleType.CHERY.value,
    }


def decode_mode22_response(pid: str, data: bytes, brand: VehicleType) -> dict | None:
    """Decode Mode 22 (manufacturer specific) response."""
    pid_defs = VEHICLE_PIDS.get(brand, {})

    if pid not in pid_defs:
        return None

    name, unit, scale, offset = pid_defs[pid]

    if len(data) < 1:
        return None

    # Decode based on data length
    if len(data) == 1:
        raw = data[0]
    elif len(data) == 2:
        raw = (data[0] << 8) + data[1]
    elif len(data) == 4:
        raw = (data[0] << 24) + (data[1] << 16) + (data[2] << 8) + data[3]
    else:
        raw = data[0]

    # Handle signed values (for current, power)
    if name in ('batteryCurrent', 'batteryPower') and len(data) == 2:
        if raw > 32767:
            raw = raw - 65536

    value = raw * scale + offset

    return {
        'name': name,
        'value': value,
        'unit': unit,
        'raw': raw,
    }


def get_essential_pids(brand: VehicleType) -> list[str]:
    """Get essential PID names for polling."""
    return [
        'batterySoc',
        'batteryVoltage',
        'batteryCurrent',
        'batteryTempMax',
        'batteryTempMin',
        'rangeRemaining',
    ]


def pid_to_hex(pid_name: str, brand: VehicleType) -> str | None:
    """Convert camelCase PID name to hex string."""
    pid_defs = VEHICLE_PIDS.get(brand, {})

    for hex_pid, (name, _, _, _) in pid_defs.items():
        if name == pid_name:
            return hex_pid

    return None


def get_pid_name_map(brand: VehicleType) -> dict[str, str]:
    """Get mapping of camelCase PID name -> hex string for telemetry queries.

    Returns:
        dict mapping human-readable PID name (e.g. 'batterySoc') to
        hex string (e.g. '221FFC') for the given vehicle brand.
    """
    pid_defs = VEHICLE_PIDS.get(brand, {})
    return {
        name: hex_pid
        for hex_pid, (name, _, _, _) in pid_defs.items()
    }


def decode_pid_by_name(name: str, data: bytes, brand: VehicleType) -> float | None:
    """Decode raw PID data using the vehicle database definition.

    Args:
        name: camelCase PID name (e.g. 'batterySoc')
        data: Raw response bytes (after stripping mode/PID header bytes)
        brand: Vehicle type for scale/offset lookup

    Returns:
        Decoded float value or None if unable to decode
    """
    pid_defs = VEHICLE_PIDS.get(brand, {})

    # Find the PID definition by name
    pid_def = None
    for hex_pid, (pid_name, unit, scale, offset) in pid_defs.items():
        if pid_name == name:
            pid_def = (hex_pid, unit, scale, offset)
            break

    if pid_def is None or len(data) < 1:
        return None

    _, _, scale, offset = pid_def

    # Decode based on data length
    if len(data) == 1:
        raw = data[0]
    elif len(data) == 2:
        raw = (data[0] << 8) + data[1]
    elif len(data) == 4:
        raw = (data[0] << 24) + (data[1] << 16) + (data[2] << 8) + data[3]
    else:
        raw = data[0]

    # Handle signed values
    if name in ('batteryCurrent', 'batteryPower') and len(data) == 2:
        if raw > 32767:
            raw = raw - 65536

    return raw * scale + offset

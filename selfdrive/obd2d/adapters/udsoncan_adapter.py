"""
UDSonCAN adapter for ExoPilot obd2d.

Wraps python-udsoncan and python-can-isotp for reliable UDS/ISO-TP communication
with support for standard OBD2 and Chinese EV Mode 22 PIDs.
"""

from __future__ import annotations

import sys
import os
from typing import Any
from dataclasses import dataclass

# Add third_party to path for imports
_third_party = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(__file__)))), 'third_party')
sys.path.insert(0, os.path.join(_third_party, 'python-can-isotp'))
sys.path.insert(0, os.path.join(_third_party, 'python-udsoncan'))

try:
    import udsoncan
    from udsoncan.client import Client
    from udsoncan.connections import IsoTPSocketConnection
    from udsoncan.services import DiagnosticSessionControl, ReadDTCInformation
    from udsoncan.exceptions import NegativeResponseException, InvalidResponseException
    import isotp
except ImportError as e:
    raise ImportError(f"UDS libraries not found. Ensure submodules are initialized: {e}")

from openpilot.selfdrive.obd2d.vehicle_db import VehicleType, VEHICLE_PIDS


@dataclass
class UDSConfig:
    """Configuration for UDS connection."""
    can_interface: str = 'can0'
    tx_addr: int = 0x7E0
    rx_addr: int = 0x7E8
    timeout: float = 5.0
    p2_timeout: float = 5.0


class BatterySOHCodec(udsoncan.DidCodec):
    """Codec for battery State of Health (SOH) decoding."""
    
    def __init__(self, scale: float = 0.01, offset: float = 0.0):
        self.scale = scale
        self.offset = offset
    
    def decode(self, data: bytes) -> float:
        if len(data) < 1:
            return 0.0
        raw = int.from_bytes(data[:2], 'big') if len(data) >= 2 else data[0]
        return raw * self.scale + self.offset
    
    def encode(self, value: float) -> bytes:
        raw = int((value - self.offset) / self.scale)
        return raw.to_bytes(2, 'big')


class BatteryVoltageCodec(udsoncan.DidCodec):
    """Codec for battery voltage decoding."""
    
    def __init__(self, scale: float = 0.1):
        self.scale = scale
    
    def decode(self, data: bytes) -> float:
        if len(data) < 1:
            return 0.0
        raw = int.from_bytes(data[:2], 'big') if len(data) >= 2 else data[0]
        return raw * self.scale
    
    def encode(self, value: float) -> bytes:
        raw = int(value / self.scale)
        return raw.to_bytes(2, 'big')


class BatteryCurrentCodec(udsoncan.DidCodec):
    """Codec for battery current decoding (signed)."""
    
    def __init__(self, scale: float = 0.1):
        self.scale = scale
    
    def decode(self, data: bytes) -> float:
        if len(data) < 2:
            return 0.0
        raw = int.from_bytes(data[:2], 'big', signed=True)
        return raw * self.scale
    
    def encode(self, value: float) -> bytes:
        raw = int(value / self.scale)
        return raw.to_bytes(2, 'big', signed=True)


class IntCodec(udsoncan.DidCodec):
    """Codec for integer values (replacement for udsoncan.codec.IntCodec)."""

    def __init__(self, length: int = 1, signed: bool = False):
        self.length = length
        self.signed = signed

    def decode(self, data: bytes) -> int:
        return int.from_bytes(data[:self.length], 'big', signed=self.signed)

    def encode(self, value: int) -> bytes:
        return value.to_bytes(self.length, 'big', signed=self.signed)


class TemperatureCodec(udsoncan.DidCodec):
    """Codec for temperature decoding with offset."""
    
    def __init__(self, scale: float = 1.0, offset: float = -40.0):
        self.scale = scale
        self.offset = offset
    
    def decode(self, data: bytes) -> float:
        if len(data) < 1:
            return 0.0
        raw = data[0]
        return raw * self.scale + self.offset
    
    def encode(self, value: float) -> bytes:
        raw = int((value - self.offset) / self.scale)
        return bytes([raw & 0xFF])


class UDSVehicleAdapter:
    """
    UDS adapter for vehicle communication.
    
    Supports:
    - Standard OBD2 PIDs (Mode 01/09)
    - Mode 22 Manufacturer Specific (Chinese EVs: BYD, MG, GAC, etc.)
    - DTC reading/clearing
    - VIN reading
    """
    
    # Mode 22 PID to Data Identifier mapping for Chinese EVs
    MODE22_DIDS = {
        'byd': {
            0x221FFC: ('batterySoc', BatterySOHCodec(0.01)),
            0x221FFD: ('batterySoh', BatterySOHCodec(0.01)),
            0x221FFE: ('batteryVoltage', BatteryVoltageCodec(0.1)),
            0x221FFF: ('batteryCurrent', BatteryCurrentCodec(0.1)),
            0x222000: ('batteryTempMax', TemperatureCodec(1.0, -40)),
            0x222001: ('batteryTempMin', TemperatureCodec(1.0, -40)),
            0x222002: ('batteryPower', BatteryCurrentCodec(0.001)),
            0x222006: ('chargingStatus', IntCodec(1)),
            0x222007: ('chargingPower', BatteryVoltageCodec(0.1)),
            0x22200B: ('rangeRemaining', IntCodec(1)),
            0x22200E: ('motorRpm', IntCodec(1)),
            0x22200F: ('motorTemp', TemperatureCodec(1.0, -40)),
            0x222012: ('inverterTemp', TemperatureCodec(1.0, -40)),
            0x222013: ('auxBatteryVoltage', BatteryVoltageCodec(0.1)),
        },
        'mg': {
            0x220501: ('batterySoc', BatterySOHCodec(0.5)),
            0x220502: ('batterySoh', BatterySOHCodec(0.01)),
            0x220503: ('batteryVoltage', BatteryVoltageCodec(0.1)),
            0x220504: ('batteryCurrent', BatteryCurrentCodec(0.1)),
            0x220505: ('batteryTempMax', TemperatureCodec(1.0, -40)),
            0x220506: ('batteryTempMin', TemperatureCodec(1.0, -40)),
            0x22050A: ('chargingStatus', IntCodec(1)),
            0x22050D: ('rangeRemaining', IntCodec(1)),
            0x22050F: ('motorRpm', IntCodec(1)),
            0x220510: ('motorTemp', TemperatureCodec(1.0, -40)),
            0x220511: ('inverterTemp', TemperatureCodec(1.0, -40)),
            0x220512: ('auxBatteryVoltage', BatteryVoltageCodec(0.1)),
        },
        'gac': {
            0x22A001: ('batterySoc', BatterySOHCodec(0.01)),
            0x22A002: ('batterySoh', BatterySOHCodec(0.01)),
            0x22A003: ('batteryVoltage', BatteryVoltageCodec(0.1)),
            0x22A004: ('batteryCurrent', BatteryCurrentCodec(0.1)),
            0x22A005: ('batteryTempMax', TemperatureCodec(1.0, -40)),
            0x22A007: ('chargingStatus', IntCodec(1)),
            0x22A009: ('rangeRemaining', IntCodec(1)),
            0x22A00A: ('motorRpm', IntCodec(1)),
            0x22A00B: ('motorTemp', TemperatureCodec(1.0, -40)),
            0x22A00C: ('inverterTemp', TemperatureCodec(1.0, -40)),
        },
        'changan': {
            0x22C101: ('batterySoc', BatterySOHCodec(0.01)),
            0x22C102: ('batteryVoltage', BatteryVoltageCodec(0.1)),
            0x22C103: ('batteryCurrent', BatteryCurrentCodec(0.1)),
            0x22C104: ('batteryTemp', TemperatureCodec(1.0, -40)),
            0x22C105: ('chargingStatus', IntCodec(1)),
            0x22C106: ('rangeRemaining', IntCodec(1)),
            0x22C107: ('motorTemp', TemperatureCodec(1.0, -40)),
            0x22C108: ('inverterTemp', TemperatureCodec(1.0, -40)),
        },
        'gwm': {
            0x22D801: ('batterySoc', BatterySOHCodec(0.01)),
            0x22D802: ('batterySoh', BatterySOHCodec(0.01)),
            0x22D803: ('batteryVoltage', BatteryVoltageCodec(0.1)),
            0x22D804: ('batteryCurrent', BatteryCurrentCodec(0.1)),
            0x22D805: ('batteryTempMax', TemperatureCodec(1.0, -40)),
            0x22D806: ('batteryTempMin', TemperatureCodec(1.0, -40)),
            0x22D807: ('chargingStatus', IntCodec(1)),
            0x22D808: ('chargingPower', BatteryVoltageCodec(0.1)),
            0x22D809: ('rangeRemaining', IntCodec(1)),
            0x22D80D: ('motorRpm', IntCodec(1)),
            0x22D80E: ('motorTemp', TemperatureCodec(1.0, -40)),
            0x22D80F: ('inverterTemp', TemperatureCodec(1.0, -40)),
            0x22D810: ('auxBatteryVoltage', BatteryVoltageCodec(0.1)),
        },
        'geely': {
            0x22E001: ('batterySoc', BatterySOHCodec(0.01)),
            0x22E002: ('batterySoh', BatterySOHCodec(0.01)),
            0x22E003: ('batteryVoltage', BatteryVoltageCodec(0.1)),
            0x22E004: ('batteryCurrent', BatteryCurrentCodec(0.1)),
            0x22E005: ('batteryTempMax', TemperatureCodec(1.0, -40)),
            0x22E007: ('chargingStatus', IntCodec(1)),
            0x22E008: ('chargingPower', BatteryVoltageCodec(0.1)),
            0x22E009: ('rangeRemaining', IntCodec(1)),
            0x22E00B: ('motorRpm', IntCodec(1)),
            0x22E00C: ('motorTemp', TemperatureCodec(1.0, -40)),
            0x22E00D: ('inverterTemp', TemperatureCodec(1.0, -40)),
        },
        'chery': {
            0x22F001: ('batterySoc', BatterySOHCodec(0.01)),
            0x22F002: ('batterySoh', BatterySOHCodec(0.01)),
            0x22F003: ('batteryVoltage', BatteryVoltageCodec(0.1)),
            0x22F004: ('batteryCurrent', BatteryCurrentCodec(0.1)),
            0x22F005: ('batteryTemp', TemperatureCodec(1.0, -40)),
            0x22F006: ('chargingStatus', IntCodec(1)),
            0x22F007: ('chargingPower', BatteryVoltageCodec(0.1)),
            0x22F008: ('rangeRemaining', IntCodec(1)),
            0x22F009: ('motorRpm', IntCodec(1)),
            0x22F00A: ('motorTemp', TemperatureCodec(1.0, -40)),
            0x22F00B: ('inverterTemp', TemperatureCodec(1.0, -40)),
        },
    }
    
    def __init__(self, config: UDSConfig | None = None):
        self.config = config or UDSConfig()
        self.client: Client | None = None
        self._connected = False
        self._vehicle_type: str | None = None
    
    def connect(self) -> bool:
        """Initialize UDS connection."""
        try:
            # ISO-TP address configuration
            tp_addr = isotp.Address(
                isotp.AddressingMode.Normal_11bits,
                txid=self.config.tx_addr,
                rxid=self.config.rx_addr
            )
            
            # Create ISO-TP connection
            conn = IsoTPSocketConnection(
                self.config.can_interface,
                tp_addr
            )
            
            # UDS client configuration
            uds_config = {
                'p2_timeout': self.config.p2_timeout,
                'request_timeout': self.config.timeout,
                'data_identifiers': self._build_data_identifiers()
            }
            
            self.client = Client(conn, config=uds_config)
            self.client.open()
            self._connected = True
            return True
            
        except Exception as e:
            print(f"UDS connection failed: {e}")
            self._connected = False
            return False
    
    def _build_data_identifiers(self) -> dict[int, Any]:
        """Build data identifier configuration for all vehicle types."""
        dids = {}
        
        # Add Mode 22 DIDs for all Chinese EVs
        for vehicle_type, vehicle_dids in self.MODE22_DIDS.items():
            for did_id, (name, codec) in vehicle_dids.items():
                dids[did_id] = udsoncan.DataIdentifier(did_id, name, codec=codec)
        
        return dids
    
    def set_vehicle_type(self, vehicle_type: str):
        """Set vehicle type for Mode 22 PID mapping."""
        self._vehicle_type = vehicle_type.lower()
    
    def read_vin(self) -> str | None:
        """Read VIN using standard UDS service."""
        if not self._connected or not self.client:
            return None
        
        try:
            response = self.client.read_data_by_identifier(udsoncan.DataIdentifier.VIN)
            return response.service_data.values.get(udsoncan.DataIdentifier.VIN)
        except Exception as e:
            print(f"VIN read failed: {e}")
            return None
    
    def read_mode22_pid(self, pid_hex: str) -> dict[str, Any | None]:
        """
        Read Mode 22 manufacturer specific PID.
        
        Args:
            pid_hex: Hex string of PID (e.g., '221FFC')
            
        Returns:
            Dict with 'name' and 'value' or None
        """
        if not self._connected or not self.client:
            return None
        
        if not self._vehicle_type:
            print("Vehicle type not set")
            return None
        
        try:
            did_id = int(pid_hex, 16)
            
            # Check if this PID is supported for this vehicle
            vehicle_dids = self.MODE22_DIDS.get(self._vehicle_type, {})
            if did_id not in vehicle_dids:
                print(f"PID {pid_hex} not supported for {self._vehicle_type}")
                return None
            
            name, _ = vehicle_dids[did_id]
            
            # Read via UDS
            response = self.client.read_data_by_identifier(did_id)
            value = response.service_data.values.get(did_id)
            
            return {'name': name, 'value': value, 'pid': pid_hex}
            
        except Exception as e:
            print(f"Mode 22 read failed: {e}")
            return None
    
    def read_mode01_pid(self, pid: int) -> bytes | None:
        """
        Read Mode 01 current data PID.
        
        Note: Mode 01 is typically broadcast, not UDS. This uses standard OBD2.
        For UDS-based vehicles, this may need adjustment.
        """
        # Mode 01 is usually broadcast CAN, not UDS
        # This is handled separately in obd2d using raw CAN
        return None
    
    def read_dtcs(self) -> list[dict[str, Any]]:
        """Read diagnostic trouble codes."""
        if not self._connected or not self.client:
            return []
        
        try:
            response = self.client.read_dtc_information(
                ReadDTCInformation.ReportType.DTC_BY_STATUS_MASK
            )
            
            dtcs = []
            for dtc in response.service_data.dtcs:
                dtcs.append({
                    'code': f"P{dtc.id:04X}" if dtc.id < 0x4000 else f"U{dtc.id:04X}",
                    'status': dtc.status,
                    'severity': getattr(dtc, 'severity', 0)
                })
            
            return dtcs
            
        except Exception as e:
            print(f"DTC read failed: {e}")
            return []
    
    def clear_dtcs(self) -> bool:
        """Clear diagnostic trouble codes."""
        if not self._connected or not self.client:
            return False
        
        try:
            self.client.clear_diagnostic_information()
            return True
        except Exception as e:
            print(f"DTC clear failed: {e}")
            return False
    
    def change_session(self, session_type: int) -> bool:
        """Change diagnostic session."""
        if not self._connected or not self.client:
            return False
        
        try:
            self.client.change_session(session_type)
            return True
        except Exception as e:
            print(f"Session change failed: {e}")
            return False
    
    def is_connected(self) -> bool:
        """Check if UDS connection is active."""
        return self._connected and self.client is not None
    
    def close(self):
        """Close UDS connection."""
        if self.client:
            try:
                self.client.close()
            except Exception:
                pass
        self._connected = False

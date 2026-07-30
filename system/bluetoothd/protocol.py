#!/usr/bin/env python3
"""NCP (NavPilot Companion Protocol) v4.1.0 — SPP wire format.

Open protocol. Any ELM327-compatible or OBD-II app can connect to the
device over Bluetooth Classic SPP. The device handles BOTH raw ELM327
and NCP structured messages simultaneously on the same socket.

┌─────────────────────────────────────────────────────────────────────────┐
│  SIMULTANEOUS PROTOCOL HANDLING (same SPP socket)                     │
├─────────────────────────────────────────────────────────────────────────┤
│  Raw ELM327   →  ATZ\r, 010C\r, 010D\r                                │
│                →  forwarded to obd2d → car ECU → raw hex response       │
│                →  41 0C 1B 56\r\r>                                     │
│                                                                         │
│  NCP framed   →  [length][type][JSON][CRC]                             │
│                →  parsed as structured command/telemetry                │
│                →  TELEMETRY_VEHICLE, CMD_NAVIGATE, CMD_VEHICLE_DATA    │
├─────────────────────────────────────────────────────────────────────────┤
│  NavPilot can use BOTH on the same connection:                        │
│    • Send 010C\r to read raw RPM directly from obd2d                  │
│    • Send NCP CMD_VEHICLE_DATA to push interpreted data back          │
│    • Send NCP CMD_NAVIGATE to set destination                         │
└─────────────────────────────────────────────────────────────────────────┘

Protocol v4.1 Architecture:
- Phone App (NavPilot, OBD2 Scan, Torque, Car Scanner, etc.) = BLE Client
- OpenPilot/VisionPilot (Device) = BLE Server (Peripheral/Slave)

Control Flow:
1. App INITIATES connection to device
2. obd2d forwards raw OBD bytes TO the app (no interpretation)
3. App INTERPRETS proprietary PIDs (Mode 22, etc.) — subscription value
4. App SENDS structured VehicleData TO device via CMD_VEHICLE_DATA
5. adaptd consumes VehicleData and adapts driving profiles

KEY PRINCIPLE: Clean separation — obd2d does ZERO interpretation.
- obd2d queries raw Mode 01/09/22 PIDs and forwards raw bytes to the app
- App parses proprietary responses (vehicle-specific lookup tables)
- App sends structured NcpVehicleData back to device
- Device never interprets proprietary PIDs directly

Wire format (NCP only — ELM327 is plain ASCII):
    [2 bytes: length] [2 bytes: type] [N bytes: payload] [2 bytes: CRC-CCITT]

    - length   : uint16 big-endian = 2 (type) + len(payload) + 2 (CRC)
    - type     : uint16 big-endian, see MessageType enum
    - payload  : JSON-encoded bytes (UTF-8)
    - CRC-CCITT: binascii.crc_hqx(payload, 0xFFFF), big-endian

Total frame size: 4 (header) + length bytes.
"""
from __future__ import annotations

import binascii
import json
import struct
from dataclasses import dataclass
from enum import IntEnum

PROTOCOL_VERSION = "4.1.0"


class MessageType(IntEnum):
    """Protocol v4.1 message type identifiers.

    Direction:
        PHONE→DEVICE: Commands from companion app to device
        DEVICE→PHONE: Telemetry/Responses from device to app
    
    Ranges:
        0x00–0x0F  Device info
        0x10–0x1F  Telemetry (DEVICE→PHONE)
        0x20–0x2F  Commands  (PHONE→DEVICE)
        0x30–0x3F  Responses (DEVICE→PHONE)
        0x40–0x4F  Control
        0x50–0x5F  Search
        0x60–0x6F  Auth (DEVICE→PHONE)
    """
    # --- Device info ---
    DEVICE_CAPABILITIES = 0x00   # PHONE→DEVICE: Request capabilities
    DEVICE_INFO         = 0x01   # DEVICE→PHONE: Device info response

    # --- Telemetry (DEVICE→PHONE) ---
    TELEMETRY_VEHICLE   = 0x10   # Vehicle telemetry (speed, steering, gear, OBD data)
    TELEMETRY_NAV       = 0x11   # Navigation maneuver instruction
    TELEMETRY_ADAS      = 0x12   # ADAS state
    TELEMETRY_ALERT     = 0x13   # Safety alerts
    TELEMETRY_SYSTEM    = 0x14   # System health
    TELEMETRY_ROUTE     = 0x15   # Route geometry
    TELEMETRY_BATCH     = 0x1F   # Batched telemetry

    # --- Commands (PHONE→DEVICE) — must match frame_protocol.dart exactly ---
    CMD_NAVIGATE         = 0x20   # Start navigation to destination
    CMD_CANCEL_NAV       = 0x21   # Cancel navigation
    CMD_DRIVING_PROFILE  = 0x22   # Set driving profile (aggressive/standard/relaxed/traffic)
    CMD_MISSION_GUIDANCE = 0x23   # Mission guidance (save_energy, arrive_by, charge_needed)
    CMD_GET_VEHICLE_INFO = 0x24   # Request vehicle info (VIN detection)
    CMD_VEHICLE          = 0x25   # Vehicle control
    CMD_SYNC_FAVORITES   = 0x26   # Sync favorites to device
    CMD_TRAFFIC_UPDATE   = 0x27   # Traffic update (premium)
    CMD_VOICE_INTENT     = 0x28   # Voice intent from companion app
    CMD_TTS_REQUEST      = 0x29   # TTS request from device to companion app
    CMD_AUTH_HANDSHAKE   = 0x2A   # Auth/subscription handshake
    CMD_PAIR             = 0x2B   # Pair phone with device (NCP-level code)
    CMD_UNPAIR           = 0x2C   # Unpair phone from device
    CMD_OBD_REQUEST      = 0x2D   # OBD-II PID request (phone → device)
    CMD_VEHICLE_DATA     = 0x2E   # Interpreted OBD telemetry from companion app
    CMD_OAUTH_TOKEN      = 0x2F   # Google OAuth token sync (NavPilot → device for Gemini)

    # --- Responses (DEVICE→PHONE) — must match frame_protocol.dart exactly ---
    RESPONSE_ACK          = 0x30   # Command acknowledgment
    RESPONSE_ERROR        = 0x31   # Error response
    RESPONSE_DATA         = 0x32   # Generic data
    RESPONSE_STATUS       = 0x33   # Status response
    RESPONSE_VEHICLE_INFO = 0x34   # Vehicle info (VIN, type, capabilities)
    RESPONSE_DEVICE_INFO  = 0x35   # Device info
    RESPONSE_PAIR         = 0x36   # Pair accept/reject

    # --- Control ---
    PING = 0x40   # PHONE→DEVICE: Keepalive
    PONG = 0x41   # DEVICE→PHONE: Keepalive response
    
    # --- Search ---
    SEARCH_REQUEST  = 0x50   # PHONE→DEVICE: Search query
    SEARCH_RESPONSE = 0x51   # DEVICE→PHONE: Search results

    # --- Auth (v4.1) ---
    AUTH_DEVICE_ID          = 0x60  # DEVICE→PHONE: Hardware device ID
    AUTH_SUBSCRIPTION_STATE = 0x61  # DEVICE→PHONE: Subscription tier state
    AUTH_TOKEN_VALIDATE     = 0x62  # DEVICE→PHONE: Token validation result
    OAUTH_STATUS            = 0x63  # DEVICE→PHONE: Token status (missing/valid/expiring_soon/expired)


@dataclass(frozen=True)
class Frame:
    """NCP protocol frame.

    Wire format: [2: length][2: type][N: payload][2: CRC-CCITT]
    """
    msg_type: int
    payload: bytes

    HEADER_SIZE = 4   # length(2) + type(2)
    CRC_SIZE    = 2
    MAX_PAYLOAD = 65527  # 65535 - 4 (type+CRC inside length field)

    def encode(self) -> bytes:
        """Encode frame to bytes ready for sendall()."""
        length = 2 + len(self.payload) + self.CRC_SIZE
        header = struct.pack('>HH', length, self.msg_type)
        crc = binascii.crc_hqx(self.payload, 0xFFFF)
        return header + self.payload + struct.pack('>H', crc)

    @classmethod
    def decode(cls, data: bytes) -> tuple[Frame | None, int]:
        """Decode one frame from buffer.

        Returns:
            (frame, bytes_consumed) — frame is None if buffer incomplete.
        """
        if len(data) < cls.HEADER_SIZE:
            return None, 0

        length, msg_type = struct.unpack('>HH', data[:cls.HEADER_SIZE])
        # total = 2 (length field) + length  (length includes type + payload + CRC)
        total = 2 + length

        if len(data) < total:
            return None, 0

        payload_end = 2 + length - cls.CRC_SIZE
        payload = data[cls.HEADER_SIZE:payload_end]
        crc_wire = struct.unpack('>H', data[payload_end:total])[0]

        if crc_wire != binascii.crc_hqx(payload, 0xFFFF):
            return None, total

        return cls(msg_type=msg_type, payload=payload), total

    @classmethod
    def from_json(cls, msg_type: int, data: dict) -> Frame:
        return cls(msg_type, json.dumps(data, separators=(',', ':')).encode())

    def to_json(self) -> dict:
        return json.loads(self.payload.decode())


# ---------------------------------------------------------------------------
# Protocol v4.0 Payload Helpers
# ---------------------------------------------------------------------------

def make_ack(request_type: int) -> Frame:
    """Make ACK response."""
    return Frame.from_json(MessageType.RESPONSE_ACK, {'ackType': request_type})


def make_error(message: str) -> Frame:
    """Make error response."""
    return Frame.from_json(MessageType.RESPONSE_ERROR, {'message': message})


def make_vehicle_telemetry(telemetry_data: dict) -> Frame:
    """Make vehicle telemetry frame (DEVICE→PHONE).

    Args:
        telemetry_data: Dictionary with vehicle telemetry including:
            - vehicleType: 'ice', 'ev', 'byd', 'mg', etc.
            - batterySoc, batteryVoltage, etc. (for EV)
            - engineRpm, fuelLevel, etc. (for ICE)
            - speedMps, odometer, etc. (common)

    Returns:
        NCP frame ready to send to companion app
    """
    payload = {
        'protocolVersion': PROTOCOL_VERSION,
        'msgType': 'VehicleTelemetry',
        **telemetry_data
    }
    return Frame.from_json(MessageType.TELEMETRY_VEHICLE, payload)


def make_vehicle_info(vin: str, vehicle_type: str, make: str, **kwargs) -> Frame:
    """Make vehicle info response (DEVICE→PHONE).

    Sent after detecting vehicle via OBD.

    Args:
        vin: Vehicle Identification Number
        vehicle_type: 'generic_ice', 'generic_ev', 'byd', 'mg', etc.
        make: Vehicle manufacturer
        **kwargs: Optional fields (model, year, fuelType, supportedPids, etc.)

    Returns:
        NCP frame ready to send to companion app
    """
    payload = {
        'protocolVersion': PROTOCOL_VERSION,
        'msgType': 'VehicleInfo',
        'vin': vin,
        'vehicleType': vehicle_type,
        'make': make,
        **kwargs
    }
    return Frame.from_json(MessageType.RESPONSE_VEHICLE_INFO, payload)


def get_device_info(phase: str = "phase_1", supports_mode22: bool = True,
                    requires_pairing: bool = False, is_paired: bool = False) -> dict:
    """Get device info for Protocol v4.1.

    This is sent in RESPONSE_DEVICE_INFO to inform the companion app about
    device capabilities including vehicle/OBD support.

    Args:
        phase: Device phase ("phase_1", "phase_2", "phase_3")
        supports_mode22: Whether device supports Chinese EV Mode 22 PIDs
        requires_pairing: Whether this device requires OS-level Bluetooth pairing
        is_paired: Whether a phone is currently paired/connected

    Returns:
        Dictionary with device capabilities
    """
    return {
        'protocolVersion': PROTOCOL_VERSION,
        'phase': phase,
        'deviceType': 'openpilot',
        'platform': 'openpilot',
        'hardware': 'rk3588',
        'requiresPairing': requires_pairing,
        'isPaired': is_paired,

        # Hardware capabilities
        'hasStereo': True,
        'hasTeleRoad': phase in ('phase_2', 'phase_3'),
        'hasRtk': phase in ('phase_2', 'phase_3'),

        # Search capabilities
        'supportsSearch': False,
        'searchProviders': [],

        # OBD operating modes (dual-mode support)
        # The device auto-detects which mode the app speaks on SPP connect:
        'supportedOBDModes': [
            'elm327Passthrough',   # Raw AT commands + hex PIDs (any OBD scanner)
            'ncpStructured',       # NCP framed JSON (NavPilot, custom apps)
        ],

        # Vehicle/OBD capabilities
        'supportsMode22': supports_mode22,
        'supportedVehicles': [
            'generic_ice',   # Standard gasoline/diesel
            'generic_ev',    # Standard EV
            'byd',           # BYD Atto 3, Seal, Dolphin
            'mg',            # MG4, MG ZS EV
            'gac',           # GAC AION
            'changan',       # CHANGAN Deepal
            'gwm',           # GWM Ora, Haval
            'geely',         # GEELY Zeekr
            'chery',         # CHERY
        ],

        # Supported services
        'supportedServices': [
            'navDestination',      # Navigation commands
            'navManeuver',         # Maneuver updates
            'navRouteSegment',     # Route segments
            'telemetry',           # Vehicle telemetry (raw + structured)
            'vehicleInfo',         # Vehicle detection/identification
            'elm327Passthrough',   # Raw ELM327 bridge to any scanner app
            'adaptiveDriving',     # Adaptive profile control via VehicleData
        ],
    }


def parse_navigate_command(payload: dict) -> tuple[float, float, str | None, str | None]:
    """Parse navigate command from companion app.
    
    Args:
        payload: JSON payload from CMD_NAVIGATE
    
    Returns:
        (latitude, longitude, name, address)
    """
    dest = payload.get('destination', {})
    return (
        dest.get('latitude', 0.0),
        dest.get('longitude', 0.0),
        dest.get('name'),
        dest.get('address'),
    )


# ---------------------------------------------------------------------------
# Vehicle Type Detection Helpers
# ---------------------------------------------------------------------------

VEHICLE_WMI_MAP = {
    'LGX': ('byd', 'BYD'),
    'SGS': ('mg', 'MG'),
    'LWV': ('gac', 'GAC'),
    'LS5': ('changan', 'CHANGAN'),
    'LGW': ('gwm', 'GWM'),
    'LB3': ('geely', 'GEELY'),
    'LVV': ('chery', 'CHERY'),
}


def detect_vehicle_type_from_vin(vin: str) -> tuple[str, str]:
    """Detect vehicle type and make from VIN.
    
    Args:
        vin: Vehicle Identification Number (17 characters)
    
    Returns:
        (vehicle_type, make) tuple
    """
    if not vin or len(vin) < 3:
        return ('generic_ice', 'Unknown')
    
    wmi = vin[:3].upper()
    
    if wmi in VEHICLE_WMI_MAP:
        return VEHICLE_WMI_MAP[wmi]
    
    # Chinese manufacturer = likely EV
    if wmi.startswith('L'):
        return ('generic_ev', 'Unknown')
    
    return ('generic_ice', 'Unknown')


def is_chinese_ev(vehicle_type: str) -> bool:
    """Check if vehicle type uses Mode 22 PIDs."""
    return vehicle_type in ('byd', 'mg', 'gac', 'changan', 'gwm', 'geely', 'chery')

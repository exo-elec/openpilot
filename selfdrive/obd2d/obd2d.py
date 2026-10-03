#!/usr/bin/env python3
"""obd2d.py - OBD-II Daemon for OpenPilot

Architecture:
- Uses python-udsoncan for UDS protocol (ISO-14229)
- Uses python-can-isotp for ISO-TP transport (ISO-15765)
- Supports Mode 01/09/22 PIDs for ICE and Chinese EVs
- Publishes structured obdState to cereal

Dependencies (git submodules):
- third_party/python-udsoncan
- third_party/python-can-isotp
"""
from __future__ import annotations

import logging
import struct
import time
import threading
from dataclasses import dataclass, field
from enum import IntEnum

import cereal.messaging as messaging
from openpilot.common.realtime import Ratekeeper
from openpilot.common.params import Params
from openpilot.common.core_config import set_daemon_affinity

from openpilot.selfdrive.obd2d.vehicle_db import (
    VehicleType, VEHICLE_PIDS, VEHICLE_WMI_MAP,
    detect_vehicle_type as db_detect_vehicle_type,
    decode_pid_by_name, is_chinese_ev
)
from openpilot.selfdrive.obd2d.telemetry import VehicleInfo
from openpilot.common.swaglog import cloudlog

# UDS adapter (new)
try:
    from openpilot.selfdrive.obd2d.adapters.udsoncan_adapter import (
        UDSVehicleAdapter, UDSConfig
    )
    UDS_AVAILABLE = True
except ImportError as e:
    UDS_AVAILABLE = False
    print(f"UDS libraries not available: {e}")

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
# =============================================================================
# OBD2 Constants
# =============================================================================
class OBDMode(IntEnum):
    """OBD2 service modes."""
    CURRENT_DATA = 0x01
    FREEZE_FRAME = 0x02
    STORED_DTC = 0x03
    CLEAR_DTC = 0x04
    TEST_RESULTS_OXYGEN = 0x05
    TEST_RESULTS_CONTINUOUS = 0x06
    PENDING_DTC = 0x07
    CONTROL_OPERATION = 0x08
    VEHICLE_INFO = 0x09
    PERMANENT_DTC = 0x0A
    MODE_22 = 0x22  # Manufacturer specific


class Protocol(IntEnum):
    """ELM327 protocols."""
    AUTO = 0
    ISO_15765_4_CAN_11BIT_500K = 6
    ISO_15765_4_CAN_29BIT_500K = 7
    ISO_15765_4_CAN_11BIT_250K = 8
    ISO_15765_4_CAN_29BIT_250K = 9


# CAN IDs for OBD2
OBD_BROADCAST_ID = 0x7DF
ECU_RESPONSE_ID = 0x7E8
ECU_REQUEST_ID = 0x7E0


# =============================================================================
# Legacy ISO-TP Handler (fallback when UDS not available)
# =============================================================================
class ISOTPHandler:
    """ISO 15765-2 Transport Protocol handler (legacy fallback)."""

    SF = 0x0  # Single Frame
    FF = 0x1  # First Frame
    CF = 0x2  # Consecutive Frame
    FC = 0x3  # Flow Control

    def __init__(self):
        pass

    def build_single_frame(self, data: bytes) -> bytes:
        """Build ISO-TP single frame."""
        length = len(data)
        if length > 7:
            raise ValueError("Data too long for single frame")
        return bytes([length]) + data.ljust(7, b'\x00')

    def build_first_frame(self, data: bytes) -> tuple[bytes, list[bytes]]:
        """Build ISO-TP first frame and consecutive frames."""
        length = len(data)
        pci = struct.pack('>H', 0x1000 | length)
        ff_data = pci + data[:6]

        cf_list = []
        remaining = data[6:]
        seq = 1
        while remaining:
            cf = bytes([0x20 | (seq & 0x0F)]) + remaining[:7].ljust(7, b'\x00')
            cf_list.append(cf)
            remaining = remaining[7:]
            seq += 1

        return ff_data.ljust(8, b'\x00'), cf_list

    def parse_frame(self, data: bytes) -> tuple[int, bytes | None]:
        """Parse ISO-TP frame, returns (type, payload)."""
        if len(data) < 1:
            return None

        pci = data[0]
        frame_type = (pci >> 4) & 0x0F

        if frame_type == self.SF:
            length = pci & 0x0F
            return (self.SF, data[1:1 + length])

        elif frame_type == self.FF:
            if len(data) < 2:
                return None
            length = ((pci & 0x0F) << 8) | data[1]
            return (self.FF, data[2:])

        elif frame_type == self.CF:
            seq = pci & 0x0F
            return (self.CF, (seq, data[1:]))

        return None


# =============================================================================
# CAN Interface
# =============================================================================
class CANInterface:
    """SocketCAN interface for OBD2 communication."""

    def __init__(self, interface: str = 'can0'):
        self.interface = interface
        self.socket = None
        self.connected = False
        self._init_socket()

    def _init_socket(self):
        """Initialize SocketCAN."""
        try:
            import socket

            self.socket = socket.socket(socket.PF_CAN, socket.SOCK_RAW, socket.CAN_RAW)
            self.socket.bind((self.interface,))
            self.socket.settimeout(1.0)
            self.connected = True
            cloudlog.info(f"CAN interface {self.interface} initialized")
        except Exception as e:
            cloudlog.warning(f"CAN init failed: {e}")
            self.connected = False

    def send(self, can_id: int, data: bytes, extended: bool = False) -> bool:
        """Send CAN frame."""
        if not self.connected:
            return False

        try:
            import socket

            if extended:
                can_id |= socket.CAN_EFF_FLAG

            can_pkt = struct.pack('<IB3x8s', can_id, len(data), data.ljust(8, b'\x00'))
            self.socket.send(can_pkt)
            return True
        except Exception as e:
            cloudlog.debug(f"CAN send error: {e}")
            return False

    def receive(self, timeout: float = 1.0) -> tuple[int, bytes | None]:
        """Receive CAN frame."""
        if not self.connected:
            return None

        try:
            self.socket.settimeout(timeout)
            pkt = self.socket.recv(16)
            can_id, dlc = struct.unpack('<IB3x', pkt[:8])
            data = pkt[8:8 + dlc]

            # Mask out extended frame flag
            can_id &= 0x1FFFFFFF
            return (can_id, data)
        except Exception:
            return None

    def close(self):
        """Close CAN socket."""
        if self.socket:
            self.socket.close()
            self.socket = None
            self.connected = False


# =============================================================================
# OBD2 Request/Response
# =============================================================================
@dataclass
class OBDRequest:
    """OBD2 request."""
    mode: int
    pid: int
    data: bytes = field(default_factory=bytes)

    def to_bytes(self) -> bytes:
        return bytes([self.mode, self.pid]) + self.data


@dataclass
class OBDResponse:
    """OBD2 response."""
    mode: int
    pid: int
    data: bytes
    raw_frames: list[tuple[int, bytes]] = field(default_factory=list)

    def to_elm_string(self, spaces: bool = True) -> str:
        """Convert to ELM327 hex string format."""
        resp_mode = self.mode + 0x40
        data = bytes([resp_mode, self.pid]) + self.data
        hex_str = data.hex().upper()
        if spaces:
            return ' '.join(hex_str[i:i + 2] for i in range(0, len(hex_str), 2))
        return hex_str


# =============================================================================
# Main ELM327 Daemon
# =============================================================================
class OBD2D:
    """OBD-II Daemon - OBD2 bridge for OpenPilot with UDS support."""

    def __init__(self):
        # CPU affinity per project convention
        set_daemon_affinity("obd2d")

        # CAN interface
        self.can = CANInterface('can0')
        self.isotp = ISOTPHandler()

        # UDS adapter (new)
        self.uds: UDSVehicleAdapter | None = None
        self._use_uds = False
        if UDS_AVAILABLE:
            try:
                uds_config = UDSConfig(
                    can_interface='can0',
                    tx_addr=ECU_REQUEST_ID,
                    rx_addr=ECU_RESPONSE_ID,
                    timeout=5.0
                )
                self.uds = UDSVehicleAdapter(uds_config)
                cloudlog.info("UDS adapter initialized")
            except Exception as e:
                cloudlog.warning(f"UDS adapter init failed: {e}")

        # Configuration
        self.protocol = Protocol.ISO_15765_4_CAN_11BIT_500K
        self.header = OBD_BROADCAST_ID
        self.rx_id = ECU_RESPONSE_ID
        self.timeout_ms = 200
        self.echo = True
        self.spaces = True
        self.linefeed = True

        # Vehicle detection
        self.vin: str | None = None
        self.vehicle_type: str = 'generic_ice'
        self.vehicle_make: str = 'Unknown'
        self.vehicle_info: VehicleInfo | None = None
        self._vehicle_detected = False

        # Cereal messaging
        self.pm = messaging.PubMaster(['obdResponse', 'obdState'])
        self.sm = messaging.SubMaster(['obdCommand'])

        # Running state
        self.running = False
        self._threads: list[threading.Thread] = []
        self._state_lock = threading.Lock()

        # Mock mode for testing without CAN
        self.mock_mode = not self.can.connected
        if self.mock_mode:
            cloudlog.warning("Running in MOCK mode (no CAN interface)")

        cloudlog.info("OBD2D initialized")

    def detect_vehicle(self) -> bool:
        """Detect vehicle from VIN and initialize telemetry."""
        cloudlog.info("Detecting vehicle...")

        # Try UDS first for VIN reading
        if self.uds and not self.mock_mode:
            try:
                if self.uds.connect():
                    vin = self.uds.read_vin()
                    if vin:
                        self.vin = vin
                        self._use_uds = True
                        cloudlog.info(f"VIN read via UDS: {vin}")
                    else:
                        self.uds.close()
            except Exception as e:
                cloudlog.debug(f"UDS VIN read failed: {e}")

        # Fallback to legacy OBD Mode 09
        if not self.vin:
            vin = self._read_vin_legacy()
            if vin:
                self.vin = vin
                cloudlog.info(f"VIN read via legacy OBD: {vin}")

        if not self.vin:
            cloudlog.warning("Could not read VIN")
            return False

        # Detect vehicle type
        vt = db_detect_vehicle_type(self.vin)
        self.vehicle_type = vt.value
        self.vehicle_make = VEHICLE_WMI_MAP.get(self.vin[:3].upper(), ('generic_ice', 'Unknown'))[1]

        # Set vehicle type for UDS adapter
        if self.uds:
            self.uds.set_vehicle_type(self.vehicle_type)

        cloudlog.info(f"Vehicle detected: {self.vehicle_make} ({self.vehicle_type})")
        cloudlog.info(f"VIN: {self.vin}")

        # Store in params
        try:
            params = Params()
            params.put("CarVin", self.vin)
            params.put("CarMake", self.vehicle_make)
            params.put("CarType", self.vehicle_type)
        except Exception as e:
            cloudlog.debug(f"Failed to store VIN: {e}")

        self._vehicle_detected = True
        return True

    def _read_vin_legacy(self) -> str | None:
        """Read VIN from vehicle using Mode 09 PID 02 (legacy)."""
        if self.mock_mode:
            return "LGX1234567890ABCDE"  # Mock BYD VIN

        try:
            resp = self.query_pid(OBDMode.VEHICLE_INFO, 0x02)
            if resp and resp.data:
                vin_bytes = resp.data
                if len(vin_bytes) >= 17:
                    return vin_bytes[:17].decode('ascii', errors='ignore')
        except Exception as e:
            cloudlog.debug(f"VIN read error: {e}")

        return None

    def query_pid(self, mode: int, pid: int, data: bytes = b'') -> OBDResponse | None:
        """Query OBD2 PID (legacy method)."""
        if self.mock_mode:
            return self._mock_response(mode, pid)

        request = OBDRequest(mode, pid, data)
        req_data = request.to_bytes()

        if len(req_data) <= 7:
            can_data = self.isotp.build_single_frame(req_data)
        else:
            ff, cfs = self.isotp.build_first_frame(req_data)
            can_data = ff

        if not self.can.send(self.header, can_data):
            return None

        frames = []
        start = time.monotonic()
        while time.monotonic() - start < (self.timeout_ms / 1000.0):
            rx = self.can.receive(0.1)
            if rx:
                can_id, data = rx
                if can_id == self.rx_id:
                    frames.append((can_id, data))
                    parsed = self.isotp.parse_frame(data)
                    if parsed and parsed[0] == ISOTPHandler.SF:
                        break

        if not frames:
            return None

        return self._parse_response(frames, mode, pid)

    def _parse_response(self, frames: list[tuple[int, bytes]],
                        req_mode: int, req_pid: int) -> OBDResponse | None:
        """Parse ISO-TP response frames."""
        if not frames:
            return None

        first = frames[0]
        parsed = self.isotp.parse_frame(first[1])

        if not parsed:
            return None

        frame_type, payload = parsed

        if frame_type == ISOTPHandler.SF:
            if len(payload) >= 2:
                resp_mode = payload[0]
                resp_pid = payload[1]
                if resp_mode == req_mode + 0x40 and resp_pid == req_pid:
                    return OBDResponse(req_mode, req_pid, payload[2:], frames)

        return None

    def query_mode22_uds(self, pid_hex: str) -> OBDResponse | None:
        """Query Mode 22 manufacturer specific PID using UDS."""
        if not self.uds or not self.uds.is_connected():
            return None

        try:
            result = self.uds.read_mode22_pid(pid_hex)
            if result:
                # Convert to OBDResponse format
                # Mode 22 response is 0x62 (Mode 22 + 0x40)
                return OBDResponse(0x22, int(pid_hex, 16),
                                   struct.pack('>f', result['value']))
        except Exception as e:
            cloudlog.debug(f"UDS Mode 22 query failed: {e}")

        return None

    def _mock_response(self, mode: int, pid: int) -> OBDResponse | None:
        """Generate mock response for testing."""
        if mode == OBDMode.CURRENT_DATA:
            if pid == 0x00:
                return OBDResponse(mode, pid, bytes([0xFF, 0xFF, 0xFF, 0xFF]))
            elif pid == 0x0C:  # RPM
                rpm = int(1750 / 0.25)
                return OBDResponse(mode, pid, bytes([(rpm >> 8) & 0xFF, rpm & 0xFF]))
            elif pid == 0x0D:  # Speed
                return OBDResponse(mode, pid, bytes([65]))
            elif pid == 0x05:  # Coolant temp
                return OBDResponse(mode, pid, bytes([90 + 40]))
            elif pid == 0x2F:  # Fuel level
                return OBDResponse(mode, pid, bytes([int(60 * 2.55)]))

        if mode == 0x22:
            if pid == 0x1FFC:  # BYD SOC
                return OBDResponse(mode, pid, bytes([75 * 2]))
            elif pid == 0x1FFE:  # BYD voltage
                voltage = 4000  # 400.0V
                return OBDResponse(mode, pid, bytes([(voltage >> 8) & 0xFF, voltage & 0xFF]))

        return None

    # =========================================================================
    # Telemetry Collection -> obdState
    # =========================================================================

    def _collect_obd_state(self) -> None:
        """Collect OBD data and publish obdState."""
        if not self._vehicle_detected:
            if not self.detect_vehicle():
                return

        msg = messaging.new_message('obdState')
        obd = msg.obdState

        # Vehicle info
        obd.vin = self.vin or ""
        obd.vehicleType = self.vehicle_type
        obd.make = self.vehicle_make
        obd.obdConnected = self.can.connected or (self.uds and self.uds.is_connected())
        obd.protocol = self.protocol.value

        # Collect based on vehicle type
        if is_chinese_ev(self.vehicle_type):
            self._collect_mode22_state(obd)
        elif self.vehicle_type == VehicleType.GENERIC_EV.value:
            self._collect_generic_ev_state(obd)
        else:
            self._collect_ice_state(obd)

        self.pm.send('obdState', msg)

    def _collect_mode22_state(self, obd):
        """Collect Mode 22 telemetry for Chinese EVs using UDS."""
        if not self.uds or not self.uds.is_connected():
            # Fallback to legacy
            self._collect_mode22_state_legacy(obd)
            return

        try:
            VehicleType(self.vehicle_type)
        except ValueError:
            return

        # Get Mode 22 PIDs for this vehicle
        from openpilot.selfdrive.obd2d.adapters.udsoncan_adapter import UDSVehicleAdapter
        vehicle_dids = UDSVehicleAdapter.MODE22_DIDS.get(self.vehicle_type, {})

        for did_id, (name, _codec) in vehicle_dids.items():
            try:
                pid_hex = f"{did_id:06X}"
                result = self.uds.read_mode22_pid(pid_hex)
                if result and result['value'] is not None:
                    self._set_obd_field(obd, name, result['value'])
            except Exception as e:
                cloudlog.debug(f"Failed to query {name}: {e}")

    def _collect_mode22_state_legacy(self, obd):
        """Legacy Mode 22 collection (fallback)."""
        try:
            vt = VehicleType(self.vehicle_type)
        except ValueError:
            return

        pid_defs = VEHICLE_PIDS.get(vt, {})

        for hex_pid, (name, _, _, _) in pid_defs.items():
            try:
                resp = self.query_mode22(hex_pid)
                if resp and resp.data:
                    if len(resp.data) >= 3 and resp.data[0] == 0x62:
                        data_bytes = bytes(resp.data[3:])
                    else:
                        data_bytes = bytes(resp.data)
                    value = decode_pid_by_name(name, data_bytes, vt)
                    if value is not None:
                        self._set_obd_field(obd, name, value)
            except Exception as e:
                cloudlog.debug(f"Failed to query {name}: {e}")

    def _collect_generic_ev_state(self, obd):
        """Collect standard Mode 01 telemetry for generic EVs."""
        pids = [
            (0x5B, 'batterySoc', lambda d: d[0] * 100.0 / 255.0 if d else None),
            (0x42, 'batteryVoltage', lambda d: ((d[0] << 8) + d[1]) / 1000.0 if len(d) >= 2 else None),
            (0x0D, 'vehicleSpeed', lambda d: float(d[0]) if d else None),
        ]

        for pid, field_name, decoder in pids:
            try:
                resp = self.query_pid(OBDMode.CURRENT_DATA, pid)
                if resp and resp.data:
                    value = decoder(resp.data)
                    if value is not None:
                        self._set_obd_field(obd, field_name, value)
            except Exception as e:
                cloudlog.debug(f"Failed to query {field_name}: {e}")

    def _collect_ice_state(self, obd):
        """Collect standard Mode 01 telemetry for ICE vehicles."""
        pids = [
            (0x0C, 'engineRpm', lambda d: ((d[0] << 8) + d[1]) * 0.25 if len(d) >= 2 else None),
            (0x0D, 'vehicleSpeed', lambda d: float(d[0]) if d else None),
            (0x05, 'coolantTemp', lambda d: float(d[0] - 40) if d else None),
            (0x2F, 'fuelLevel', lambda d: d[0] * 100.0 / 255.0 if d else None),
        ]

        for pid, field_name, decoder in pids:
            try:
                resp = self.query_pid(OBDMode.CURRENT_DATA, pid)
                if resp and resp.data:
                    value = decoder(resp.data)
                    if value is not None:
                        self._set_obd_field(obd, field_name, value)
            except Exception as e:
                cloudlog.debug(f"Failed to query {field_name}: {e}")

    def _set_obd_field(self, obd, name: str, value: float):
        """Set a field on obdState capnp struct by name."""
        field_map = {
            'batterySoc': 'batterySoc',
            'batterySoh': 'batterySoh',
            'batteryVoltage': 'batteryVoltage',
            'batteryCurrent': 'batteryCurrent',
            'batteryTempMax': 'batteryTempMax',
            'batteryTempMin': 'batteryTempMin',
            'batteryPower': 'batteryPower',
            'chargingStatus': 'chargingStatus',
            'chargingPower': 'chargingPower',
            'rangeRemaining': 'rangeRemaining',
            'motorRpm': 'motorRpm',
            'motorTemp': 'motorTemp',
            'inverterTemp': 'inverterTemp',
            'auxBatteryVoltage': 'auxBatteryVoltage',
            'odometer': 'odometer',
            'engineRpm': 'engineRpm',
            'vehicleSpeed': 'vehicleSpeed',
            'coolantTemp': 'coolantTemp',
            'throttlePos': 'throttlePos',
            'engineLoad': 'engineLoad',
            'fuelLevel': 'fuelLevel',
            'engineTemp': 'coolantTemp',
            'speed': 'vehicleSpeed',
            'speedMps': 'vehicleSpeed',
        }

        capnp_name = field_map.get(name, name)
        if hasattr(obd, capnp_name):
            try:
                if capnp_name == 'chargingStatus':
                    setattr(obd, capnp_name, int(value))
                else:
                    setattr(obd, capnp_name, float(value))
            except Exception as e:
                cloudlog.debug(f"Failed to set {capnp_name}: {e}")

    # =========================================================================
    # Background Threads
    # =========================================================================

    def _telemetry_loop(self):
        """Periodic OBD state collection and publish."""
        rk = Ratekeeper(2.0)  # 2 Hz obdState update

        while self.running:
            try:
                with self._state_lock:
                    self._collect_obd_state()
            except Exception as e:
                cloudlog.debug(f"Telemetry error: {e}")

            rk.keep_time()

    def _cereal_loop(self):
        """Process commands from Cereal."""
        rk = Ratekeeper(10.0)

        while self.running:
            self.sm.update(0)

            if self.sm.updated['obdCommand']:
                cmd = self.sm['obdCommand']
                if cmd.command:
                    with self._state_lock:
                        response = self.handle_elm_command(cmd.command)

                    msg = messaging.new_message('obdResponse')
                    msg.obdResponse.response = response
                    msg.obdResponse.success = response not in ['NO DATA', '?']
                    self.pm.send('obdResponse', msg)

            rk.keep_time()

    # =========================================================================
    # ELM327 Command Handling
    # =========================================================================

    def handle_elm_command(self, cmd: str) -> str:
        """Handle ELM327 AT command or OBD2 query."""
        cmd = cmd.strip().upper()

        if not cmd:
            return ""

        if cmd.startswith('AT'):
            return self._handle_at(cmd[2:])

        if all(c in '0123456789ABCDEF' for c in cmd):
            return self._handle_obd_hex(cmd)

        return "?"

    def _handle_at(self, cmd: str) -> str:
        """Handle AT configuration command."""
        if cmd == 'Z' or cmd.startswith('Z'):
            self.__init__()
            return "ELM327 v1.5"

        if cmd == 'E0':
            self.echo = False
            return "OK"
        if cmd == 'E1':
            self.echo = True
            return "OK"

        if cmd == 'L0':
            self.linefeed = False
            return "OK"
        if cmd == 'L1':
            self.linefeed = True
            return "OK"

        if cmd == 'S0':
            self.spaces = False
            return "OK"
        if cmd == 'S1':
            self.spaces = True
            return "OK"

        if cmd.startswith('SP'):
            try:
                proto = int(cmd[2:])
                self.protocol = Protocol(proto)
                return "OK"
            except ValueError:
                pass

        if cmd == 'DP':
            names = {
                6: 'ISO 15765-4 (CAN 11-bit ID, 500 kbaud)',
                7: 'ISO 15765-4 (CAN 29-bit ID, 500 kbaud)',
                8: 'ISO 15765-4 (CAN 11-bit ID, 250 kbaud)',
            }
            return names.get(self.protocol.value, 'AUTO')

        if cmd.startswith('SH'):
            try:
                self.header = int(cmd[2:].strip(), 16)
                return "OK"
            except ValueError:
                pass

        if cmd.startswith('ST'):
            try:
                self.timeout_ms = int(cmd[2:].strip(), 16) * 4
                return "OK"
            except ValueError:
                pass

        if cmd == 'I':
            return "ELM327 v1.5"
        if cmd == 'RV':
            return "12.6V"

        return "OK"

    def _handle_obd_hex(self, cmd: str) -> str:
        """Handle OBD2 hex command."""
        try:
            mode = int(cmd[0:2], 16)
            pid_str = cmd[2:].strip()
        except ValueError:
            return "?"

        if mode == 0x22:
            return self._handle_mode22(pid_str)

        try:
            pid = int(pid_str, 16) if pid_str else 0
        except ValueError:
            return "?"

        response = self.query_pid(mode, pid)

        if response:
            return response.to_elm_string(self.spaces)

        return "NO DATA"

    def _handle_mode22(self, pid_hex: str) -> str:
        """Handle Mode 22 manufacturer specific queries."""
        if not pid_hex:
            return "?"

        # Try UDS first
        if self._use_uds and self.uds:
            resp = self.query_mode22_uds(pid_hex)
            if resp:
                result = f"62 {pid_hex.upper()} "
                result += resp.to_elm_string(self.spaces).replace("41 ", "")
                return result.strip()

        # Fallback to legacy
        resp = self.query_mode22(pid_hex)

        if resp:
            result = f"62 {pid_hex.upper()} "
            result += resp.to_elm_string(self.spaces).replace("41 ", "")
            return result.strip()

        return "NO DATA"

    def query_mode22(self, pid_hex: str) -> OBDResponse | None:
        """Query Mode 22 manufacturer specific PID (legacy)."""
        try:
            pid_bytes = bytes.fromhex(pid_hex)
            if len(pid_bytes) >= 2:
                mode = 0x22
                pid_high = pid_bytes[0]
                pid_low = pid_bytes[1] if len(pid_bytes) > 1 else 0
                pid = (pid_high << 8) | pid_low
                return self.query_pid(mode, pid)
        except Exception as e:
            cloudlog.debug(f"Mode 22 query error: {e}")

        return None

    # =========================================================================
    # Main Loop
    # =========================================================================

    def run(self):
        """Main loop."""
        self.running = True

        # Connect UDS if available
        if self.uds and not self.mock_mode:
            try:
                if self.uds.connect():
                    cloudlog.info("UDS connected")
                    self._use_uds = True
            except Exception as e:
                cloudlog.warning(f"UDS connection failed: {e}")

        telemetry_thread = threading.Thread(target=self._telemetry_loop, daemon=True, name="telemetry")
        cereal_thread = threading.Thread(target=self._cereal_loop, daemon=True, name="cereal")

        telemetry_thread.start()
        cereal_thread.start()

        self._threads.extend([telemetry_thread, cereal_thread])

        cloudlog.info("OBD2D running")

        try:
            while self.running:
                time.sleep(1)
        except KeyboardInterrupt:
            pass
        finally:
            self.stop()

    def stop(self):
        """Cleanup."""
        self.running = False

        for t in self._threads:
            if t.is_alive():
                t.join(timeout=1.0)

        if self.uds:
            self.uds.close()

        self.can.close()
        cloudlog.info("OBD2D stopped")


def main():
    """Entry point."""
    daemon = OBD2D()
    daemon.run()


if __name__ == '__main__':
    main()

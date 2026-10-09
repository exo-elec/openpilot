# OBD-II Daemon (obd2d)

OBD2/UDS diagnostic daemon for OpenPilot. Interfaces with vehicle CAN bus via SocketCAN and provides ELM327 command interface to `bluetoothd`.

**Status:** Clean migration implementation with UDS support via python-udsoncan.

## Architecture Note: OBD2 vs ADAS Control Separation

**obd2d is separate from socketd.** While both use SocketCAN, they serve different purposes:

| Daemon | Purpose | CAN Bus Usage | Direction | Status |
|--------|---------|---------------|-----------|--------|
| **obd2d** | OBD2 diagnostics, telemetry | OBD2 port (can0/can1) | Query/Response | **New** |
| **socketd** | ADAS vehicle control | Main vehicle bus (can0) | Continuous TX/RX | Replaces pandad |

The OBD2 port provides diagnostic access to vehicle ECUs (engine, battery, etc.), while socketd handles the ADAS control path through Panda/TC275 for steering and acceleration.

### Clean Migration: No Backward Compatibility

| Removed | Replacement | Reason |
|---------|-------------|--------|
| Legacy OBD Python scripts | `obd2d` daemon | Unified architecture |
| Direct Panda OBD queries | `obd2d` SocketCAN | Cleaner separation |
| Multiple OBD implementations | Single `obd2d` path | Eliminate confusion |
| Old vehicle DB files | `vehicle_db.py` | Consolidated database |

**Breaking Change:** Old OBD implementations completely removed. Custom scripts must use `obdCommand`/`obdResponse` cereal messages or `obdState` telemetry.

## Architecture

### Hardware Connections

```
┌─────────────────────────────────────────────────────────────────────────┐
│                    HARDWARE ARCHITECTURE                                 │
├─────────────────────────────────────────────────────────────────────────┤
│                                                                         │
│  ┌─────────────┐      BLE OBD         ┌──────────────┐      Cereal    │
│  │  NavPilot   │◄────────────────────►│  bluetoothd  │◄───────────────│►│
│  │ (ELM327 App)│  ATZ, ATSP0, 010C    │  (BLE GATT)  │  obdCommand/   │ │
│  │             │                      │              │  obdResponse   │ │
│  └─────────────┘  41 0C 1B 56 (RPM)   └──────────────┘                │ │
│       ▲                                    ▲                          │ │
│       │                                    │                          │ │
│       │ NCP v4.1                           │ Cereal                   │ │
│       │                                    │                          │ │
│       │                            ┌───────┴───────┐                  │ │
│       │                            │    obd2d      │                  │ │
│       │                            │  (OBD2/UDS)   │                  │ │
│       │                            └───────┬───────┘                  │ │
│       │                                    │                          │ │
│       │                                    │ SocketCAN                │ │
│       │                                    ▼                          │ │
│       │                            ┌───────────────┐                  │ │
│       └────────────────────────────│   OBD2 Port   │◄─────────────────┘ │
│                                    │  (SocketCAN)  │                    │
│                                    └───────┬───────┘                    │
│                                            │                            │
│                                    ┌───────┴───────┐                    │
│                                    │ Vehicle ECUs  │                    │
│                                    │ • ECM/PCM     │                    │
│                                    │ • BCM         │                    │
│                                    │ • ABS/ESP     │                    │
│                                    └───────────────┘                    │
│                                                                         │
│  ┌──────────────────────────────────────────────────────────────────┐  │
│  │  SEPARATE: ADAS Control Path (socketd + TC275)                   │  │
│  │                                                                  │  │
│  │  ┌─────────┐    Cereal    ┌─────────┐   SocketCAN   ┌─────────┐ │  │
│  │  │controlsd│◄────────────►│ socketd │◄─────────────►│  OBD2   │ │  │
│  │  └─────────┘              └────┬────┘               │  Port   │ │  │
│  │                                │                    │ (shared)│ │  │
│  │                                │ UART/SPI           └────┬────┘ │  │
│  │                                ▼                         │      │  │
│  │                          ┌─────────┐                     │      │  │
│  │                          │  TC275  │◄────────────────────┘      │  │
│  │                          │freeRTOS │    CAN Bus (to vehicle)     │  │
│  │                          └────┬────┘                            │  │
│  │                               │                                  │  │
│  │                          ┌────┴────┐                             │  │
│  │                          │ Vehicle │                             │  │
│  │                          │EPS/VCU  │                             │  │
│  │                          └─────────┘                             │  │
│  │                                                                  │  │
│  └──────────────────────────────────────────────────────────────────┘  │
│                                                                         │
└─────────────────────────────────────────────────────────────────────────┘

Key Points:
• obd2d → OBD2 Port: Diagnostic queries only (Mode 01/09/22)
• socketd → OBD2 Port: ADAS control communication (TX/RX)
• socketd ↔ TC275: Safety Layer coordination
• TC275 → Vehicle: ADAS control gateway
• Both obd2d and socketd may share OBD2 port CAN bus
```

## Communication Flow

1. **NavPilot** sends ELM327 AT command via BLE OBD characteristic
2. **bluetoothd** receives command → publishes `obdCommand` via Cereal
3. **obd2d** subscribes to `obdCommand` → processes via ELM327 interpreter
4. **obd2d** sends CAN frames via SocketCAN → waits for ECU response
5. **obd2d** receives CAN response → formats ELM327 response
6. **obd2d** publishes `obdResponse` via Cereal
7. **bluetoothd** receives `obdResponse` → notifies phone via BLE

## Structured OBD Data Flow

In addition to raw ELM327 passthrough, obd2d periodically publishes `obdState`:

```
obd2d ──► obdState ──► cereal ──► bluetoothd
                                        │
                                        ├──► TELEMETRY_VEHICLE (includes OBD fields)
                                        └──► RESPONSE_VEHICLE_INFO (on CMD_GET_VEHICLE_INFO)
```

`obdState` contains:
- Vehicle identification (VIN, make, vehicleType)
- Standard OBD data (RPM, speed, temps, fuel, throttle)
- EV telemetry (batterySoc, batteryVoltage, motorTemp, rangeRemaining, etc.)

### Vehicle Type Detection

obd2d automatically detects vehicle type from VIN and uses appropriate PID set:

| Vehicle Type | Detected By | PID Set |
|--------------|-------------|---------|
| `generic_ice` | Standard OBD2, no EV indicators | Mode 01 (RPM, coolant, fuel) |
| `generic_ev` | OBD2 shows hybrid battery PIDs | Mode 01 (battery SOC, voltage) |
| `generic_phev` | Fuel type = PHEV/Plug-in Hybrid | Mode 01 (both ICE + EV PIDs) |
| `byd` | VIN starts with LGX | Mode 22 (BYD-specific PIDs) |
| `tesla` | Make = TESLA | Mode 01 + Tesla-specific |

The `vehicleType` field in `obdState` allows UI components (like TelemetryCard) to adapt their display:
- **ICE**: Shows RPM, coolant, fuel
- **EV**: Shows battery SOC, voltage, motor temp
- **PHEV**: Shows both battery SOC and RPM, with EV mode indicator

PHEVs (Plug-in Hybrids) like Toyota Prius Prime, Honda Clarity, or BYD DM-i models have both an engine and a battery, so they display metrics for both systems.

## UDS Integration

obd2d includes UDS (ISO 14229) support via python-udsoncan and python-can-isotp:

```
┌─────────────────────────────────────────────────────────┐
│                    UDS Stack                             │
├─────────────────────────────────────────────────────────┤
│  Application Layer: UDS (ISO 14229)                     │
│  ├─ Read Data By Identifier (0x22)                      │
│  ├─ Diagnostic Session Control (0x10)                   │
│  ├─ Read DTC Information (0x19)                         │
│  └─ Clear Diagnostic Information (0x14)                 │
├─────────────────────────────────────────────────────────┤
│  Transport Layer: ISO-TP (ISO 15765-2)                  │
│  ├─ Single Frame (SF)                                   │
│  ├─ First Frame (FF)                                    │
│  ├─ Consecutive Frame (CF)                              │
│  └─ Flow Control (FC)                                   │
│  (via python-can-isotp)                                 │
├─────────────────────────────────────────────────────────┤
│  Data Link: SocketCAN                                   │
│  (via python-can)                                       │
└─────────────────────────────────────────────────────────┘
```

### UDS Advantages

| Feature | Legacy ISO-TP | UDS (python-udsoncan) |
|---------|--------------|----------------------|
| Timing Control | Manual | Automatic (STmin, BS) |
| Error Recovery | Basic | Full UDS protocol |
| Session Management | None | Built-in |
| DTC Handling | Manual | Standardized |
| Mode 22 Support | Raw bytes | Structured DIDs |
| Multi-frame | Manual | Automatic |

### UDS Initialization

```bash
# Initialize submodules (required for UDS support)
git submodule update --init --recursive third_party/python-udsoncan
git submodule update --init --recursive third_party/python-can-isotp
```

## Supported Protocols

| Protocol | ID | Description |
|----------|-----|-------------|
| ISO 15765-4 | 6 | CAN 11-bit ID, 500 kbaud |
| ISO 15765-4 | 7 | CAN 29-bit ID, 500 kbaud |
| ISO 15765-4 | 8 | CAN 11-bit ID, 250 kbaud |
| ISO 15765-4 | 9 | CAN 29-bit ID, 250 kbaud |

## Supported OBD2 Modes

### Mode 01 - Current Data
| PID | Name | Unit | Description |
|-----|------|------|-------------|
| 0x00 | PIDS_SUPPORTED | - | Supported PIDs 01-20 |
| 0x04 | ENGINE_LOAD | % | Calculated engine load |
| 0x05 | COOLANT_TEMP | °C | Engine coolant temperature |
| 0x0B | INTAKE_PRESSURE | kPa | Intake manifold pressure |
| 0x0C | ENGINE_RPM | rpm | Engine RPM |
| 0x0D | VEHICLE_SPEED | km/h | Vehicle speed |
| 0x0E | TIMING_ADVANCE | ° | Timing advance |
| 0x0F | INTAKE_TEMP | °C | Intake air temperature |
| 0x10 | MAF_RATE | g/s | MAF air flow rate |
| 0x11 | THROTTLE_POS | % | Throttle position |
| 0x2F | FUEL_LEVEL | % | Fuel tank level |
| 0x5C | OIL_TEMP | °C | Engine oil temperature |

### Mode 09 - Vehicle Information
| PID | Name | Description |
|-----|------|-------------|
| 0x00 | VI_PIDS_SUPPORTED | Supported VInfo PIDs |
| 0x02 | VIN | Vehicle Identification Number |

### Mode 22 - Manufacturer Specific (Chinese EVs)
| Brand | Supported PIDs |
|-------|---------------|
| BYD | batterySoc, batteryVoltage, motorTemp, rangeRemaining, chargingPower |
| MG | batterySoc, batterySoh, batteryVoltage, motorRpm, inverterTemp |
| GAC | batterySoc, batteryCurrent, chargingStatus, rangeRemaining |
| CHANGAN | batterySoc, batteryVoltage, motorTemp, inverterTemp |
| GWM | batterySoc, batterySoh, batteryPower, chargingStatus, hevMode |
| GEELY | batterySoc, batteryVoltage, motorRpm, energyConsumption |
| CHERY | batterySoc, batteryCurrent, chargingPower, odometer |

## Module Structure

| File | Purpose |
|------|---------|
| `obd2d.py` | Main daemon — SocketCAN, ISO-TP, ELM327 command interpreter, UDS integration |
| `vehicle_db.py` | PID databases, vehicle type detection, decoders |
| `telemetry.py` | VehicleTelemetry / VehicleInfo dataclasses |
| `adapters/__init__.py` | Adapter module exports |
| `adapters/udsoncan_adapter.py` | UDS adapter using python-udsoncan |

## UDS Adapter Usage

```python
from openpilot.selfdrive.obd2d.adapters.udsoncan_adapter import UDSVehicleAdapter, UDSConfig

# Create adapter
config = UDSConfig(
    can_interface='can0',
    tx_addr=0x7E0,
    rx_addr=0x7E8,
    timeout=5.0
)
uds = UDSVehicleAdapter(config)

# Connect
if uds.connect():
    # Set vehicle type for Mode 22 mapping
    uds.set_vehicle_type('byd')
    
    # Read VIN
    vin = uds.read_vin()
    
    # Read Mode 22 PID
    result = uds.read_mode22_pid('221FFC')  # BYD SOC
    print(f"SOC: {result['value']}%")
    
    # Read DTCs
    dtcs = uds.read_dtcs()
    
    uds.close()
```

## Cereal Messages

### Published
- `obdResponse` - ELM327 response strings (passthrough to bluetoothd)
- `obdState` - Structured OBD data (2Hz, vehicle info + telemetry)

### Subscribed
- `obdCommand` - ELM327 commands from bluetoothd

## Testing

```bash
# Initialize submodules (required for UDS)
git submodule update --init --recursive

# Start daemon manually
cd /data/openpilot
python3 selfdrive/obd2d/obd2d.py

# Test with mock CAN (no hardware)
# Daemon auto-detects missing CAN and runs in mock mode

# Test commands
echo "ATZ"     # Reset
echo "0100"   # Supported PIDs
echo "010C"   # RPM
echo "010D"   # Speed
```

## Configuration

| Parameter | Default | Description |
|-----------|---------|-------------|
| `can_interface` | `can0` | SocketCAN interface |
| `protocol` | `6` | ISO 15765-4 CAN 11-bit 500k |
| `timeout_ms` | `200` | Response timeout |
| `header` | `0x7DF` | OBD2 broadcast ID |

## Mock Mode

When CAN interface is not available, obd2d runs in mock mode:
- Returns simulated data for common PIDs
- Useful for testing without vehicle connection

## Process Configuration

```python
# system/manager/process_config.py
PythonProcess("obd2d", "selfdrive.obd2d.obd2d", always_run)
```

## Git Submodules

The UDS functionality requires these submodules:

```bash
# Add/update submodules
git submodule add https://github.com/pylessard/python-udsoncan.git third_party/python-udsoncan
git submodule add https://github.com/pylessard/python-can-isotp.git third_party/python-can-isotp

# Pin to stable versions
cd third_party/python-udsoncan
git checkout v1.23.2
cd ../python-can-isotp
git checkout v2.0.7
```

## See Also

- [bluetoothd README](../bluetoothd/README.md) - BLE interface

# vehicled Architecture

## Overview

`vehicled` is the unified SocketCAN vehicle adapter using Tesla-format CAN
protocol. It replaces the Panda-dependent generic `card.py` path while using
the pinned OpenDBC submodule as the shared protocol/model source. The
BrownPanda gateway remains the hardware safety gateway (v1 = TC275, v2 = TC375).

## Safety Architecture

```
┌─────────────────────────────────────────────────────────────────┐
│                     SAFETY LAYERS                                │
├─────────────────────────────────────────────────────────────────┤
│                                                                  │
│  Layer 1 (Software): vehicled safety                            │
│  - Location: selfdrive/vehicled/                                │
│  - Limits: TIGHTER (80% of Panda limits)                        │
│  - Purpose: Catch bugs, enforce smooth control                  │
│  - Runs on: RK3588/RK3576 A76 cores                             │
│                                                                  │
│  Layer 2 (Hardware): BrownPanda Gateway                          │
│  - Location: External microcontroller                           │
│  - Limits: LOOSER (100% of Panda limits)                        │
│  - Purpose: Safety net, hardware enforcement                    │
│  - Runs on: BrownPanda v1 (TC275) or v2 (TC375)                  │
│                                                                  │
└─────────────────────────────────────────────────────────────────┘
```

## Component Architecture

```
┌─────────────────────────────────────────────────────────────────┐
│                        vehicled                                 │
├─────────────────────────────────────────────────────────────────┤
│                                                                  │
│  ┌─────────────┐    ┌─────────────┐    ┌─────────────┐         │
│  │   state.py  │    │ controller. │    │   safety    │         │
│  │             │    │    .py      │    │  _manager   │         │
│  │ VehicleState│    │  Vehicle    │    │  Safety     │         │
│  │             │    │ Controller  │    │  Manager    │         │
│  │ - Parse CAN │    │             │    │             │         │
│  │ - Track     │    │ - Generate  │    │ - Check TX  │         │
│  │   vehicle   │    │   CAN       │    │ - Process   │         │
│  │   state     │    │   commands  │    │   RX        │         │
│  │             │    │             │    │ - Enforce   │         │
│  │             │    │             │    │   limits    │         │
│  └──────┬──────┘    └──────┬──────┘    └──────┬──────┘         │
│         │                  │                  │                │
│         └──────────────────┼──────────────────┘                │
│                            │                                   │
│                            ▼                                   │
│                    ┌─────────────┐                             │
│                    │  vehicle.py │                             │
│                    │   (main)    │                             │
│                    │             │                             │
│                    │ - Coordinates│                            │
│                    │ - Safety     │                             │
│                    │   checks     │                             │
│                    │ - Messaging  │                             │
│                    └──────┬──────┘                             │
│                           │                                    │
└───────────────────────────┼────────────────────────────────────┘
                            │
                            ▼
┌─────────────────────────────────────────────────────────────────┐
│                      socketd (system level)                     │
│                     PLAIN CAN BRIDGE - NO SAFETY                │
├─────────────────────────────────────────────────────────────────┤
│                                                                  │
│  - Receives CAN from vehicle (SocketCAN)                        │
│  - Publishes to 'can' topic                                     │
│  - Receives 'sendcan' topic                                     │
│  - Sends to vehicle (SocketCAN)                                 │
│  - NO SAFETY CHECKS (just a bridge)                             │
│                                                                  │
└─────────────────────────────────────────────────────────────────┘
                            │
                            ▼
┌─────────────────────────────────────────────────────────────────┐
│                  BrownPanda Gateway (Hardware)                  │
│                     Layer 2 Safety                              │
├─────────────────────────────────────────────────────────────────┤
│                                                                  │
│  - Receives CAN from socketd                                    │
│  - Applies LOOSER safety limits                                 │
│  - Forwards to vehicle EPS/ACC                                  │
│  - Hardware-enforced safety net                                 │
│                                                                  │
└─────────────────────────────────────────────────────────────────┘
                            │
                            ▼
┌─────────────────────────────────────────────────────────────────┐
│                     Tesla Vehicle (EPS/ACC)                     │
└─────────────────────────────────────────────────────────────────┘
```

## Data Flow

### RX Path (Vehicle → openpilot)
```
Tesla CAN → socketd → 'can' topic → vehicled (safety RX processing) → VehicleState → 'carState' topic
```

### TX Path (openpilot → Vehicle)
```
'carControl' topic → vehicled (VehicleController) → vehicled (Safety Check) → 'sendcan' topic → socketd → BrownPanda → Tesla
```

## Key Files

| File | Purpose |
|------|---------|
| `vehicle.py` | Main daemon, coordinates all components |
| `state.py` | VehicleState - parses CAN, tracks vehicle state |
| `controller.py` | VehicleController - generates CAN commands |
| OpenDBC Tesla modules | Tesla CAN parser, packer, controller, and DBC |
| `tesla_parser.py` | Tesla CAN message parser (minimal) |
| `safety.py` | TeslaSafety - 1st layer safety implementation |
| `safety_manager.py` | SafetyManager - wraps safety for vehicled |
| `vehicle_model.py` | Vehicle dynamics model |
| `values.py` | Tesla constants and configuration |
| `events.py` | Tesla-specific event handling |

## Safety Limits (Layer 1 - TIGHTER)

| Parameter | Value | Panda Original | Ratio |
|-----------|-------|----------------|-------|
| MAX_STEERING_ANGLE | 270° | 360° | 75% |
| MAX_STEERING_RATE | 20°/s | 25°/s | 80% |
| MAX_ANGLE_ERROR | 24° | 30° | 80% |
| MAX_ACCEL | ~1.6 m/s² | 2.0 m/s² | 80% |
| MIN_ACCEL | ~-2.8 m/s² | -3.48 m/s² | 80% |
| HEARTBEAT_TIMEOUT | 200ms | 250ms | 80% |
| DRIVER_TORQUE | 2.0 Nm | 2.5 Nm | 80% |

## Removed Dependencies

- `opendbc_repo` - Car interfaces moved to vehicled
- `panda` - Safety moved to vehicled (software) + BrownPanda gateway (hardware)

## Process Configuration

```python
# system/manager/process_config.py
PythonProcess("vehicled", "selfdrive.vehicled.vehicle", ignition_on),
PythonProcess("socketd", "system.socketd.socketd", always_run),  # Plain bridge
```

## Migration Notes

### From opendbc/car
- Car interfaces moved to `vehicled/`
- Tesla-specific only (no multi-brand support)
- VehicleModel ported to `vehicled/vehicle_model.py`

### From panda/board/safety
- Safety logic moved to `vehicled/safety.py`
- Runs as software on RK3588 instead of STM32
- Tighter limits than original Panda

### From selfdrive/car/card.py
- Replaced by `vehicled/vehicle.py`
- Added Layer 1 safety checks
- Tesla-only (no generic interface)

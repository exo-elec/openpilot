# EOP vehicle and sensor ownership

EOP10 uses native SocketCAN/socketd and vehicled for the BrownPanda gateway.
The runtime no longer depends on the OpenDBC package or submodule. A minimal,
MIT-licensed Tesla-party protocol (packing, parsing, checksums, counters, state
interpretation and control limits) is retained under `vehicle/protocol`, with
provenance in its NOTICE.md. Multi-brand dispatch and car radar are excluded.
The car schema is owned by cereal; all prior wire IDs/ordinals are preserved.

NGP10 continues to use normal comma card/OpenDBC and portable radar2d/BSD logic.
EOP10 introduces UART forward radar3d and canonical surround radar4d: BLE 3D
tracks now, optional WiFi point clouds on supported hardware. Radar2d is the
planar compatibility view. Presence-only CAN flags never fabricate geometry.

## Historical migration (superseded): Directory Structure

```
system/socketd/vehicle/
├── __init__.py              # exports Car, main
├── car/                      # FROM: selfdrive/car/ + opendbc/car/tesla/
│   ├── card.py               # Car — main daemon class (run as a thread by socketd.py)
│   ├── carstate.py           # CarState class
│   ├── carcontroller.py      # CarController class
│   ├── cruise.py             # VCruiseHelper
│   ├── events.py             # VehicleEvents
│   └── vehicle_model.py      # Vehicle dynamics model
├── tesla/                    # EOP BrownPanda adapter over pinned OpenDBC DBCs
│   ├── values.py              # Tesla constants
│   └── tesla_parser.py        # OpenDBC CANParser boundary wrapper
├── safety/                   # Shim over system/socketd/safety/tesla_safety.py
│   ├── safety.py              # re-exports TeslaSafety, SafetyLimits, etc.
│   └── safety_manager.py      # SafetyManager wrapper
├── ARCHITECTURE.md           # Documentation
└── MIGRATION_SUMMARY.md      # This file
```

## Source Mapping

| Original Location | New Location | Description |
|-------------------|--------------|-------------|
| `selfdrive/vehicled/car/card.py` | `system/socketd/vehicle/car/card.py` | Main daemon class |
| `selfdrive/vehicled/car/events.py` | `system/socketd/vehicle/car/events.py` | Events |
| `selfdrive/vehicled/car/cruise.py` | `system/socketd/vehicle/car/cruise.py` | Cruise helper |
| `selfdrive/vehicled/car/carstate.py` | `system/socketd/vehicle/car/carstate.py` | Adapter boundary |
| `selfdrive/vehicled/car/carcontroller.py` | `system/socketd/vehicle/car/carcontroller.py` | Adapter boundary |
| `selfdrive/vehicled/car/vehicle_model.py` | `system/socketd/vehicle/car/vehicle_model.py` | Dynamics |
| `selfdrive/vehicled/tesla/tesla_parser.py` | `system/socketd/vehicle/tesla/tesla_parser.py` | CAN parser |
| `selfdrive/vehicled/tesla/values.py` | `system/socketd/vehicle/tesla/values.py` | Tesla constants |
| `selfdrive/vehicled/safety/safety.py` | `system/socketd/vehicle/safety/safety.py` | Now a re-export shim over `system/socketd/safety/tesla_safety.py` |
| `selfdrive/vehicled/safety/safety_manager.py` | `system/socketd/vehicle/safety/safety_manager.py` | Safety manager |
| `selfdrive/vehicled/vehicled.py` | *(deleted)* | Was a standalone process wrapper; no longer needed since `socketd` runs `Car` directly |

## Key Classes

| Class | Location | Purpose |
|-------|----------|---------|
| `Car` | `car/card.py` | Main daemon logic, run as a thread inside `socketd` |
| `CarState` | `car/carstate.py` | Parses CAN |
| `CarController` | `car/carcontroller.py` | Generates CAN |
| `TeslaSafety` | `system/socketd/safety/tesla_safety.py` | Layer 1 safety (canonical) |
| `SafetyManager` | `safety/safety_manager.py` | Safety wrapper |
| `VCruiseHelper` | `car/cruise.py` | Cruise speed |

## Architecture

```
┌─────────────────────────────────────────────────────────┐
│  system.socketd.socketd (single process)                 │
│  ├── SocketCAN bridge   - system/socketd/socketd.py       │
│  └── vehicle.Car thread - system/socketd/vehicle/         │
│      ├── car/      - Car interface                        │
│      ├── tesla/    - Tesla CAN parsing                     │
│      └── safety/   - Layer 1 safety (shim)                 │
├─────────────────────────────────────────────────────────┤
│  BrownPanda gateway (hardware; v1=TC275, v2=TC375)        │
│  - Layer 2 Safety (LOOSER limits)                          │
├─────────────────────────────────────────────────────────┤
│  Tesla Vehicle                                             │
└─────────────────────────────────────────────────────────┘
```

## Process Configuration

```python
# system/manager/process_config.py
PythonProcess("socketd", "system.socketd.socketd", always_run),
```

There is a single process, `socketd`. `SocketD.start()` (in
`system/socketd/socketd.py`) imports `Car` from `system.socketd.vehicle` and
runs its main loop (`Car.card_thread()`).

## Import Examples

```python
from openpilot.system.socketd.vehicle.car.card import Car
from openpilot.system.socketd.vehicle.car.carstate import CarState
from openpilot.system.socketd.vehicle.car.cruise import VCruiseHelper

# Safety
from openpilot.system.socketd.vehicle.safety.safety_manager import SafetyManager

# Tesla-specific
from openpilot.system.socketd.vehicle.tesla.values import VEHICLE, CarControllerParams
```

## OpenDBC De-duplication (2026-08-02)

`system/socketd/vehicle/tesla/values.py` used to keep its own hand-rolled
copy of `CANBUS` and the accel/jerk limits (`ACCEL_MIN`/`ACCEL_MAX`/
`JERK_LIMIT_MIN`/`JERK_LIMIT_MAX`) alongside the ones already defined in
`opendbc/car/tesla/values.py` (pinned submodule, shared commit with
`dev/NGP10`). Two independent copies of the same numbers is exactly what
caused a real bug earlier in this migration: a safety-layer copy of the accel
limits drifted out of sync with Tesla's offset-encoding semantics and ended
up rejecting 100% of longitudinal commands (see `task.md`, "Safety
reconciliation").

To close that gap:

- `CANBUS` and `CarControllerParams.ACCEL_MIN/ACCEL_MAX/JERK_LIMIT_MIN/JERK_LIMIT_MAX`
  are now sourced directly from `opendbc.car.tesla.values` instead of being
  redefined. `system/socketd/vehicle/tesla/values.py` stays the stable import
  path for the rest of the EOP tree (`card.py`, `longitudinal_planner.py`,
  `long_mpc.py`, sim tooling) — only where the numbers come from changed.
- `GEAR_MAP`, `TeslaSafetyFlags`, `TeslaFlags`, `STEER_THRESHOLD`, and
  `FW_QUERY_CONFIG` were removed from this file — they were dead code with no
  importers anywhere in the tree (`carstate.py`/`carcontroller.py` delegate
  entirely to `opendbc.car.tesla.CarState`/`CarController`, which carry their
  own equivalents internally).
- `VEHICLE`/`PlatformConfig` (generic Chinese-EV chassis presets that
  BrownPanda normalizes onto Tesla-format CAN) and the steering-specific
  fields of `CarControllerParams` (`MAX_STEER_ANGLE`, `MAX_ANGLE_RATE`,
  `MAX_LATERAL_ACCEL`/`JERK`, …) have no OpenDBC equivalent and stay
  EOP-owned.

# vehicled Migration Summary

## Directory Structure (Organized by Source)

```
selfdrive/vehicled/
├── vehicled.py              # Wrapper (imports from car/)
├── __init__.py
├── car/                     # FROM: selfdrive/car/ + opendbc/car/tesla/
│   ├── card.py              # Main daemon (was vehicle.py)
│   ├── carstate.py          # CarState class (was state.py)
│   ├── carcontroller.py     # CarController class (was controller.py)
│   ├── cruise.py            # VCruiseHelper
│   ├── events.py            # CarSpecificEvents
│   └── vehicle_model.py     # Vehicle dynamics model
├── tesla/                   # FROM: opendbc/can/ (minimal replacement)
│   ├── values.py            # Tesla constants
│   ├── teslacan.py          # CAN message creation
│   ├── tesla_packer.py      # Minimal CAN packer
│   └── tesla_parser.py      # Minimal CAN parser
├── safety/                  # FROM: panda/board/safety/ + socketd/safety/
│   ├── safety.py            # TeslaSafety (Layer 1)
│   └── safety_manager.py    # SafetyManager wrapper
├── ARCHITECTURE.md          # Documentation
└── MIGRATION_SUMMARY.md     # This file
```

## Source Mapping

| Original Location | New Location | Description |
|-------------------|--------------|-------------|
| `selfdrive/car/card.py` | `vehicled/car/card.py` | Main daemon |
| `selfdrive/car/car_specific.py` | `vehicled/car/events.py` | Events |
| `selfdrive/car/cruise.py` | `vehicled/car/cruise.py` | Cruise helper |
| `opendbc/car/tesla/carstate.py` | `vehicled/car/carstate.py` | CAN parsing |
| `opendbc/car/tesla/carcontroller.py` | `vehicled/car/carcontroller.py` | CAN generation |
| `opendbc/car/tesla/interface.py` | Merged into `card.py` | Params setup |
| `opendbc/car/vehicle_model.py` | `vehicled/car/vehicle_model.py` | Dynamics |
| `opendbc/can/packer.py` | `vehicled/tesla/tesla_packer.py` | CAN packer |
| `opendbc/can/parser.py` | `vehicled/tesla/tesla_parser.py` | CAN parser |
| `panda/board/safety/safety_tesla.h` | `vehicled/safety/safety.py` | Safety logic |
| `system/socketd/safety/*.py` | `vehicled/safety/*.py` | Safety manager |

## Key Classes

| Class | Location | Purpose |
|-------|----------|---------|
| `Car` | `car/card.py` | Main daemon |
| `CarState` | `car/carstate.py` | Parses CAN |
| `CarController` | `car/carcontroller.py` | Generates CAN |
| `TeslaSafety` | `safety/safety.py` | Layer 1 safety |
| `SafetyManager` | `safety/safety_manager.py` | Safety wrapper |
| `VCruiseHelper` | `car/cruise.py` | Cruise speed |

## Architecture

```
┌─────────────────────────────────────────────────────────┐
│  vehicled (selfdrive level)                             │
│  ├── car/      - Car interface (from opendbc/car)       │
│  ├── tesla/    - Tesla CAN (from opendbc/can)           │
│  └── safety/   - Layer 1 safety (from panda)            │
├─────────────────────────────────────────────────────────┤
│  socketd (system level)                                 │
│  - Plain CAN bridge (NO SAFETY)                         │
├─────────────────────────────────────────────────────────┤
│  TC275 (hardware)                                       │
│  - Layer 2 Safety (LOOSER limits)                       │
├─────────────────────────────────────────────────────────┤
│  Tesla Vehicle                                          │
└─────────────────────────────────────────────────────────┘
```

## Process Configuration

```python
# system/manager/process_config.py
PythonProcess("vehicled", "selfdrive.vehicled.vehicled", ignition_on),
```

The process name is "vehicled" but the main implementation is in `car/card.py`.

## Import Examples

```python
# Old style (still works)
from openpilot.selfdrive.vehicled.car.card import Car
from openpilot.selfdrive.vehicled.car.carstate import CarState
from openpilot.selfdrive.vehicled.car.cruise import VCruiseHelper

# New style (wrapper)
from openpilot.selfdrive.vehicled.vehicled import Vehicle

# Safety
from openpilot.selfdrive.vehicled.safety.safety_manager import SafetyManager

# Tesla-specific
from openpilot.selfdrive.vehicled.tesla.values import CAR
```
